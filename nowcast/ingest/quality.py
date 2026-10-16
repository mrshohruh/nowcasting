"""Quality checks (task 1.6). Each series in a run is marked ok, warn or fail.

| check                                    | result if it trips |
| ---------------------------------------- | ------------------ |
| no data at all                           | fail               |
| duplicate (item, period)                 | fail               |
| unit as published changed                | fail               |
| m/m index outside 80-125 (index_mm)      | fail               |
| items disappeared or appeared            | warn               |
| label of a selected code changed         | warn               |
| cumulative value falls within a year     | warn               |
| periods removed from the source          | warn               |
| latest period behind what the lag allows | warn               |

Range and cumulative checks look only at periods that are new or revised in this run, so a known
quirk in old data (e.g. wholesale falling in 2023-11) warns once, when the series is first
loaded, not every month. A failed series is left out of the store for this run; the store keeps
what it had, and the run carries on with the other series.

The lag rule is the backtest rule of Phase 0: a period is usable from the 1st of the month
``months_after`` months after it ends (2 = August data from 1 October, Q2 from 1 August).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from nowcast.ingest.base import Parsed, period_index
from nowcast.store.changes import SeriesChanges

INDEX_MM_RANGE = (80.0, 125.0)
ORDER = {"ok": 0, "warn": 1, "fail": 2}


@dataclass
class Result:
    status: str = "ok"
    messages: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def flag(self, status: str, msg: str) -> None:
        if ORDER[status] > ORDER[self.status]:
            self.status = status
        self.messages.append(f"{status}: {msg}")


def expected_latest(series: dict, run_date: dt.date) -> str:
    """Latest period the lag rule says should be out by ``run_date``."""
    last_usable = run_date.year * 12 + run_date.month - 1 - int(series["months_after"])  # month index
    if series["frequency"] == "M":
        return f"{last_usable // 12:04d}-{last_usable % 12 + 1:02d}"
    y, m0 = divmod(last_usable, 12)  # last quarter whose final month is <= last_usable
    q = (m0 + 1) // 3
    return f"{y:04d}Q{q}" if q else f"{y - 1:04d}Q4"


def _steps(series: dict, a: str, b: str) -> int:
    """Number of periods from a to b (b - a)."""
    n = period_index(b) - period_index(a)
    return n if series["frequency"] == "M" else n // 3


def check(
    series: dict,
    parsed: Parsed,
    prev_meta: dict | None,
    changes: SeriesChanges | None,
    prev_latest: str | None,
    run_date: dt.date,
) -> Result:
    res = Result()
    df, meta = parsed.data, parsed.meta
    if df.empty:
        res.flag("fail", "no data in the file")
        return res
    dup = df.duplicated(["item", "ref_period"])
    if dup.any():
        res.flag("fail", f"{int(dup.sum())} duplicate periods, e.g. {df[dup].iloc[0]['ref_period']}")
    freq_ok = df["ref_period"].str.contains("Q") == (series["frequency"] == "Q")
    if not freq_ok.all():
        res.flag("fail", f"periods do not match frequency {series['frequency']}")
        return res

    if prev_meta:
        if prev_meta.get("unit") != meta.get("unit"):
            res.flag("fail", f"unit changed from {prev_meta.get('unit')!r} to {meta.get('unit')!r}")
        old, new = set(prev_meta.get("items", [])), set(meta.get("items", []))
        if old - new:
            res.flag("warn", f"items missing: {sorted(old - new)}")
        if new - old:
            res.flag("warn", f"new items: {sorted(new - old)}")
        for code, lab in (meta.get("labels") or {}).items():
            was = (prev_meta.get("labels") or {}).get(code)
            if was is not None and was != lab:
                res.flag("warn", f"label of {code} changed from {was!r} to {lab!r}")
    labels = meta.get("labels") or {}
    if series["source"] == "siat" and len(labels) == 1 and not str(series["key"]).startswith("all items"):
        lab = next(iter(labels.values()))
        if not lab.lower().startswith(str(series["key"]).rstrip(". ").lower()[:40]):
            res.flag("warn", f"label {lab!r} does not match catalogue key {series['key']!r}")

    touched = set()
    if changes is not None:
        touched = {(r["item"], r["ref_period"]) for r in changes.new} | {
            (r["item"], r["ref_period"]) for r in changes.revised
        }
        if changes.removed:
            ps = sorted({r["ref_period"] for r in changes.removed})
            res.flag("warn", f"{len(changes.removed)} periods removed from the source ({ps[0]}..{ps[-1]})")
    is_touched = pd.Series(
        [(i, p) in touched for i, p in zip(df["item"], df["ref_period"], strict=True)], index=df.index
    )

    if series["form"] == "index_mm":
        lo, hi = INDEX_MM_RANGE
        bad = df[is_touched & ~df["value"].between(lo, hi)]
        if len(bad):
            r = bad.iloc[0]
            res.flag(
                "fail",
                f"{len(bad)} m/m index values outside {lo:g}-{hi:g}, e.g. {r['ref_period']}={r['value']:g}",
            )

    if series["form"] == "cumulative_ytd":
        d = df.sort_values(["item", "ref_period"]).copy()
        d["year"] = d["ref_period"].str[:4]
        d["prev"] = d.groupby(["item", "year"])["value"].shift(1)
        falls = d[(d["value"] < d["prev"] - 1e-9) & is_touched.reindex(d.index)]
        if len(falls):
            res.flag(
                "warn",
                "cumulative value falls within the year at "
                + ", ".join(f"{r.item}:{r.ref_period}" for r in falls.head(5).itertuples()),
            )

    latest = df["ref_period"].max()
    res.info["latest"] = latest
    if not series.get("last_period"):
        exp = expected_latest(series, run_date)
        res.info["expected_latest"] = exp
        if latest < exp:
            res.flag("warn", f"latest period is {latest}; the lag rule expects {exp} by {run_date}")
        if prev_latest:
            res.info["new_periods"] = _steps(series, prev_latest, latest)
            res.info["new_periods_expected"] = max(0, _steps(series, prev_latest, exp))
    return res
