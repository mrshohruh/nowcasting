"""The NAG workbook changed layout on 2026-11-03 (sheet names, coded e-GDDS rows, Jan-Sep 2026 added).

Both layouts must read to the same shape; the 2025Q3 column of the new workbook contradicts its own
published growth and must not leak into the target.
"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from nowcast.ingest.quality import nag_inconsistencies
from nowcast.transform.target import (
    ITEMS,
    _code,
    build_target,
    find_quarterly_sheet,
    read_nag,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "nag"
NEW = FIX / "nag_uzbekistan_mcd_2026-11-05.xlsx"
OLD = Path(__file__).resolve().parents[1] / "data" / "seed" / "nag_uzbekistan_mcd_2026-10-14.xlsx"


@pytest.fixture(scope="module")
def new():
    return read_nag(NEW.read_bytes())


@pytest.fixture(scope="module")
def target_new(new):
    return build_target(new)


def _long(nag: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = [(f"{i}.{b}", q, v) for b, df in nag.items() for i in df.index for q, v in df.loc[i].items()]
    return pd.DataFrame(rows, columns=["item", "ref_period", "value"])


def test_new_layout_reads_through_2026q3(new):
    assert list(new) == ["N", "R", "G"]
    for df in new.values():
        assert list(df.index) == [k for k, _ in ITEMS]
        assert df.columns[0] == "2018Q1" and df.columns[-1] == "2026Q3"
    assert new["G"].loc["gdp", "2026Q3"] == pytest.approx(108.3, abs=0.05)  # SIAT 3698: 108.3
    assert new["N"].loc["gdp", "2026Q3"] == pytest.approx(1755485.8, abs=0.1)


def test_old_layout_still_reads_and_is_unchanged_in_new_workbook_before_2025q3(new):
    old = read_nag(OLD)
    assert old["N"].columns[-1] == "2026Q2"
    for k in "NRG":
        before = [q for q in old[k].columns if q < "2025Q3"]
        rel = (new[k][before] - old[k][before]).abs() / old[k][before].abs()
        assert rel.max().max() < 1e-5  # one item in 2024Q2 moved by 0.02 bn UZS


@pytest.mark.parametrize(
    "names, expected",
    [
        (["Annual 2018-2025", "Quarterly 2016-2017", "Quarterly 2018-2026"], "Quarterly 2018-2026"),
        (["Annual_2018-2025", "Quarterly_2016-2017", "Quarterly_2018-2026"], "Quarterly_2018-2026"),
        (["Quarterly 2018-2026", "Quarterly 2018-2027"], "Quarterly 2018-2027"),
    ],
)
def test_sheet_is_found_by_pattern(names, expected):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for n in names:
        wb.create_sheet(n)
    assert find_quarterly_sheet(wb) == expected


def test_missing_quarterly_sheet_is_a_layout_error():
    wb = openpyxl.Workbook()
    wb.active.title = "Annual 2018-2025"
    with pytest.raises(ValueError, match="NAG layout changed"):
        find_quarterly_sheet(wb)


def test_changed_indicator_code_is_a_layout_error():
    wb = openpyxl.load_workbook(NEW)
    ws = wb["Quarterly 2018-2026"]
    for row in ws.iter_rows(min_row=1, max_row=12):
        if row[2].value == "NGDP_PA_CUM_XDC":
            row[2].value = "NGDP_PA_CUM_XDC_X"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="NAG layout changed"):
        read_nag(buf.getvalue())


def test_codes_match_the_published_indicator_names():
    assert _code("N", "gdp") == "NGDP_PA_CUM_XDC"
    assert _code("R", "industry") == "UZB_NGDPVA_R_NACE2_BTE_PYP_CUM_XDC"
    assert _code("G", "other_services") == "UZB_NGDPVA_R_NACE2_KTU_PYP_CUM_PT"
    assert _code("G", "transport_ict") == "NGDPVA_R_NACE2_H_J_PYP_CUM_PT"
    assert _code("N", "net_taxes") == "NGDPT_CUM_XDC"
    assert _code("R", "gva") == "NGDPVA_R_NACE2_PYP_CUM_XDC"


def test_levels_contradict_growth_only_in_2025q3(new):
    bad = nag_inconsistencies(_long(new))
    assert {p for _, p, _, _ in bad} == {"2025Q3"}
    assert ("gdp", "2025Q3") in {(b, p) for b, p, _, _ in bad}
    assert nag_inconsistencies(_long(read_nag(OLD))) == []


def test_only_new_or_revised_cells_are_checked(new):
    assert nag_inconsistencies(_long(new), only={("gdp", "2026Q3")}) == []


def test_target_uses_published_growth_where_levels_disagree(target_new):
    t = target_new.set_index(["item", "quarter"])
    assert t.loc[("gdp", "2025Q3"), "real_basis"] == "published growth"
    assert t.loc[("gdp", "2026Q3"), "real_basis"] == "levels"
    # plausible discrete growth instead of the 147% / 73% the raw levels imply
    for q in ("2025Q3", "2025Q4"):
        assert 104 < t.loc[("gdp", q), "growth_disc"] < 112


def test_target_2026q3_is_published_growth_and_reaggregates(target_new):
    t = target_new[target_new["item"] == "gdp"].set_index("quarter")
    assert t.loc["2026Q3", "growth_cum_published"] == pytest.approx(108.3, abs=0.05)
    assert t.loc["2026Q3", "growth_disc"] == pytest.approx(107.99, abs=0.02)
    # cumulative growth is the weighted mean of discrete growth, for every quarter incl. 2025Q3
    from nowcast.transform.target import cumulative_from_discrete

    cum = cumulative_from_discrete(t["growth_disc"], t["nominal_prev_year_disc"])
    assert (cum - t["growth_cum_published"]).abs().max() < 1e-3


def test_old_workbook_target_is_all_levels():
    t = build_target(read_nag(OLD))
    assert set(t["real_basis"]) == {"levels"}
