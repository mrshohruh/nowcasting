"""Vintage store, change detector and quality checks (tasks 1.3, 1.5, 1.6)."""

import datetime as dt

import pandas as pd
import pytest

from nowcast.ingest.base import Parsed
from nowcast.ingest.quality import check, expected_latest
from nowcast.store.changes import compare, delta_rows
from nowcast.store.vintages import Store


def frame(values: dict[str, float], item: str = "x") -> pd.DataFrame:
    return pd.DataFrame({"item": item, "ref_period": list(values), "value": list(values.values())})


def write(store: Store, sid: str, df: pd.DataFrame, date: str, run_id: str | None = None):
    run_id = run_id or date
    prev = store.latest([sid])[["item", "ref_period", "value"]]
    ch = compare(sid, prev, df)
    when = dt.date.fromisoformat(date)
    store.write_observations(run_id, delta_rows(ch, when, run_id, "h", dt.datetime.combine(when, dt.time())))
    return ch


# --------------------------------------------------------------------------- change detector
def test_planted_revision_new_and_removed():
    old = frame({"2026-06": 100.0, "2026-07": 101.0, "2026-08": 102.0})
    new = frame({"2026-06": 100.0, "2026-07": 101.5, "2026-09": 103.0})
    ch = compare("s", old, new)
    assert ch.revised == [{"item": "x", "ref_period": "2026-07", "old": 101.0, "new": 101.5}]
    assert ch.new == [{"item": "x", "ref_period": "2026-09", "value": 103.0}]
    assert ch.removed == [{"item": "x", "ref_period": "2026-08", "old": 102.0}]
    assert ch.unchanged == 1 and ch.summary() == "1 new (2026-09), 1 revised, 1 removed"


def test_identical_data_is_no_change():
    df = frame({"2026-07": 101.0, "2026-08": 1e-12 + 102.0})
    ch = compare("s", frame({"2026-07": 101.0, "2026-08": 102.0}), df)
    assert not ch.changed and ch.summary() == "no change" and ch.unchanged == 2


# ----------------------------------------------------------------------------------- store
def test_as_of_keeps_every_vintage(tmp_path):
    st = Store(tmp_path)
    assert st.is_empty() and st.latest().empty
    write(st, "s", frame({"2026-07": 101.0}), "2026-09-01")
    write(st, "s", frame({"2026-07": 101.5, "2026-08": 102.0}), "2026-10-01")
    write(st, "s", frame({"2026-08": 102.0}), "2026-11-01")  # July disappears from the source
    v = lambda d: st.as_of(d, ["s"]).set_index("ref_period")["value"].to_dict()  # noqa: E731
    assert v("2026-08-31") == {}
    assert v("2026-09-15") == {"2026-07": 101.0}
    assert v("2026-10-01") == {"2026-07": 101.5, "2026-08": 102.0}
    assert v("2026-11-01") == {"2026-08": 102.0}
    assert st.last_periods() == {"s": "2026-08"}
    # the store holds deltas only: 1 + 2 (one revised, one new) + 1 removal row
    with st.connect() as con:
        assert con.execute("select count(*) from observations").fetchone()[0] == 4


def test_same_day_runs_and_ordering(tmp_path):
    st = Store(tmp_path)
    assert st.new_run_id(dt.date(2026, 12, 1)) == "2026-12-01"
    write(st, "s", frame({"2026-10": 1.0}), "2026-12-01")
    (st.runs_dir / "2026-12-01").mkdir()
    rid = st.new_run_id(dt.date(2026, 12, 1))
    assert rid == "2026-12-01.2"
    prev = st.latest(["s"])[["item", "ref_period", "value"]]
    ch = compare("s", prev, frame({"2026-10": 2.0}))
    st.write_observations(rid, delta_rows(ch, dt.date(2026, 12, 1), rid, "h", dt.datetime(2026, 12, 1, 9)))
    assert st.latest(["s"])["value"].tolist() == [2.0]  # later run on the same day wins
    with pytest.raises(FileExistsError):
        st.write_observations(
            rid, delta_rows(ch, dt.date(2026, 12, 1), rid, "h", dt.datetime(2026, 12, 1, 9))
        )


