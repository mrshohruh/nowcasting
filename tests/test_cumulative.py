"""Year-to-date conversion: quarter nowcasts back to Uzstat's published cumulative growth."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nowcast.transform.cumulative import Published, last_year_chain, to_ytd
from nowcast.transform.target import SEED_NAG, build_target, cumulative_from_discrete, read_nag

NEW_NAG = Path(__file__).resolve().parent / "fixtures" / "nag" / "nag_uzbekistan_mcd_2026-11-05.xlsx"


def _pub(nag: dict[str, pd.DataFrame]) -> Published:
    return Published(N=nag["N"].loc["gdp"].dropna(), R=nag["R"].loc["gdp"].dropna(),
                     G=nag["G"].loc["gdp"].dropna() - 100.0)


def _without(pub: Published, quarter: str) -> Published:
    """As if Uzstat had not yet published *quarter*."""
    return Published(N=pub.N.drop(quarter), R=pub.R.drop(quarter), G=pub.G.drop(quarter))


@pytest.fixture(scope="module", params=["seed", "new"])
def case(request):
    nag = read_nag(SEED_NAG if request.param == "seed" else NEW_NAG)
    t = build_target(nag)
    return _pub(nag), t[t["item"] == "gdp"].set_index("quarter")


def test_derived_quarter_goes_back_to_uzstat_cumulative(case):
    """Every published Jan-Jun / Jan-Sep / Jan-Dec rate, from the quarter derived from it."""
    pub, t = case
    checked = 0
    for tq in t.index[t.index.str[-1] != "1"]:
        if int(tq[:4]) < 2019:  # 2018 needs the year before, which the file does not have
            continue
        r = to_ytd(tq, {tq: t.loc[tq, "growth_disc"] - 100.0}, _without(pub, tq))
        assert r.value == pytest.approx(t.loc[tq, "growth_cum_published"] - 100.0, abs=1e-6), tq
        checked += 1
    assert checked >= 20


def test_jan_sep_2026_is_8_3_from_the_derived_quarter():
    pub = _pub(read_nag(NEW_NAG))
    r = to_ytd("2026Q3", {"2026Q3": 7.98}, _without(pub, "2026Q3"))
    assert r.known_through == "2026Q2"
    assert r.value == pytest.approx(8.30, abs=0.01)
    assert r.span == "Jan-Sep"


def test_q1_is_the_quarter_itself():
    pub = _pub(read_nag(NEW_NAG))
    r = to_ytd("2027Q1", {"2027Q1": 7.4}, pub)
    assert r.value == 7.4 and r.target_share == 1.0 and r.known_through is None


def test_published_quarter_returns_uzstat():
    pub = _pub(read_nag(NEW_NAG))
    r = to_ytd("2026Q3", {"2026Q3": 1.0}, pub)
    assert r.value == pytest.approx(8.2995, abs=1e-3)
    assert r.target_share == 0.0


def test_unpublished_earlier_quarter_needs_an_estimate():
    pub = _without(_without(_pub(read_nag(SEED_NAG)), "2026Q2"), "2026Q1")
    assert np.isnan(to_ytd("2026Q2", {"2026Q2": 8.0}, pub).value)
    r = to_ytd("2026Q2", {"2026Q1": 8.0, "2026Q2": 9.0}, pub)
    assert r.estimated == ["2026Q1", "2026Q2"] and r.known_through is None
    # Same as the target module's weighted mean with last year's nominal quarters
    t = build_target(read_nag(SEED_NAG))
    w = t[t["item"] == "gdp"].set_index("quarter")["nominal_prev_year_disc"]
    expect = cumulative_from_discrete(pd.Series({"2026Q1": 8.0, "2026Q2": 9.0}), w[["2026Q1", "2026Q2"]])
    assert r.value == pytest.approx(expect["2026Q2"], abs=1e-9)
    assert r.unknown_share == pytest.approx(1.0)


def test_linear_in_the_target_quarter():
    pub = _without(_pub(read_nag(NEW_NAG)), "2026Q3")
    a = to_ytd("2026Q3", {"2026Q3": 7.0}, pub)
    b = to_ytd("2026Q3", {"2026Q3": 9.0}, pub)
    assert b.value - a.value == pytest.approx(2.0 * a.target_share, abs=1e-9)
    assert a.with_target(9.0) == pytest.approx(b.value, abs=1e-9)
    assert 0.35 < a.target_share < 0.45  # Q3 is about 40% of Jan-Sep


def test_guard_replaces_the_broken_2025q4_level():
    """In the 2026-11-05 workbook Jan-Dec 2025 is on the old basis and Jan-Sep 2025 on the new:
    Q4 2025 would weigh 24% of Jan-Sep instead of 42-50%."""
    pub = _pub(read_nag(NEW_NAG))
    chain, basis = last_year_chain(pub, 2026)
    assert basis[4].startswith("median share")
    assert all(basis[j] == "levels" for j in (1, 2, 3))
    assert 0.40 < (chain[4] - chain[3]) / chain[3] < 0.52
    r = to_ytd("2026Q4", {"2026Q4": 9.0}, pub)
    assert r.known_through == "2026Q3"
    assert 0.28 < r.target_share < 0.36  # Q4 is about a third of the year, as in 2019-2025
    assert r.weight_basis["2025Q4"].startswith("median share")
    # Between Uzstat's Jan-Sep 8.3% and the quarter's 9.0%
    assert 8.3 < r.value < 9.0


def test_guard_leaves_consistent_years_alone():
    for path in (SEED_NAG, NEW_NAG):
        pub = _pub(read_nag(path))
        for year in range(2020, 2026):
            _, basis = last_year_chain(pub, year)
            assert set(basis.values()) == {"levels"}, (path.name, year)


def test_guard_catches_q3_2025_in_the_october_workbook():
    """The 2026-10-14 workbook had Jan-Sep 2025 only 45% above Jan-Jun (a fall in current
    prices against Q3 2024); the new workbook has 66%, inside the usual 60-62%."""
    _, basis = last_year_chain(_pub(read_nag(SEED_NAG)), 2026)
    assert basis[3].startswith("median share")
    _, basis = last_year_chain(_pub(read_nag(NEW_NAG)), 2026)
    assert basis[3] == "levels"


def test_decomposition_in_ytd_terms_adds_up():
    from nowcast.explain.news import Decomposition, NewsItem

    pub = _pub(read_nag(NEW_NAG))
    ytd = to_ytd("2026Q4", {"2026Q4": 9.0}, pub)
    d = Decomposition(target_quarter="2026Q4", old_nowcast=8.8, new_nowcast=9.0, total_change=0.2,
                      revision_effect=0.05, news_effect=0.10, reestimation_effect=0.05,
                      news_items=[NewsItem("retail", "2026-09", 1, 0, 1, 0.1, 0.10)])
    y = d.in_ytd_terms(ytd, old_ytd=8.28, old_quarter=8.55)
    s = ytd.target_share
    assert y.basis == "ytd" and y.old_nowcast == 8.28 and y.new_nowcast == ytd.value
    assert y.news_effect == pytest.approx(0.10 * s) and y.news_items[0].impact == pytest.approx(0.10 * s)
    assert y.residual < 1e-12  # earlier quarters + scaled quarter effects = total change
    assert y.earlier_effect == pytest.approx(ytd.value - 8.28 - 0.2 * s)
