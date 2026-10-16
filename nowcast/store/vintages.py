"""Vintage store (task 1.3).

Everything lives under one folder, which on GitHub is the ``data`` branch:

    raw/<source>/<series_id>/<run_id>.<ext>   every file exactly as downloaded
    raw/index.csv                             one line per raw file: run, series, path, sha256, url
    observations/<run_id>.parquet             what changed in that run (see below)
    meta/<series_id>.json                     unit, items and labels last accepted (for checks)
    runs/<run_id>/report.json                 status of every series in the run
    runs/<run_id>/changes.json                new, revised and removed periods (change detector)
    latest.csv                                today's data, one row per series/item/period
    inbox/<series_id>/                        manual fallback files (see docs/runbook_ingest.md)

Observations are stored as deltas: a run writes a row only for a (series, item, period) whose
value is new or different from what the store knew, and a ``removed`` row for a period that
disappeared from the source. So ``observations`` holds the full revision history while each run
file stays small, and nothing already written is ever rewritten (git keeps it cheaply, and two
runs can never corrupt each other's data). Raw files that did not change cost nothing in git
either, because git stores identical content once.

DuckDB reads the parquet files directly:

* ``observations``  every row ever written:
  ``series_id, item, ref_period, value, vintage_date, source_hash, fetched_at, run_id, removed``
* ``as_of(d)``      a table macro: the value of every period as the store knew it on date ``d``
* ``latest``        ``as_of`` today
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from nowcast.ingest.base import Raw

OBS_COLUMNS = [
    "series_id",
    "item",
    "ref_period",
    "value",
    "vintage_date",
    "source_hash",
    "fetched_at",
    "run_id",
    "removed",
]
INDEX_COLUMNS = ["run_id", "series_id", "source", "path", "sha256", "bytes", "url", "fetched_at"]


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.obs_dir = self.root / "observations"
        self.meta_dir = self.root / "meta"
        self.runs_dir = self.root / "runs"
        self.inbox_dir = self.root / "inbox"
        for d in (self.raw_dir, self.obs_dir, self.meta_dir, self.runs_dir, self.inbox_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ reading (DuckDB)
    def is_empty(self) -> bool:
        return not any(self.obs_dir.glob("*.parquet"))

    def connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        if self.is_empty():
            con.execute("""CREATE TABLE observations (series_id VARCHAR, item VARCHAR, ref_period VARCHAR,
                           value DOUBLE, vintage_date DATE, source_hash VARCHAR, fetched_at TIMESTAMP,
                           run_id VARCHAR, removed BOOLEAN)""")
        else:
            glob = str(self.obs_dir / "*.parquet").replace("'", "''")
            con.execute(
                f"CREATE VIEW observations AS SELECT * FROM read_parquet('{glob}', union_by_name = true)"
            )
        con.execute("""
            CREATE MACRO as_of(d) AS TABLE
            SELECT series_id, item, ref_period, value, vintage_date, run_id, source_hash, fetched_at
            FROM (SELECT *, row_number() OVER (PARTITION BY series_id, item, ref_period
                                               ORDER BY vintage_date DESC, fetched_at DESC) AS rn
                  FROM observations WHERE vintage_date <= CAST(d AS DATE))
            WHERE rn = 1 AND NOT removed
        """)
        con.execute("CREATE VIEW latest AS SELECT * FROM as_of(DATE '9999-12-31')")
        return con

    def as_of(self, date: dt.date | str, series_ids: list[str] | None = None) -> pd.DataFrame:
        """Values as the store knew them on ``date`` (inclusive)."""
        with self.connect() as con:
            q = "SELECT * FROM as_of(?::DATE)"
            params: list = [str(date)]
            if series_ids is not None:
                q += " WHERE list_contains(?, series_id)"
                params.append(list(series_ids))
            return con.execute(q + " ORDER BY series_id, item, ref_period", params).df()

    def latest(self, series_ids: list[str] | None = None) -> pd.DataFrame:
        return self.as_of("9999-12-31", series_ids)

    def last_periods(self) -> dict[str, str]:
        with self.connect() as con:
            rows = con.execute("SELECT series_id, max(ref_period) FROM latest GROUP BY 1").fetchall()
        return dict(rows)

    def runs(self) -> list[str]:
        return sorted(p.name for p in self.runs_dir.iterdir() if (p / "report.json").exists())

    def report(self, run_id: str) -> dict:
        return json.loads((self.runs_dir / run_id / "report.json").read_text(encoding="utf-8"))

    # ------------------------------------------------------------------------- writing
    def new_run_id(self, date: dt.date) -> str:
        """``YYYY-MM-DD``; a second run on the same day gets ``YYYY-MM-DD.2`` and so on."""
        base, n = date.isoformat(), 1
        run_id = base
        while (self.runs_dir / run_id).exists() or (self.obs_dir / f"{run_id}.parquet").exists():
            n += 1
            run_id = f"{base}.{n}"
        return run_id

    def save_raw(self, run_id: str, series: dict, raw: Raw, fetched_at: dt.datetime) -> tuple[str, str]:
        digest = sha256(raw.content)
        rel = Path("raw") / series["source"] / series["id"] / f"{run_id}.{raw.ext}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.content)
        index = self.raw_dir / "index.csv"
        new = not index.exists()
        with index.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(INDEX_COLUMNS)
            w.writerow(
                [
                    run_id,
                    series["id"],
                    series["source"],
                    rel.as_posix(),
                    digest,
                    len(raw.content),
                    raw.url,
                    fetched_at.isoformat(timespec="seconds"),
                ]
            )
        return rel.as_posix(), digest

    def write_observations(self, run_id: str, rows: pd.DataFrame) -> Path | None:
        """Append one run's delta rows. Never overwrites an existing run file."""
        if rows.empty:
            return None
        path = self.obs_dir / f"{run_id}.parquet"
        if path.exists():
            raise FileExistsError(path)
        df = rows[OBS_COLUMNS].copy()
        df["vintage_date"] = pd.to_datetime(df["vintage_date"]).dt.date
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True).dt.tz_localize(None)
        df["value"] = df["value"].astype("float64")
        df["removed"] = df["removed"].astype(bool)
        df.to_parquet(path, index=False)
        return path

    def load_meta(self, series_id: str) -> dict | None:
        p = self.meta_dir / f"{series_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def save_meta(self, series_id: str, meta: dict) -> None:
        (self.meta_dir / f"{series_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    def write_run_file(self, run_id: str, name: str, obj: dict) -> Path:
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
        return p

    def export_latest_csv(self) -> Path:
        p = self.root / "latest.csv"
        df = self.latest()
        df[["series_id", "item", "ref_period", "value", "vintage_date", "run_id"]].to_csv(
            p, index=False, float_format="%.10g"
        )
        return p
