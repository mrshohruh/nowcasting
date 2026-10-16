"""Change detector (task 1.5).

``compare`` sets one series as just parsed against what the store knew before the run and
sorts every (item, period) into new, revised (old and new value), removed, or unchanged. The
same result gives the delta rows the store writes and the entry in ``changes.json``.

Two values are equal when they differ by less than 1e-9 relative: floats read from JSON, XML
and xlsx are not always bit-identical across files, and no publisher reports 10 significant
digits.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from nowcast.store.vintages import OBS_COLUMNS

RTOL = 1e-9


@dataclass
class SeriesChanges:
    series_id: str
    new: list[dict] = field(default_factory=list)
    revised: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    unchanged: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.new or self.revised or self.removed)

    def summary(self) -> str:
        if not self.changed:
            return "no change"
        parts = []
        if self.new:
            ps = sorted({r["ref_period"] for r in self.new})
            parts.append(f"{len(self.new)} new ({ps[0]}" + (f" to {ps[-1]})" if len(ps) > 1 else ")"))
        if self.revised:
            parts.append(f"{len(self.revised)} revised")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        return ", ".join(parts)

    def to_json(self) -> dict:
        return {
            "summary": self.summary(),
            "new": self.new,
            "revised": self.revised,
            "removed": self.removed,
            "unchanged": self.unchanged,
        }


def _close(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a - b) <= RTOL * np.maximum(1.0, np.maximum(np.abs(a), np.abs(b)))


def compare(series_id: str, prev: pd.DataFrame, new: pd.DataFrame) -> SeriesChanges:
    """prev, new: columns item, ref_period, value (prev may be empty)."""
    keys = ["item", "ref_period"]
    p = (
        prev[keys + ["value"]].rename(columns={"value": "old"})
        if len(prev)
        else pd.DataFrame(columns=keys + ["old"])
    )
    n = new[keys + ["value"]]
    m = p.merge(n, on=keys, how="outer", indicator=True).sort_values(keys)
    out = SeriesChanges(series_id)
    both = m[m["_merge"] == "both"]
    same = _close(both["old"].to_numpy(float), both["value"].to_numpy(float))
    out.unchanged = int(same.sum())
    for r in both[~same].itertuples():
        out.revised.append(
            {"item": r.item, "ref_period": r.ref_period, "old": float(r.old), "new": float(r.value)}
        )
    for r in m[m["_merge"] == "right_only"].itertuples():
        out.new.append({"item": r.item, "ref_period": r.ref_period, "value": float(r.value)})
    for r in m[m["_merge"] == "left_only"].itertuples():
        out.removed.append({"item": r.item, "ref_period": r.ref_period, "old": float(r.old)})
    return out


def delta_rows(
    ch: SeriesChanges, vintage_date: dt.date, run_id: str, source_hash: str, fetched_at: dt.datetime
) -> pd.DataFrame:
    rows = []
    for r in ch.new:
        rows.append((r["item"], r["ref_period"], r["value"], False))
    for r in ch.revised:
        rows.append((r["item"], r["ref_period"], r["new"], False))
    for r in ch.removed:
        rows.append((r["item"], r["ref_period"], np.nan, True))
    df = pd.DataFrame(rows, columns=["item", "ref_period", "value", "removed"])
    df["series_id"] = ch.series_id
    df["vintage_date"] = vintage_date
    df["source_hash"] = source_hash
    df["fetched_at"] = fetched_at
    df["run_id"] = run_id
    return df[OBS_COLUMNS]
