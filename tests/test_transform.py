"""Phase 2 tests (task 2.9), on a snapshot of the real store (tests/fixtures/store/).

The snapshot is the store's latest values on 16 Oct 2026, plus the three weight series added in
Phase 2 (industry sub-sector output, investment in current prices, market-service volumes),
downloaded the same day.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from conftest import FIX

from nowcast.catalogue import load as load_catalogue
from nowcast.store.vintages import Store
from nowcast.transform import growth as gr
from nowcast.transform.decumulate import (
    cumulate,
    cumulative_growth,
    decumulate,
    decumulate_average,
    discrete_growth,
)
from nowcast.transform.inputs import load_inputs, released_on
from nowcast.transform.levels import chain_mm, levels_from_yoy, to_quarterly_mean, yoy_index
from nowcast.transform.panels import build_panels, load_spec
from nowcast.transform.seasonal import stl_adjust

SNAPSHOT = FIX / "store" / "latest_2026-10-16.csv.gz"
CAT = {s["id"]: s for s in load_catalogue()}


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Store:
    st = Store(tmp_path_factory.mktemp("store"))
    df = pd.read_csv(SNAPSHOT, dtype={"item": str, "ref_period": str})
    df["source_hash"] = "fixture"
    df["fetched_at"] = pd.Timestamp("2026-10-16T06:00:00Z")
    df["run_id"] = df["vintage_date"]
    df["removed"] = False
    for vd, part in df.groupby("vintage_date"):
        st.write_observations(f"fixture-{vd}", part)
    return st


@pytest.fixture(scope="module")
def inputs(store):
    return load_inputs(store, "2026-10-16")


@pytest.fixture(scope="module")
def panels(inputs):
    return build_panels(inputs=inputs)


def cumulative_series(inputs, form="cumulative_ytd"):
    for sid, s in CAT.items():
        if s["form"] != form:
            continue
        for item in sorted(set(inputs.data.loc[inputs.data["series_id"] == sid, "item"])):
            yield sid, item, inputs.raw(sid, item)


# ------------------------------------------------------------------ 2.1 decumulation
def test_decumulated_months_add_back_to_published_cumulative_values(inputs):
    n = 0
    for sid, item, cum in cumulative_series(inputs):
        back = cumulate(decumulate(cum))
        ok = cum.notna()
        np.testing.assert_allclose(back[ok], cum[ok], rtol=0, atol=1e-6 * cum.abs().max(),
                                   err_msg=f"{sid}/{item}")
        n += 1
    assert n >= 40  # every cumulative series and item (incl. 17 NAG items x 3 bases)


def test_december_totals_match_annual_totals(inputs):
    """Sum of the discrete months (quarters) of a full year = the December (Q4) cumulative value."""
    checked = 0
    for sid, item, cum in cumulative_series(inputs):
        disc = decumulate(cum)
        per_year = 12 if cum.index.freqstr.startswith("M") else 4
        for y in sorted(set(cum.index.year)):
            d = disc[disc.index.year == y]
            if len(d) == per_year and d.notna().all():
                last = cum[cum.index.year == y].iloc[-1]
                assert d.sum() == pytest.approx(last, rel=1e-12), f"{sid}/{item} {y}"
                checked += 1
    assert checked > 150


def test_a_gap_leaves_the_next_month_missing():
    idx = pd.period_range("2024-01", "2024-06", freq="M")
    cum = pd.Series([10, 20, np.nan, 40, 50, 60.0], index=idx)
    d = decumulate(cum)
    assert d["2024-01"] == 10 and d["2024-02"] == 10
    assert np.isnan(d["2024-03"]) and np.isnan(d["2024-04"])
    assert d["2024-05"] == 10


def test_average_wages_decumulate_and_average_back(inputs):
    w = inputs.raw("wages_q", "1700")
    disc = decumulate_average(w)
    for y in range(2018, 2026):
        d = disc[disc.index.year == y]
        assert d.mean() == pytest.approx(w[pd.Period(f"{y}Q4", freq="Q")], rel=1e-12)


# ------------------------------------------------------------------ cumulative real growth -> discrete
@pytest.mark.parametrize("sid,item,wsid,witem", [
    ("ind_total_real", "B, C, D, E", "ind_total_nom", "B, C, D, E"),
    ("ind_manuf_real", "C", "ind_total_nom", "C"),
    ("constr_real", "1700", "constr_nom", "1700"),
    ("retail_real", "1700", "retail_nom", "1700"),
    ("wholesale_real", "1700", "wholesale_nom", "1700"),
    ("invest_growth_q", "1700", "invest_nom_q", "1700"),
    ("mkt_services_growth", "G", "mkt_services_nom", "G"),
])
def test_discrete_growth_recumulates_to_uzstat_cumulative_growth(inputs, sid, item, wsid, witem):
    G = inputs.raw(sid, item)
    dg = discrete_growth(G, [("own", inputs.raw(wsid, witem))])
    back = cumulative_growth(dg["g"], dg["weight"])
    ok = back.notna()
    assert ok.mean() > 0.9
    np.testing.assert_allclose(back[ok], G[ok], atol=1e-9)


def test_gdp_formula_is_the_same_as_target(inputs, panels):
    """discrete_growth on NAG published growth + nominal weights = target.py's discrete growth."""
    G = inputs.raw("gdp_nag", "gdp.G")
    N = inputs.raw("gdp_nag", "gdp.N")
    dg = discrete_growth(G[G.index.year >= 2019], [("own", N)])
    t = panels.yoy[(panels.yoy["variable"] == "gdp")].set_index("period")["value"]
    ours = dg["g"] - 100
    common = [p for p in ours.index if str(p) in t.index]
    np.testing.assert_allclose(ours[common].to_numpy(), t[[str(p) for p in common]].to_numpy(), atol=2e-3)


