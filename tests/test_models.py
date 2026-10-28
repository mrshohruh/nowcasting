"""DFM output conversion, the Data Monitor and the Downloads page (review fixes, Oct 2026)."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nowcast.models.dfm import DFM, dlog_path_to_yoy, quarterly_gdp_dlog
from nowcast.transform.panels import Panels

ROOT = Path(__file__).resolve().parents[1]


def _load_produce():
    spec = importlib.util.spec_from_file_location("produce", ROOT / "scripts" / "produce.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gdp_panels(last: str = "2026Q2") -> Panels:
    """SA GDP levels growing 2% a quarter from 2024Q1 to *last*, and matching y/y rows."""
    idx = pd.period_range("2024Q1", last, freq="Q")
    level = pd.Series(100 * np.exp(0.02 * np.arange(len(idx))), index=idx)
    yoy = pd.DataFrame({
        "variable": "gdp", "freq": "Q", "period": [str(p) for p in idx[4:]],
        "value": (level.iloc[4:].to_numpy() / level.iloc[:-4].to_numpy() - 1) * 100,
    })
    return Panels(as_of=None, yoy=yoy, sa=pd.DataFrame(), meta=pd.DataFrame(),
                  flags=pd.DataFrame(), levels={"gdp": {"sa_level": level}}, modes={})


# ── y/y from the quarterly growth path ────────────────────────────────────────


def test_each_quarter_uses_its_own_growth():
    p = _gdp_panels()
    sa = p.levels["gdp"]["sa_level"]
    dlog = pd.Series({pd.Period("2026Q3"): 3.0, pd.Period("2026Q4"): 1.0})
    got = dlog_path_to_yoy(dlog, p, pd.Period("2026Q4"))
    want = (sa[pd.Period("2026Q2")] * np.exp(0.04) / sa[pd.Period("2025Q4")] - 1) * 100
    assert got == pytest.approx(want)
    # The old code applied Q4's growth twice: a different (wrong) number
    old = (sa[pd.Period("2026Q2")] * np.exp(0.02) / sa[pd.Period("2025Q4")] - 1) * 100
    assert abs(got - old) > 1.0


def test_one_quarter_ahead():
    p = _gdp_panels()
    sa = p.levels["gdp"]["sa_level"]
    dlog = pd.Series({pd.Period("2026Q3"): 1.5})
    got = dlog_path_to_yoy(dlog, p, pd.Period("2026Q3"))
    assert got == pytest.approx((sa.iloc[-1] * np.exp(0.015) / sa[pd.Period("2025Q3")] - 1) * 100)


def test_published_quarter_uses_the_official_level():
    p = _gdp_panels()
    got = dlog_path_to_yoy(pd.Series({pd.Period("2026Q2"): 99.0}), p, pd.Period("2026Q2"))
    assert got == pytest.approx((np.exp(0.08) - 1) * 100)


def test_gap_in_the_path_falls_back_instead_of_guessing():
    p = _gdp_panels()
    # 2026Q3 missing: the level cannot be chained, so the AR fallback answers (finite, not NaN)
    got = dlog_path_to_yoy(pd.Series({pd.Period("2026Q4"): 1.0}), p, pd.Period("2026Q4"))
    assert np.isfinite(got)
    assert got != pytest.approx((np.exp(0.04) - 1) * 100)


# ── smoothed estimates ──────────────────────────────────────────────────────


class _FakePrediction:
    def __init__(self, frame):
        self.predicted_mean = frame


class _FakeResult:
    def __init__(self):
        self.asked: list[str] = []
        idx = pd.period_range("2026-01", "2026-12", freq="M")
        self.frame = pd.DataFrame({"gdp": np.arange(12, dtype=float) / 10}, index=idx)

    def get_prediction(self, information_set="predicted"):
        self.asked.append(information_set)
        return _FakePrediction(self.frame)


def test_quarterly_dlog_reads_smoothed_values_at_quarter_end():
    res = _FakeResult()
    dlog = quarterly_gdp_dlog(res, {"gdp": {"mu": 1.5, "sd": 2.0}})
    assert res.asked == ["smoothed"]
    assert list(dlog.index) == list(pd.period_range("2026Q1", "2026Q4", freq="Q"))
    # March is row 2 (0.2), June 0.5, September 0.8, December 1.1; unscaled: z * sd + mu
    assert dlog.to_numpy() == pytest.approx([1.9, 2.5, 3.1, 3.7])


# ── real-data check: the published number is the chained smoothed path ──────


@pytest.fixture(scope="module")
def snapshot_panels(tmp_path_factory):
    from nowcast.store.vintages import Store
    from nowcast.transform.inputs import load_inputs
    from nowcast.transform.panels import build_panels

    snap = ROOT / "tests" / "fixtures" / "snapshot" / "latest_2026-10-16.csv.gz"
    st = Store(tmp_path_factory.mktemp("store"))
    df = pd.read_csv(snap, dtype={"item": str, "ref_period": str})
    df["source_hash"] = "fixture"
    df["fetched_at"] = pd.Timestamp("2026-10-16T06:00:00Z")
    df["run_id"] = df["vintage_date"]
    df["removed"] = False
    for vd, part in df.groupby("vintage_date"):
        st.write_observations(f"fixture-{vd}", part)
    return build_panels(inputs=load_inputs(st, "2026-10-16"))


def test_dfm_nowcast_chains_the_smoothed_path(snapshot_panels):
    ids = ["ind_manuf", "constr", "retail", "wholesale", "cpi", "imports", "gdp"]
    m = DFM(variable_ids=ids, em_max_iter=20)
    out = m.predict(snapshot_panels, pd.Period("2026Q4"))
    assert np.isfinite(out["gdp_yoy"]), out

    from nowcast.models.dfm import _build_endog
    _, _, meta = _build_endog(snapshot_panels, ids, pd.Period("2026Q4"))
    dlog = quarterly_gdp_dlog(m._last_result, meta)
    assert out["info"]["gdp_dlog"] == pytest.approx(dlog[pd.Period("2026Q4")])
    want = dlog_path_to_yoy(dlog, snapshot_panels, pd.Period("2026Q4"))
    assert out["gdp_yoy"] == pytest.approx(want)  # inside the winsorising bounds


# ── Data Monitor ────────────────────────────────────────────────────────────


def _monitor_panels() -> Panels:
    months = [str(p) for p in pd.period_range("2026-01", "2026-08", freq="M")]
    yoy = pd.concat([
        pd.DataFrame({"variable": "retail", "freq": "M", "period": months, "value": 10.0}),
        pd.DataFrame({"variable": "gdp", "freq": "Q", "period": ["2026Q1", "2026Q2"], "value": 8.5}),
    ], ignore_index=True)
    meta = pd.DataFrame({"variable": ["retail", "gdp", "cpi"], "freq": ["M", "Q", "M"],
                         "yoy_first": ["2014-01", "2013Q1", "2011-01"]})
    return Panels(as_of=None, yoy=yoy, sa=pd.DataFrame(), meta=meta,
                  flags=pd.DataFrame(), levels={}, modes={})


def test_monitor_lists_only_model_inputs_with_sources_and_lags():
    produce = _load_produce()
    rows = produce.build_monitor_data(_monitor_panels(), dt.date(2026, 10, 22),
                                      variable_ids=["retail", "gdp"])
    assert [r["variable"] for r in rows] == ["retail", "gdp"]  # cpi is not a model input here
    retail, gdp = rows
    assert retail["source"] == "Uzstat" and retail["series_id"] == "retail_real"
    assert retail["expected_lag"] == 2 and retail["first_period"] == "2014-01"
    assert retail["periods"]["2026-08"] == "available"
    assert retail["periods"]["2026-09"] == "not_due"  # September is used from 1 November
    assert retail["next_due"] == "2026-11-01"
    # Quarterly rows get quarterly periods, not an empty "not yet due" row
    assert gdp["series_id"] == "gdp_nag"
    assert gdp["periods"] == {"2026Q1": "available", "2026Q2": "available",
                              "2026Q3": "not_due", "2026Q4": "not_due"}


def test_monitor_flags_a_due_period_that_is_missing():
    produce = _load_produce()
    rows = produce.build_monitor_data(_monitor_panels(), dt.date(2026, 12, 2),
                                      variable_ids=["retail"])
    assert rows[0]["periods"]["2026-09"] == "missing"  # due on 1 November, not in the panel


# ── Downloads ──────────────────────────────────────────────────────────────


def test_downloads_are_copied_and_linked(tmp_path):
    from nowcast.report.site import SiteContext, build_site

    produce = _load_produce()
    ctx = SiteContext(run_date="2026-10-22", target_quarter="2026Q4", horizon="nowcast_m1",
                      new_nowcast=8.0, band_lo=6.0, band_hi=10.0)
    ctx.nowcast_paths = {"2026Q4": [{"run_date": "2026-10-22", "gdp_yoy": 8.0,
                                     "horizon": "nowcast_m1"}]}
    ctx.download_files = produce.generate_downloads(ctx, None, [], tmp_path / "staging")
    out = build_site(ctx, tmp_path / "site")

    assert (out / "downloads" / "nowcast_path_2026Q4.csv").is_file()
    assert (out / "downloads" / "nowcast_2026Q4.xlsx").is_file()
    html = (out / "en" / "downloads.html").read_text(encoding="utf-8")
    assert 'href="../downloads/nowcast_path_2026Q4.csv"' in html
    assert "Nowcast path by run date" in html
    assert 'href="../pdf/note_en.pdf"' in html
    assert 'href="../downloads/"' not in html  # no empty links
