"""The Nowcast Timeline is on Uzstat's published basis: cumulative from January."""

from nowcast.report.charts import ACTUAL_LABEL, nowcast_vs_actual_chart


def _rows():
    return [
        dict(model="dfm", horizon="nowcast_m3", target_quarter="2026Q3", run_date="2026-09-01",
             forecast=7.7, forecast_ytd=8.2, actual=7.98, actual_cum=8.3),
        dict(model="dfm", horizon="nowcast_m3", target_quarter="2026Q2", run_date="2026-06-01",
             forecast=7.8, forecast_ytd=8.2, actual=8.40, actual_cum=8.5),
        dict(model="dfm", horizon="nowcast_m3", target_quarter="2026Q1", run_date="2026-03-01",
             forecast=6.9, forecast_ytd=6.9, actual=8.66, actual_cum=8.66),
    ]


def _trace(fig, name):
    return next(t for t in fig["data"] if t.get("name") == name)


def test_actual_line_is_uzstat_cumulative_figure():
    fig = nowcast_vs_actual_chart(_rows())
    names = [t.get("name") for t in fig["data"]]
    assert ACTUAL_LABEL in names and "cumulative" in ACTUAL_LABEL
    assert not any("Official" in str(n) for n in names)
    y = dict(zip(*(lambda t: (t["x"], t["y"]))(_trace(fig, ACTUAL_LABEL)), strict=True))
    assert y == {"2026Q1": 8.66, "2026Q2": 8.5, "2026Q3": 8.3}


def test_nowcast_line_is_year_to_date_and_hover_has_the_quarter():
    fig = nowcast_vs_actual_chart(_rows())
    t = _trace(fig, "Nowcast (month 3, end of quarter)")
    assert dict(zip(t["x"], t["y"], strict=True))["2026Q3"] == 8.2
    text = dict(zip(t["x"], t["customdata"], strict=True))
    assert text["2026Q3"] == "Jan-Sep 2026<br>Q3 alone: 7.7%"
    assert text["2026Q1"] == "Jan-Mar 2026"  # Q1 is the quarter itself


def test_hover_of_uzstat_line_has_the_derived_quarter():
    fig = nowcast_vs_actual_chart(_rows())
    t = _trace(fig, ACTUAL_LABEL)
    text = dict(zip(t["x"], t["customdata"], strict=True))
    assert text["2026Q3"] == "Uzstat Jan-Sep 2026<br>Q3 alone: 8.0%"


def test_current_point_is_year_to_date_with_band():
    cur = dict(quarter="2026Q4", value=8.5, band_lo=7.9, band_hi=9.1, quarter_value=9.0)
    t = _trace(nowcast_vs_actual_chart(_rows(), current=cur), "Current nowcast 2026Q4")
    assert t["y"] == [8.5]
    assert t["customdata"] == ["Jan-Dec 2026<br>Q4 alone: 9.0%"]
    assert abs(t["error_y"]["array"][0] - 0.6) < 1e-9


def test_rows_without_year_to_date_are_not_drawn_on_the_wrong_basis():
    rows = [dict(r, forecast_ytd=None) for r in _rows()]
    t = _trace(nowcast_vs_actual_chart(rows), "Nowcast (month 3, end of quarter)")
    assert all(v is None for v in t["y"])