def test_non_positive_weight_gives_no_growth():
    idx = pd.period_range("2023-01", "2024-03", freq="M")
    nominal = pd.Series([10, 20, 15, 40, 50, 60, 70, 80, 90, 100, 110, 120, 12, 24, 36.0], index=idx)
    G = pd.Series(105.0, index=pd.period_range("2024-01", "2024-03", freq="M"))
    dg = discrete_growth(G, [("own", nominal)])
    assert dg["g"].iloc[0] == 105.0
    assert np.isnan(dg["g"].iloc[2])  # March 2023 weight is 15 - 20 < 0
    assert set(dg["basis"]) == {"prev_year"}


def test_weight_fallbacks_are_recorded():
    G = pd.Series([104.0, 106.0], index=pd.period_range("2024Q1", "2024Q2", freq="Q"))
    dg = discrete_growth(G, [("own", None)])
    assert list(dg["basis"]) == ["equal", "equal"]
    assert dg["g"].iloc[1] == pytest.approx(2 * 106 - 104)


# ------------------------------------------------------------------ 2.2 prices, 2.3 deflation
def test_cpi_spliced_and_2021_equals_100(inputs):
    cpi = chain_mm(inputs.raw("cpi_mm_old", "1"), inputs.raw("cpi_mm", "1"))
    assert cpi.index[0] == pd.Period("2010-01", freq="M")
    assert cpi[cpi.index.year == 2021].mean() == pytest.approx(100.0)
    mm = 100 * cpi / cpi.shift(1)
    assert mm[pd.Period("2021-01", freq="M")] == pytest.approx(inputs.raw("cpi_mm", "1")["2021-01"])
    assert mm[pd.Period("2020-12", freq="M")] == pytest.approx(inputs.raw("cpi_mm_old", "1")["2020-12"])


