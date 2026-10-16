"""Each parser on a real file saved from its source (task 1.10)."""

import datetime as dt
import json

import pytest
from conftest import FIX, NAG_SEED, WB_DIR, FakeSession, response, siat_route

from nowcast.ingest.adapters import PINK_LINK
from nowcast.ingest.base import FetchContext, Raw, SourceError, adapter_for, norm_period
from nowcast.transform.target import build_target, read_nag

CTX = FetchContext(run_date=dt.date(2026, 10, 16))


def parse(series, path, ext):
    return adapter_for(series, CTX).parse(Raw(path.read_bytes(), ext, str(path)))


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2026-M08", "2026-08"),
        ("2010-М01", "2010-01"),
        ("1960M01", "1960-01"),
        ("2026-08", "2026-08"),
        ("2026-Q2", "2026Q2"),
        ("2018Q1", "2018Q1"),
        (dt.datetime(2019, 1, 1), "2019-01"),
    ],
)
def test_norm_period(label, expected):
    assert norm_period(label) == expected


@pytest.mark.parametrize("bad", ["2026-M13", "2026-Q5", "Jan 2026", ""])
def test_norm_period_rejects(bad):
    with pytest.raises(ValueError):
        norm_period(bad)


# ---------------------------------------------------------------------------------- SIAT
def test_siat_selects_by_code(catalogue):
    p = parse(catalogue["ind_total_real"], FIX / "siat/562.json", "json")
    d = p.data.set_index("ref_period")["value"]
    assert set(p.data["item"]) == {"B, C, D, E"}
    assert (d.index.min(), d.index.max(), len(d)) == ("2019-01", "2026-08", 92)
    assert d["2026-08"] == 108.0 and d["2019-01"] == 105.0
    assert p.meta["unit"] == "Percent" and p.meta["labels"] == {"B, C, D, E": "Industrial production"}


def test_siat_survives_label_change(catalogue, tmp_path):
    """Rows are picked by code: a renamed label changes nothing but the reported label."""
    payload = json.loads((FIX / "siat/562.json").read_bytes())
    for r in payload[0]["data"]:
        if r["Code"] == "B, C, D, E":
            r["Klassifikator_en"] = "Industry (renamed)"
    f = tmp_path / "562.json"
    f.write_text(json.dumps(payload))
    p = parse(catalogue["ind_total_real"], f, "json")
    assert len(p.data) == 92 and p.meta["labels"]["B, C, D, E"] == "Industry (renamed)"


def test_siat_missing_code_is_an_error(catalogue):
    s = {**catalogue["ind_total_real"], "codes": ["ZZZ"]}
    with pytest.raises(SourceError, match="ZZZ"):
        parse(s, FIX / "siat/562.json", "json")


def test_siat_cyrillic_and_latin_period_labels(catalogue):
    """PPI table 1581 mixes '2016-М01' (Cyrillic) and '2026-M09' (Latin)."""
    d = parse(catalogue["ppi_mm"], FIX / "siat/1581.json", "json").data.set_index("ref_period")["value"]
    assert d.index.is_unique and d.index.min() == "2013-01" and d.index.max() == "2026-09"
    assert d["2016-01"] == 101.1 and d["2026-09"] == 100.6


def test_siat_all_items(catalogue):
    p = parse(catalogue["mkt_services_growth"], FIX / "siat/3218.json", "json")
    assert p.data["item"].nunique() == 14 and {"G", "J", "493-494", "100"} <= set(p.data["item"])
    assert p.data.groupby("item").size().eq(32).all()


def test_siat_fetch_follows_pointer(catalogue):
    s = FakeSession(siat_route({562: FIX / "siat/562.json"}))
    a = adapter_for(catalogue["ind_total_real"], FetchContext(run_date=CTX.run_date, session=s))
    raw = a.fetch()
    assert raw.url.endswith("sdmx_data_562.json") and raw.info["updated_at"].startswith("2026-09-29")
    assert "download_format=json" in s.calls[0] and a.file_key == "siat:562"


# ------------------------------------------------------------------------------ CBU SDMX
@pytest.mark.parametrize(
    "sid,n,first,last",
    [
        ("rate_loan_firms", 92, "2019-01", "2026-08"),
        ("policy_rate", 164, "2013-01", "2026-08"),
    ],
)
def test_sdmx(catalogue, sid, n, first, last):
    p = parse(catalogue[sid], FIX / "cbu/INR_Uzbekistan_Online_STA.xml", "xml")
    assert (len(p.data), p.data["ref_period"].min(), p.data["ref_period"].max()) == (n, first, last)
    assert p.meta["unit"] == "UNIT_MULT=0"


