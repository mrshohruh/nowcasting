"""Parser for the SDMX 2.1 structure-specific XML files listed on Uzstat's NSDP page
(https://nsdp.stat.uz/). Central bank files live at https://cbu.uz/sdmx/<name>.xml.

Each <Series> element carries its key as attributes (e.g. FREQ, INDICATOR, UNIT...),
and each <Obs> carries TIME_PERIOD and OBS_VALUE.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests
from lxml import etree

TIMEOUT = 90


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse(xml: bytes | str | Path) -> pd.DataFrame:
    """Return one row per observation: series attributes + TIME_PERIOD + OBS_VALUE."""
    if isinstance(xml, Path) or (isinstance(xml, str) and not xml.lstrip().startswith("<")):
        root = etree.parse(str(xml)).getroot()
    else:
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode())
    rows = []
    for series in root.iter():
        if _local(series.tag) != "Series":
            continue
        key = dict(series.attrib)
        for obs in series:
            if _local(obs.tag) != "Obs":
                continue
            row = dict(key)
            row.update(obs.attrib)
            rows.append(row)
    df = pd.DataFrame(rows)
    if "OBS_VALUE" in df:
        df["OBS_VALUE"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
    return df


def fetch(url: str, session: requests.Session | None = None) -> pd.DataFrame:
    s = session or requests.Session()
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return parse(r.content)


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    """One row per series key: first/last period and count."""
    key_cols = [c for c in df.columns if c not in ("TIME_PERIOD", "OBS_VALUE", "OBS_STATUS",
                                                   "COMMENT", "OBS_CONF")]
    return (df.groupby(key_cols, dropna=False)["TIME_PERIOD"]
              .agg(first="min", last="max", n="count").reset_index())
