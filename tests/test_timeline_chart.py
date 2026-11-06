"""The Nowcast Timeline names its blue line for what it is and shows Uzstat's cumulative rate."""

from nowcast.report.charts import ACTUAL_LABEL, nowcast_vs_actual_chart


def _rows():
    return [
        dict(model="dfm", horizon="nowcast_m3", target_quarter="2026Q3", run_date="2026-09-01",
             forecast=7.7, actual=7.98, actual_cum=8.3),
        dict(model="dfm", horizon="nowcast_m3", target_quarter="2026Q2", run_date="2026-06-01",
             forecast=7.8, actual=8.40, actual_cum=8.5),
        dict(model="dfm", horizon="nowcast_m3", target_quarter="2026Q1", run_date="2026-03-01",
             forecast=6.9, actual=8.66, actual_cum=None),
    ]


def test_actual_line_is_not_called_official():
    fig = nowcast_vs_actual_chart(_rows())
    names = [t.get("name") for t in fig["data"]]
    assert ACTUAL_LABEL in names
    assert not any("Official" in str(n) for n in names)


def test_hover_carries_uzstat_cumulative_rate():
    fig = nowcast_vs_actual_chart(_rows())
    trace = next(t for t in fig["data"] if t.get("name") == ACTUAL_LABEL)
    by_q = dict(zip(trace["x"], trace["customdata"], strict=True))
    assert by_q["2026Q3"] == "Uzstat Jan-Sep: 8.3%"
    assert by_q["2026Q2"] == "Uzstat Jan-Jun: 8.5%"
    assert by_q["2026Q1"] == ""
