from pathlib import Path

import pandas as pd
import pytest

from nowcast.transform.target import build_target, cumulative_from_discrete

ROOT = Path(__file__).resolve().parents[1]
SEED_MONTHLY = ROOT / "data" / "seed" / "uzbekistan_macro_monthly_quarterly_2026-10-14.xlsx"


@pytest.fixture(scope="module")
def target() -> pd.DataFrame:
    return build_target()


def test_cumulative_matches_published_everywhere(target):
    # exit check of Phase 0: within 0.1 pp; in practice it matches to rounding noise
    diff = (target["growth_cum_computed"] - target["growth_cum_published"]).abs()
    assert diff.max() < 1e-3


def test_q2_2026_discrete_is_8_4(target):
    g = target.set_index(["item", "quarter"]).loc[("gdp", "2026Q2"), "growth_disc"]
    assert g == pytest.approx(108.40, abs=0.01)


def test_q1_discrete_equals_cumulative(target):
    q1 = target[target["quarter"].str.endswith("Q1")]
    assert (q1["growth_disc"] - q1["growth_cum_published"]).abs().max() < 1e-3


@pytest.mark.parametrize("item", ["gdp", "gva", "agriculture", "industry", "construction",
                                  "trade_accommodation", "transport_ict", "other_services",
                                  "net_taxes"])
def test_discrete_reaggregates_to_cumulative(target, item):
    t = target[target["item"] == item].set_index("quarter")
    cum = cumulative_from_discrete(t["growth_disc"], t["nominal_prev_year_disc"])
    assert (cum - t["growth_cum_published"]).abs().max() < 1e-3


def test_seed_quarterly_gdp_column_agrees(target):
    seed = pd.read_excel(SEED_MONTHLY, sheet_name="Quarterly_Data", index_col=0)
    col = [c for c in seed.columns if "Gross domestic product" in c][0]
    gdp = target[target["item"] == "gdp"].set_index("quarter")["growth_cum_published"]
    joined = pd.concat([seed[col], gdp], axis=1, join="inner").dropna()
    assert len(joined) >= 28
    assert (joined.iloc[:, 0] - joined.iloc[:, 1]).abs().max() <= 0.051  # seed is rounded to 0.1