@pytest.mark.parametrize("period,official", [("2025Q4", 9.3), ("2026Q2", 10.9)])
def test_cpi_deflated_wages_within_1pp_of_uzstat_real_wage_growth(inputs, period, official):
    """Uzstat: real wages +9.3% in 2025 (kun.uz, 31 Mar 2026), +10.9% in Jan-Jun 2026 (uzdaily, 2 Oct)."""
    cpi = chain_mm(inputs.raw("cpi_mm_old", "1"), inputs.raw("cpi_mm", "1"))
    wq = decumulate_average(inputs.raw("wages_q", "1700"))
    real = wq / (to_quarterly_mean(cpi) / 100)
    p = pd.Period(period, freq="Q")
    this = real[(real.index.year == p.year) & (real.index.quarter <= p.quarter)].mean()
    last = real[(real.index.year == p.year - 1) & (real.index.quarter <= p.quarter)].mean()
    assert 100 * (this / last - 1) == pytest.approx(official, abs=1.0)


# ------------------------------------------------------------------ 2.4 levels
def test_yoy_to_levels_and_back(panels, inputs):
    for var in ("gdp", "gva_industry", "ind_total", "retail", "constr", "invest"):
        L = panels.levels[var]["level"]
        y = panels.yoy[panels.yoy["variable"] == var].set_index("period")["value"]
        back = yoy_index(L) - 100
        common = [p for p in back.dropna().index if str(p) in y.index]
        assert len(common) > 15, var
        np.testing.assert_allclose(back[common].to_numpy(), y[[str(p) for p in common]].to_numpy(),
                                   atol=1e-9, err_msg=var)


def test_gdp_level_2018_is_nominal_and_matches_published_growth(panels, inputs):
    L = panels.levels["gdp"]["level"]
    N = decumulate(inputs.raw("gdp_nag", "gdp.N"))
    np.testing.assert_allclose(L[L.index.year == 2018].to_numpy(), N[N.index.year == 2018].to_numpy())
    # cumulative growth of the level, with last year's nominal weights, is Uzstat's headline
    G = inputs.raw("gdp_nag", "gdp.G")
    g = yoy_index(L)
    w = N.shift(4)
    back = cumulative_growth(g, w)
    ok = back.notna()
    np.testing.assert_allclose(back[ok], G[ok], atol=1e-6)


def test_levels_bridge_a_missing_month_without_breaking_later_years():
    idx = pd.period_range("2021-01", "2023-12", freq="M")
    g = pd.Series(110.0, index=idx[12:])
    g[pd.Period("2022-05", freq="M")] = np.nan
    base = pd.Series(np.arange(1.0, 13.0), index=idx[:12])
    L, bridged = levels_from_yoy(g, base)
    assert np.isnan(L["2022-05"])
    assert bridged == ["2022-05"]
    assert L["2023-05"] == pytest.approx(np.sqrt(4 * 1.1 * 6 * 1.1) * 1.1)  # link = log-mean of Apr, Jun
    assert L["2023-06"] == pytest.approx(6 * 1.1 * 1.1)


# ------------------------------------------------------------------ 2.5 seasonal adjustment
def test_stl_removes_a_fixed_seasonal_pattern():
    idx = pd.period_range("2015-01", "2024-12", freq="M")
    seas = np.tile([0.9, 0.95, 1.1, 1.0, 1.0, 1.05, 1.0, 0.98, 1.0, 1.02, 0.97, 1.03], 10)
    level = pd.Series(100 * 1.005 ** np.arange(len(idx)) * seas, index=idx)
    sa, note = stl_adjust(level)
    g = gr.log_growth(sa).dropna()
    assert g.iloc[12:-12].std() < 0.05 and abs(g.mean() - 100 * np.log(1.005)) < 0.02


def test_short_series_are_not_adjusted():
    idx = pd.period_range("2024-01", "2025-12", freq="M")
    sa, note = stl_adjust(pd.Series(np.linspace(100, 120, len(idx)), index=idx))
    assert sa is None and "fewer than" in note


