"""Minimal client for Uzstat's SIAT open-data API (api.siat.stat.uz).

How the API works (checked 16 Oct 2026):

* ``GET https://api.siat.stat.uz/sdmx/<id>/table/download/?download_format=json`` returns a
  small pointer object: ``{"updated_at": ..., "file": <url of the data file>, ...}``.
* The data file (``sdmx_data_<id>.json``) is a one-element list holding ``metadata`` (a list of
  name/value pairs in four languages) and ``data`` (one row per classifier item, one column per
  period such as ``2026-M08`` or ``2026-Q2``).
* ``GET https://api.siat.stat.uz/sdmx/`` returns the full category tree with every dataset id.

Phase 1 extends this into the full ingestion adapter; Phase 0 uses it for the inventory.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import pandas as pd
import requests

API = "https://api.siat.stat.uz/sdmx"
TIMEOUT = 60
LABEL_COLUMNS = ["Code", "Klassifikator", "Klassifikator_ru", "Klassifikator_en", "Klassifikator_uzc"]
PERIOD_RE = re.compile(r"^(\d{4})-(M\d{2}|Q\d|H\d)?$|^(\d{4})$")


@dataclass
class SiatTable:
    dataset_id: int
    updated_at: str
    file_url: str
    metadata: dict[str, str] = field(default_factory=dict)
    data: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def period_columns(self) -> list[str]:
        return [c for c in self.data.columns if c not in LABEL_COLUMNS]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "nowcast-uz/0.1 (+https://github.com/mrshohruh/nowcasting)"
    return s


def _get(s: requests.Session, url: str, params: dict | None = None, retries: int = 5) -> requests.Response:
    """GET with backoff. SIAT answers 429 (Too Many Requests) after roughly 15-20 quick calls,
    so every caller must pace itself; on 429 we wait Retry-After (or 15, 30, 60... seconds)."""
    wait = 15.0
    for attempt in range(retries + 1):
        r = s.get(url, params=params, timeout=TIMEOUT)
        if r.status_code != 429 or attempt == retries:
            r.raise_for_status()
            return r
        ra = r.headers.get("Retry-After")
        time.sleep(float(ra) if ra and ra.isdigit() else wait)
        wait *= 2
    raise RuntimeError("unreachable")


def fetch_pointer(dataset_id: int, fmt: str = "json", session: requests.Session | None = None) -> dict:
    s = session or _session()
    r = _get(s, f"{API}/{dataset_id}/table/download/", params={"download_format": fmt})
    return r.json()


def _metadata_dict(items: list[dict]) -> dict[str, str]:
    """Keep the first value of each English metadata name (the file repeats some entries)."""
    out: dict[str, str] = {}
    for it in items:
        name = it.get("name_en") or it.get("name_uz")
        value = it.get("value_en") or it.get("value_uz")
        if name and name not in out and value is not None:
            out[name] = str(value)
    return out


def fetch_table(dataset_id: int, session: requests.Session | None = None) -> SiatTable:
    s = session or _session()
    ptr = fetch_pointer(dataset_id, "json", s)
    r = _get(s, ptr["file"])
    payload = r.json()
    block = payload[0] if isinstance(payload, list) else payload
    df = pd.DataFrame(block.get("data", []))
    return SiatTable(
        dataset_id=dataset_id,
        updated_at=ptr.get("updated_at", ""),
        file_url=ptr["file"],
        metadata=_metadata_dict(block.get("metadata", [])),
        data=df,
    )


def fetch_tree(session: requests.Session | None = None) -> list[dict]:
    s = session or _session()
    return _get(s, f"{API}/").json()["results"]


def to_long(table: SiatTable) -> pd.DataFrame:
    """Wide SIAT table -> long rows (code, label_en, period, value)."""
    df = table.data
    periods = table.period_columns
    long = df.melt(id_vars=[c for c in LABEL_COLUMNS if c in df.columns], value_vars=periods,
                   var_name="period", value_name="value")
    long = long.rename(columns={"Code": "code", "Klassifikator_en": "label_en"})
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    return long[["code", "label_en", "period", "value"]]