def test_sdmx_unit_mult_and_missing_key(catalogue):
    assert (
        parse(catalogue["reserves_fx"], FIX / "cbu/IR_Uzbekistan_MCD_STA.xml", "xml").meta["unit"]
        == "UNIT_MULT=6"
    )
    with pytest.raises(SourceError, match="NOPE"):
        parse({**catalogue["reserves_fx"], "key": "NOPE"}, FIX / "cbu/IR_Uzbekistan_MCD_STA.xml", "xml")


# --------------------------------------------------------------------------- CBU BoP xlsx
def test_bop_remittances(catalogue):
    p = parse(catalogue["remittances_q"], FIX / "cbu/BOP_Analytical_Uzbekistan.xlsx", "xlsx")
    d = p.data.set_index("ref_period")["value"]
    assert p.meta["unit"] == "million USD" and d.index.min() == "2005Q1" and d.index.max() == "2026Q2"
    assert d["2026Q2"] == pytest.approx(4000.1144686, rel=1e-9)


def test_bop_missing_row(catalogue):
    with pytest.raises(SourceError, match="not found"):
        parse(
            {**catalogue["remittances_q"], "key": "Tertiary income"},
            FIX / "cbu/BOP_Analytical_Uzbekistan.xlsx",
            "xlsx",
        )


# --------------------------------------------------------------------------------- NAG
def test_nag_items_match_target(catalogue):
    p = parse(catalogue["gdp_nag"], NAG_SEED, "xlsx")
    assert p.data["item"].nunique() == 17 * 3
    g = p.data.set_index(["item", "ref_period"])["value"]
    t = build_target(read_nag(NAG_SEED)).set_index(["item", "quarter"])
    assert g[("gdp.G", "2026Q2")] == t.loc[("gdp", "2026Q2"), "growth_cum_published"]
    assert g[("gdp.N", "2018Q1")] == t.loc[("gdp", "2018Q1"), "nominal_cum"]


def test_nag_subset(catalogue):
    p = parse(catalogue["gva_services_q"], NAG_SEED, "xlsx")
    assert {i.split(".")[0] for i in p.data["item"]} == {
        "trade_accommodation",
        "transport_ict",
        "other_services",
    }


def test_nag_layout_change_is_a_source_error(catalogue):
    with pytest.raises(SourceError):
        parse(catalogue["gdp_nag"], FIX / "cbu/BOP_Analytical_Uzbekistan.xlsx", "xlsx")


# ---------------------------------------------------------------------------- World Bank
needs_wb = pytest.mark.skipif(
    not (WB_DIR / "CMO-Historical-Data-Monthly.xlsx").exists(),
    reason="World Bank fixture not captured yet (fixtures workflow)",
)


@needs_wb
def test_worldbank_gold(catalogue):
    p = parse(catalogue["gold_price"], WB_DIR / "CMO-Historical-Data-Monthly.xlsx", "xlsx")
    d = p.data.set_index("ref_period")["value"]
    assert d.index.min() == "1960-01" and d.index.max() >= "2026-08"
    assert "troy oz" in (p.meta["unit"] or "")
    # the seed workbook took its gold column from the same Pink Sheet
    from nowcast.store.seed import workbook_series

    seed = workbook_series()["gold_price"].set_index("ref_period")["value"]
    common = seed.index.intersection(d.index)
    assert len(common) >= 80
    assert (seed[common] - d[common]).abs().max() < 0.01


@needs_wb
def test_worldbank_page_link_and_fetch(catalogue):
    page = (WB_DIR / "commodity-markets.html").read_bytes()
    xlsx = (WB_DIR / "CMO-Historical-Data-Monthly.xlsx").read_bytes()
    assert PINK_LINK.search(page.decode("utf-8", "replace"))

    def route(url):
        return page if "commodity-markets" in url else response(url, xlsx)

    a = adapter_for(catalogue["gold_price"], FetchContext(run_date=CTX.run_date, session=FakeSession(route)))
    raw = a.fetch()
    assert raw.url.endswith("CMO-Historical-Data-Monthly.xlsx") and raw.content == xlsx