def test_market_services_are_not_in_the_sa_panel(panels):
    assert not panels.sa["variable"].str.startswith("mkt_").any()
    assert panels.yoy["variable"].str.startswith("mkt_").sum() > 300


# ------------------------------------------------------------------ 2.6 / 2.7 panels
def test_every_spec_variable_is_built(panels):
    ids = {v["id"] for v in load_spec()}
    built = set(panels.meta.loc[panels.meta["yoy_n"].notna(), "variable"])
    assert ids == built


def test_standardised_on_the_sample(panels):
    for name in ("yoy", "sa"):
        df = getattr(panels, name)
        df = df[df["block"] != "dummy"]
        stats = df.groupby("variable")["z"].agg(["mean", "std"])
        assert stats["mean"].abs().max() < 1e-9
        assert (stats["std"] - 1).abs().max() < 1e-9


def test_outliers_missing_in_clean_value_but_kept(panels):
    sa = panels.sa
    assert sa.loc[sa["outlier"], "value_clean"].isna().all()
    assert sa.loc[sa["outlier"], "value"].notna().all()
    gdp = sa[(sa["variable"] == "gdp") & sa["outlier"]]["period"].tolist()
    assert "2020Q2" in gdp


def test_covid_dummies(panels):
    d = panels.yoy[panels.yoy["block"] == "dummy"]
    assert set(d["variable"]) == {"d_2020Q2", "d_2020Q3"}
    assert d.loc[d["value"] == 1, "period"].tolist() == ["2020Q2", "2020Q3"]


def test_exports_without_gold(inputs, panels):
    x = decumulate(inputs.raw("exports_usd", "1700")) - inputs.raw("exports_gold_usd", "71") / 1000
    lvl = panels.levels["exports_nogold"]["level"]
    p = pd.Period("2026-03", freq="M")
    assert lvl[p] == pytest.approx(x[p])


# ------------------------------------------------------------------ 2.8 as-of builder
def test_released_on():
    assert released_on("2025-08", 2) == dt.date(2025, 10, 1)
    assert released_on("2025Q2", 2) == dt.date(2025, 8, 1)
    assert released_on("2025Q2", 4) == dt.date(2025, 10, 1)
    assert released_on("2025-12", 2) == dt.date(2026, 2, 1)


def test_build_panels_2025_11_01_holds_nothing_released_later(store):
    as_of = dt.date(2025, 11, 1)
    inp = load_inputs(store, as_of)
    assert set(inp.modes.values()) == {"pseudo"}
    for sid, item, p in inp.data[["series_id", "item", "ref_period"]].itertuples(index=False):
        assert released_on(p, CAT[sid]["months_after"]) <= as_of, (sid, item, p)
    p = build_panels(inputs=inp)
    last = p.meta.set_index("variable")
    assert last.loc["ind_total", "yoy_last"] == "2025-09"
    assert last.loc["gdp", "yoy_last"] == "2025Q3"
    assert last.loc["remittances_real", "yoy_last"] == "2025Q2"
    lag_of = {}
    for v in load_spec():
        ins = v.get("inputs") or [v.get("input") or ["gdp_nag"]]
        lag_of[v["id"]] = min(CAT[i[0]]["months_after"] for i in ins)
    for panel in (p.yoy, p.sa):
        rows = panel.loc[panel["block"] != "dummy", ["variable", "period"]]
        for var, period in rows.itertuples(index=False):
            assert released_on(period, lag_of[var]) <= as_of, (var, period)
    assert last.loc["usd_rate", "yoy_last"] == "2025-10"  # one-month lag


def test_vintage_mode_after_the_first_vintage(store):
    inp = load_inputs(store, "2026-10-16")
    assert inp.modes["ind_total_real"] == "vintage"
    early = load_inputs(store, "2026-10-15")  # seed workbooks: 14 Oct; SIAT-only series: 16 Oct
    assert early.modes["m2"] == "vintage"
    assert early.modes["ind_total_real"] == "pseudo" and early.modes["invest_nom_q"] == "pseudo"
