"""A whole run, offline, on real files (task 1.10): a broken source does not stop the run, a second
run reports no change, a planted revision is found, catch-up retries only failures, and the
manual inbox replaces a download."""

import datetime as dt
import json
import shutil

import pytest
import requests
from conftest import FIX, NAG_SEED, FakeSession, siat_route

from nowcast.ingest import alerts
from nowcast.ingest.run import run
from nowcast.store.vintages import Store

IDS = [
    "ind_total_real",
    "ind_mining_real",
    "ppi_mm",
    "mkt_services_growth",
    "rate_loan_firms",
    "remittances_q",
    "gdp_nag",
    "usd_rate",
    "gold_price",
]
RUN_DATE = dt.date(2026, 10, 16)


class Sources:
    """Routes every URL of the test catalogue to a fixture; ``broken`` hosts raise."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "src"
        shutil.copytree(FIX, self.dir)
        self.broken = {"www.worldbank.org"}
        self.siat = siat_route(
            {
                562: self.dir / "siat/562.json",
                1581: self.dir / "siat/1581.json",
                3218: self.dir / "siat/3218.json",
            }
        )

    def __call__(self, url):
        host = url.split("/")[2]
        if host in self.broken:
            return requests.ConnectionError(f"cannot reach {host}")
        if "siat" in host:
            return self.siat(url)
        if "arkhiv-kursov-valyut" in url:
            return (self.dir / "cbu/usd_2026-08-31.json").read_bytes()
        if url.endswith("INR_Uzbekistan_Online_STA.xml"):
            return (self.dir / "cbu/INR_Uzbekistan_Online_STA.xml").read_bytes()
        if url.endswith("BOP_Analytical_Uzbekistan.xlsx"):
            return (self.dir / "cbu/BOP_Analytical_Uzbekistan.xlsx").read_bytes()
        if url.endswith("nag_uzbekistan_mcd.xlsx"):
            return NAG_SEED.read_bytes()
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def setup(tmp_path, catalogue):
    src = Sources(tmp_path)
    cat = [catalogue[i] for i in IDS]
    store_dir = tmp_path / "store"

    def go(**kw):
        return run(store_dir, RUN_DATE, catalogue=cat, session=FakeSession(src), **kw)

    return src, Store(store_dir), go


def test_broken_source_does_not_stop_the_run(setup):
    src, store, go = setup
    rep = go()
    st = {sid: r["status"] for sid, r in rep["series"].items()}
    assert st["gold_price"] == "fail" and "ConnectionError" in rep["series"]["gold_price"]["error"]
    assert all(st[i] != "fail" for i in IDS if i != "gold_price"), st
    assert rep["counts"]["fail"] == 1 and rep["sources"]["worldbank:pinksheet"]["failed"] == ["gold_price"]
    # the seed vintage is there, and the store kept gold from the seed although the download failed
    assert "seed-2026-10-14" in rep["seeded"]
    latest = store.latest()
    assert len(latest[latest.series_id == "gold_price"]) > 80
    assert (store.root / "runs" / rep["run_id"] / "changes.json").exists()
    assert (
        "gold_price"
        not in json.loads((store.root / "runs" / rep["run_id"] / "changes.json").read_text())["series"]
    )
    assert (store.root / "latest.csv").exists() and (store.inbox_dir / "README.md").exists()
    raw = store.root / rep["series"]["ind_total_real"]["raw"]
    assert raw.exists() and raw.read_bytes() == (src.dir / "siat/562.json").read_bytes()


def test_download_once(tmp_path, catalogue):
    src = Sources(tmp_path)
    sess = FakeSession(src)
    run(
        tmp_path / "store",
        RUN_DATE,
        catalogue=[catalogue["ind_total_real"], catalogue["ind_mining_real"]],
        session=sess,
    )
    assert sum(c.endswith("sdmx_data_562.json") for c in sess.calls) == 1


def test_second_run_reports_no_change_then_finds_planted_revision(setup):
    src, store, go = setup
    go()
    second = go()
    assert second["run_id"] == "2026-10-16.2"
    for sid in (
        "ind_total_real",
        "ppi_mm",
        "mkt_services_growth",
        "rate_loan_firms",
        "remittances_q",
        "gdp_nag",
    ):
        assert second["series"][sid]["changes"] == "no change", sid
    assert second["new_points"] == second["revised_points"] == 0

    # plant a revision in the real SIAT file: August industry 108.0 -> 108.3
    f = src.dir / "siat/562.json"
    payload = json.loads(f.read_bytes())
    row = next(r for r in payload[0]["data"] if r["Code"] == "B, C, D, E")
    assert row["2026-M08"] == 108.0
    row["2026-M08"] = 108.3
    f.write_text(json.dumps(payload))
    third = go()
    ch = json.loads((store.root / "runs" / third["run_id"] / "changes.json").read_text())["series"]
    assert ch["ind_total_real"]["revised"] == [
        {"item": "B, C, D, E", "ref_period": "2026-08", "old": 108.0, "new": 108.3}
    ]
    assert ch["ind_mining_real"]["summary"] == "no change"
    assert store.latest(["ind_total_real"]).set_index("ref_period").loc["2026-08", "value"] == 108.3
    assert store.as_of("2026-10-15", ["ind_total_real"]).empty  # not in the seed, first fetched on the 16th


def test_failed_parse_keeps_previous_values(setup):
    src, store, go = setup
    go()
    before = store.latest(["mkt_services_growth"])
    (src.dir / "siat/3218.json").write_text('[{"metadata": [], "data": []}]')
    rep = go()
    assert rep["series"]["mkt_services_growth"]["status"] == "fail"
    after = store.latest(["mkt_services_growth"])
    assert after[["item", "ref_period", "value"]].equals(before[["item", "ref_period", "value"]])


def test_catch_up_retries_only_failures(setup):
    src, store, go = setup
    go()
    rep = go(catch_up=True)
    assert rep["mode"] == "catch-up" and list(rep["series"]) == ["gold_price"]
    src.broken.clear()
    src.broken.add("cbu.uz")
    rep2 = go(catch_up=True)  # gold failed again in the catch-up, so it is retried again
    assert list(rep2["series"]) == ["gold_price"]


def test_catch_up_with_nothing_failed_does_nothing(setup, catalogue, tmp_path):
    src, store, _ = setup
    cat = [catalogue["ind_total_real"]]
    run(store.root, RUN_DATE, catalogue=cat, session=FakeSession(src))
    rep = run(store.root, RUN_DATE, catalogue=cat, session=FakeSession(src), catch_up=True)
    assert rep["skipped"] is True


def test_manual_inbox_replaces_download(setup):
    src, store, go = setup
    src.broken.add("cbu.uz")  # BoP download broken...
    box = store.inbox_dir / "remittances_q"
    box.mkdir(parents=True)
    shutil.copy(src.dir / "cbu/BOP_Analytical_Uzbekistan.xlsx", box / "bop_by_hand.xlsx")  # ...file by hand
    rep = go()
    r = rep["series"]["remittances_q"]
    assert r["status"] == "ok" and r["via_inbox"] == "bop_by_hand.xlsx"
    assert not (box / "bop_by_hand.xlsx").exists()
    assert (store.inbox_dir / "_used" / rep["run_id"] / "remittances_q" / "bop_by_hand.xlsx").exists()
    assert rep["series"]["rate_loan_firms"]["status"] == "fail"  # same host, no inbox file


def test_alert_text_and_issues(setup):
    _, store, go = setup
    rep = go()
    text = alerts.telegram_text(rep)
    assert "fail 1" in text and "gold_price" in text and len(text) <= alerts.TG_LIMIT

    class FakeIssues:
        def __init__(self, open_):
            self.open_, self.log = open_, []

        def open_issues(self):
            return dict(self.open_)

        def create(self, title, body):
            self.log.append(("create", title))

        def comment(self, n, body):
            self.log.append(("comment", n))

        def close(self, n):
            self.log.append(("close", n))

    fi = FakeIssues({})
    alerts.sync_issues(rep, fi)
    assert fi.log == [("create", "Data source failing: worldbank:pinksheet")]
    fi = FakeIssues({"Data source failing: worldbank:pinksheet": 7, "Data source failing: siat:562": 3})
    alerts.sync_issues(rep, fi)
    assert ("comment", 7) in fi.log and ("close", 3) in fi.log and ("close", 7) not in fi.log


def test_accept_overrides_a_failed_check_only(setup):
    src, store, go = setup
    go()
    meta = store.load_meta("ppi_mm")
    store.save_meta("ppi_mm", {**meta, "unit": "Index points"})  # pretend the unit used to differ
    assert go()["series"]["ppi_mm"]["status"] == "fail"
    rep = go(accept=["ppi_mm", "gold_price"])
    assert rep["series"]["ppi_mm"]["status"] == "warn" and rep["series"]["ppi_mm"]["accepted"]
    assert store.load_meta("ppi_mm")["unit"] == "Percent"  # new unit is now the reference
    assert rep["series"]["gold_price"]["status"] == "fail"  # a download error cannot be accepted
