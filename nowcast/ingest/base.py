"""Fetch framework (task 1.1).

Every source is a ``Source`` subclass with two steps:

* ``fetch()`` downloads the file and returns ``Raw`` (the bytes exactly as received, plus the
  URL and anything the publisher says about the file, e.g. SIAT's ``updated_at``);
* ``parse(raw)`` turns those bytes into ``Parsed``: a long table with columns
  ``item, ref_period, value`` and a ``meta`` dict (unit as published, items found, labels) that
  the quality checks compare from run to run.

The two are separate so that a raw file saved by an earlier run, or a file dropped in the manual
inbox, goes through exactly the same parser as a fresh download.

The adapter is chosen by the catalogue's ``source`` field (``REGISTRY``). Several series can share
one file (four industry series come from SIAT table 562); ``file_key`` says which, so a run
downloads each file once.

Periods are normalised to ``YYYY-MM`` (monthly) and ``YYYYQn`` (quarterly), the string forms of
pandas monthly and quarterly periods.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import ClassVar

import pandas as pd
import requests

from nowcast.ingest import http

COLUMNS = ["item", "ref_period", "value"]

_MONTH = re.compile(r"^(\d{4})[-\s]?[MМ](\d{1,2})$")  # 2026-M08, 2010-М01 (Cyrillic), 1960M01
_MONTH_DASH = re.compile(r"^(\d{4})-(\d{2})$")  # 2026-08
_QUARTER = re.compile(r"^(\d{4})[-\s]?Q([1-4])$")  # 2026-Q2, 2026Q2


def norm_period(label: object) -> str:
    """Any period label the sources use -> 'YYYY-MM' or 'YYYYQn'. Raises on anything else."""
    s = str(label).strip()
    if isinstance(label, (dt.date, dt.datetime, pd.Timestamp)):
        return f"{label.year:04d}-{label.month:02d}"
    for rx, kind in ((_MONTH, "M"), (_MONTH_DASH, "M"), (_QUARTER, "Q")):
        m = rx.match(s)
        if m:
            y, n = int(m.group(1)), int(m.group(2))
            if kind == "M":
                if not 1 <= n <= 12:
                    break
                return f"{y:04d}-{n:02d}"
            return f"{y:04d}Q{n}"
    raise ValueError(f"unrecognised period label {label!r}")


def period_index(ref_period: str) -> int:
    """Absolute month number of the period's last month (for lag arithmetic)."""
    if "Q" in ref_period:
        y, q = ref_period.split("Q")
        return int(y) * 12 + int(q) * 3 - 1
    y, m = ref_period.split("-")
    return int(y) * 12 + int(m) - 1


@dataclass
class Raw:
    content: bytes
    ext: str  # file extension without the dot: json, xml, xlsx, csv
    url: str
    info: dict = field(default_factory=dict)


@dataclass
class Parsed:
    data: pd.DataFrame  # columns COLUMNS
    meta: dict = field(default_factory=dict)


@dataclass
class FetchContext:
    """What an adapter may need beyond its catalogue entry."""

    run_date: dt.date
    session: requests.Session = field(default_factory=http.session)
    # last stored ref_period per series id (the USD rate adapter only fetches what is missing)
    last_period: dict[str, str] = field(default_factory=dict)


class SourceError(RuntimeError):
    """A source returned something the adapter cannot use (layout change, missing key...)."""


class Source:
    name: ClassVar[str] = ""
    pause_after_fetch: ClassVar[float] = 0.0  # seconds; SIAT needs pacing

    def __init__(self, series: dict, ctx: FetchContext):
        self.series = series
        self.ctx = ctx

    @property
    def file_key(self) -> str:
        """Series with the same file_key share one download within a run."""
        return self.series["url"]

    def fetch(self) -> Raw:  # pragma: no cover - interface
        raise NotImplementedError

    def parse(self, raw: Raw) -> Parsed:  # pragma: no cover - interface
        raise NotImplementedError

    # helpers -----------------------------------------------------------------------------
    def get(self, url: str, params: dict | None = None) -> requests.Response:
        return http.get(self.ctx.session, url, params=params)

    @staticmethod
    def frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=COLUMNS)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        return df.sort_values(["item", "ref_period"]).reset_index(drop=True)


REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    REGISTRY[cls.name] = cls
    return cls


def adapter_for(series: dict, ctx: FetchContext) -> Source:
    # importing the module fills REGISTRY
    from nowcast.ingest import adapters  # noqa: F401

    try:
        cls = REGISTRY[series["source"]]
    except KeyError as e:
        raise SourceError(f"no adapter for source {series['source']!r}") from e
    return cls(series, ctx)