# ------------------------------------------------------------------------------ quality
def series(**kw):
    base = {"id": "s", "source": "siat", "key": "x", "frequency": "M", "form": "level", "months_after": 2}
    return {**base, **kw}


@pytest.mark.parametrize(
    "freq,after,run,expected",
    [
        ("M", 2, "2026-10-01", "2026-08"),
        ("M", 2, "2026-12-01", "2026-10"),
        ("M", 1, "2026-01-01", "2025-12"),
        ("Q", 2, "2026-08-01", "2026Q2"),
        ("Q", 2, "2026-07-31", "2026Q1"),
        ("Q", 4, "2026-10-01", "2026Q2"),
        ("Q", 2, "2027-01-01", "2026Q3"),
        ("Q", 2, "2027-02-01", "2026Q4"),
    ],
)
def test_expected_latest(freq, after, run, expected):
    assert expected_latest(series(frequency=freq, months_after=after), dt.date.fromisoformat(run)) == expected


def run_check(s, df, prev=None, meta=None, prev_meta=None, run="2026-10-01"):
    prev = prev if prev is not None else frame({})
    ch = compare("s", prev, df)
    latest = prev["ref_period"].max() if len(prev) else None
    return check(
        s,
        Parsed(df, meta or {"unit": "u", "items": ["x"]}),
        prev_meta,
        ch,
        latest,
        dt.date.fromisoformat(run),
    )


def test_clean_series_is_ok():
    prev = frame({"2026-06": 1.0, "2026-07": 2.0})
    r = run_check(
        series(),
        frame({"2026-06": 1.0, "2026-07": 2.0, "2026-08": 3.0}),
        prev,
        prev_meta={"unit": "u", "items": ["x"]},
    )
    assert r.status == "ok" and r.info["new_periods"] == 1 and r.info["new_periods_expected"] == 1


def test_unit_change_fails():
    r = run_check(
        series(),
        frame({"2026-08": 3.0}),
        meta={"unit": "million", "items": ["x"]},
        prev_meta={"unit": "billion", "items": ["x"]},
    )
    assert r.status == "fail" and "unit changed" in r.messages[0]


def test_duplicates_fail():
    df = pd.concat([frame({"2026-08": 3.0}), frame({"2026-08": 3.1})], ignore_index=True)
    assert run_check(series(), df).status == "fail"


def test_index_mm_range_only_on_new_values():
    s = series(form="index_mm")
    old_outlier = frame({"2019-01": 171.9, "2026-08": 100.4})
    assert run_check(s, old_outlier, prev=old_outlier).status == "ok"  # already in the store
    r = run_check(s, frame({"2019-01": 171.9, "2026-08": 100.4}))  # first load: every value is new
    assert r.status == "fail" and "outside 80-125" in r.messages[0]


def test_cumulative_fall_warns():
    r = run_check(series(form="cumulative_ytd"), frame({"2026-06": 10.0, "2026-07": 9.0, "2026-08": 12.0}))
    assert r.status == "warn" and "x:2026-07" in r.messages[0]
    jan = run_check(
        series(form="cumulative_ytd"), frame({"2025-12": 120.0, "2026-01": 10.0, "2026-08": 80.0})
    )
    assert jan.status == "ok"  # restarting in January is not a fall


def test_late_release_warns_and_discontinued_does_not():
    assert run_check(series(), frame({"2026-07": 1.0}), run="2026-10-01").status == "warn"
    assert run_check(series(last_period="2020-12"), frame({"2020-12": 1.0})).status == "ok"


def test_items_and_labels_changed_warn():
    meta = {"unit": "u", "items": ["x"], "labels": {"x": "Industry"}}
    prev_meta = {"unit": "u", "items": ["x", "y"], "labels": {"x": "Industrial production"}}
    r = run_check(series(key="Industry"), frame({"2026-08": 1.0}), meta=meta, prev_meta=prev_meta)
    assert r.status == "warn"
    assert any("items missing" in m for m in r.messages) and any(
        "label of x changed" in m for m in r.messages
    )
