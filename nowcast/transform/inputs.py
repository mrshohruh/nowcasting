"""Inputs of the panels as they stood on a date (task 2.8).

For each catalogue series:

* ``vintage``  the as-of date is on or after the series' first vintage in the store: its values
               are exactly what the store knew that day (``Store.as_of``);
* ``pseudo``   the date is before the first vintage (every backtest date before October 2026):
               the latest values, cut back by the series' release lag. A period counts as
               released on the 1st of the month ``months_after`` months after it ends (Phase 0
               rule; August data on 1 October, Q2 on 1 August, remittances Q2 on 1 October).
               This is pseudo-real-time: revisions made later are already in the values.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from nowcast.catalogue import load as load_catalogue
from nowcast.transform.decumulate import regular


def released_on(ref_period: str, months_after: int) -> dt.date:
    """First day a period may be used under the release-lag rule."""
    y = int(ref_period[:4])
    end_month = int(ref_period[5:7]) if "-" in ref_period else 3 * int(ref_period[5])
    m = y * 12 + (end_month - 1) + int(months_after)
    return dt.date(m // 12, m % 12 + 1, 1)


def to_period_index(ref_periods: pd.Series) -> pd.PeriodIndex:
    first = str(ref_periods.iloc[0])
    return pd.PeriodIndex(ref_periods.astype(str), freq="M" if "-" in first else "Q")


@dataclass
class Inputs:
    """Long frame of input values plus the catalogue; hands out regular period series."""

    data: pd.DataFrame
    catalogue: dict[str, dict]
    as_of: dt.date
    modes: dict[str, str] = field(default_factory=dict)

    def raw(self, series_id: str, item: str) -> pd.Series:
        d = self.data[(self.data["series_id"] == series_id) & (self.data["item"] == str(item))]
        if d.empty:
            return pd.Series(dtype=float)
        s = pd.Series(d["value"].to_numpy(dtype=float), index=to_period_index(d["ref_period"]))
        return regular(s.sort_index())

    def form(self, series_id: str) -> str:
        return self.catalogue[series_id]["form"]

    def has(self, series_id: str, item: str) -> bool:
        return not self.raw(series_id, item).empty


def load_inputs(store, as_of: dt.date | str, catalogue: list[dict] | None = None) -> Inputs:
    as_of = dt.date.fromisoformat(str(as_of))
    cat = {s["id"]: s for s in (catalogue or load_catalogue())}
    with store.connect() as con:
        first = dict(con.execute(
            "SELECT series_id, min(vintage_date) FROM observations GROUP BY 1").fetchall())
    real = store.as_of(as_of)
    latest = None
    parts, modes = [], {}
    for sid, s in cat.items():
        fv = first.get(sid)
        if fv is None:
            modes[sid] = "absent"
            continue
        if fv <= as_of:
            parts.append(real[real["series_id"] == sid])
            modes[sid] = "vintage"
        else:
            if latest is None:
                latest = store.latest()
            d = latest[latest["series_id"] == sid]
            ok = [released_on(p, s["months_after"]) <= as_of for p in d["ref_period"]]
            parts.append(d[ok])
            modes[sid] = "pseudo"
    cols = ["series_id", "item", "ref_period", "value", "vintage_date"]
    data = pd.concat([p[cols] for p in parts if len(p)], ignore_index=True) if parts else pd.DataFrame(
        columns=cols)
    return Inputs(data=data, catalogue=cat, as_of=as_of, modes=modes)
