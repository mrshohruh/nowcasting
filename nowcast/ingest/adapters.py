"""Source adapters (task 1.2). One class per catalogue ``source`` value.

| source         | publisher  | file                                       | rows picked by           |
| -------------- | ---------- | ------------------------------------------ | ------------------------ |
| siat           | Uzstat     | SIAT JSON data file (via the download API) | classifier Code (codes)  |
| cbu_sdmx       | CBU        | SDMX 2.1 XML at cbu.uz/sdmx/               | INDICATOR (key)          |
| cbu_xlsx       | CBU        | BoP analytical workbook                    | row label (key)          |
| cbu_rate_json  | CBU        | daily official rate archive, one call/day  | -                        |
| worldbank_xlsx | World Bank | Pink Sheet monthly workbook (link on page) | Gold column              |
| stat_nag_xlsx  | Uzstat     | quarterly national accounts workbook       | NAG item ids (codes)     |

Labels are never used to select SIAT rows, so a translation change cannot break a series; the
label of each selected code is still reported in ``meta`` and checked against ``key``.
"""

from __future__ import annotations

import calendar
import datetime as dt
import io
import json
import re
import time

import openpyxl
import pandas as pd

from nowcast.ingest import sdmx
from nowcast.ingest.base import Parsed, Raw, Source, SourceError, norm_period, register
from nowcast.transform import target

SIAT_API = "https://api.siat.stat.uz/sdmx"
SIAT_LABELS = {"Code", "Klassifikator", "Klassifikator_ru", "Klassifikator_en", "Klassifikator_uzc"}


def _norm_text(s: object) -> str:
    return " ".join(str(s or "").lower().split())


# ------------------------------------------------------------------------------------- SIAT
@register
class Siat(Source):
    """Uzstat SIAT. The download API returns a pointer {updated_at, file}; the file is a one-element
    list with ``metadata`` (name/value pairs) and ``data`` (one row per classifier code, one
    column per period such as 2026-M08 or 2026-Q2)."""

    name = "siat"
    pause_after_fetch = 3.0  # the API answers 429 after ~15-20 quick calls

    @property
    def table_id(self) -> int:
        m = re.search(r"/sdmx/(\d+)/", self.series["url"])
        if not m:
            raise SourceError(f"{self.series['id']}: no SIAT table id in url")
        return int(m.group(1))

    @property
    def file_key(self) -> str:
        return f"siat:{self.table_id}"

    def fetch(self) -> Raw:
        ptr = self.get(
            f"{SIAT_API}/{self.table_id}/table/download/", params={"download_format": "json"}
        ).json()
        if not isinstance(ptr, dict) or not ptr.get("file"):
            raise SourceError(f"SIAT {self.table_id}: pointer has no file: {str(ptr)[:200]}")
        time.sleep(1.0)
        r = self.get(ptr["file"])
        return Raw(
            r.content,
            "json",
            ptr["file"],
            info={"table": self.table_id, "updated_at": ptr.get("updated_at", "")},
        )

    def parse(self, raw: Raw) -> Parsed:
        payload = json.loads(raw.content)
        block = payload[0] if isinstance(payload, list) else payload
        rows = block.get("data") or []
        if not rows:
            raise SourceError("SIAT file has no data rows")
        meta_items = {}
        for it in block.get("metadata", []):
            name = it.get("name_en") or it.get("name_uz")
            if name and name not in meta_items:
                meta_items[name] = it.get("value_en") or it.get("value_uz")
        labels = {str(r.get("Code")): str(r.get("Klassifikator_en") or "").strip() for r in rows}
        codes = [str(c) for c in self.series.get("codes") or []]
        if not codes:
            raise SourceError(f"{self.series['id']}: catalogue entry has no 'codes'")
        wanted = list(labels) if codes == ["*"] else codes
        missing = [c for c in wanted if c not in labels]
        if missing:
            raise SourceError(f"codes {missing} not in SIAT table (found {sorted(labels)[:10]}...)")
        out = []
        for r in rows:
            code = str(r.get("Code"))
            if code not in wanted:
                continue
            for col, v in r.items():
                if col in SIAT_LABELS:
                    continue
                out.append((code, norm_period(col), v))
        return Parsed(
            self.frame(out),
            meta={
                "unit": meta_items.get("Unit of measurement"),
                "items": sorted(wanted),
                "labels": {c: labels[c] for c in sorted(wanted)},
                "updated_at": raw.info.get("updated_at") or meta_items.get("Last modified date"),
            },
        )


# ------------------------------------------------------------------------------ CBU SDMX
@register
class CbuSdmx(Source):
    name = "cbu_sdmx"
    pause_after_fetch = 1.0

    def fetch(self) -> Raw:
        r = self.get(self.series["url"])
        return Raw(r.content, "xml", self.series["url"])

    def parse(self, raw: Raw) -> Parsed:
        try:
            df = sdmx.parse(raw.content)
        except Exception as e:  # lxml raises its own error types
            raise SourceError(f"not a readable SDMX file: {e}") from e
        key = str(self.series["key"])
        if df.empty or "INDICATOR" not in df:
            raise SourceError("SDMX file has no series")
        sub = df[df["INDICATOR"] == key]
        if sub.empty:
            raise SourceError(f"INDICATOR {key} not in file")
        freqs = sorted(sub.get("FREQ", pd.Series(dtype=str)).dropna().unique())
        if freqs and freqs != [self.series["frequency"]]:
            raise SourceError(f"INDICATOR {key} has FREQ {freqs}, catalogue says {self.series['frequency']}")
        rows = [(key, norm_period(p), v) for p, v in zip(sub["TIME_PERIOD"], sub["OBS_VALUE"], strict=True)]
        mult = sorted(sub.get("UNIT_MULT", pd.Series(dtype=str)).dropna().astype(str).unique())
        return Parsed(self.frame(rows), meta={"unit": f"UNIT_MULT={','.join(mult)}", "items": [key]})


# -------------------------------------------------------------------------- CBU BoP xlsx
@register
class CbuXlsx(Source):
    """Balance of payments, analytical presentation: one sheet, periods across row 4
    ('2005-Q1'...), item labels in column A, unit ('million USD') in a title row above."""

    name = "cbu_xlsx"
    pause_after_fetch = 1.0

    def fetch(self) -> Raw:
        r = self.get(self.series["url"])
        return Raw(r.content, "xlsx", self.series["url"])

    def parse(self, raw: Raw) -> Parsed:
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw.content), read_only=True, data_only=True)
        except Exception as e:
            raise SourceError(f"not a readable xlsx: {e}") from e
        key = _norm_text(self.series["key"])
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            hdr = next(
                (
                    i
                    for i, r in enumerate(rows)
                    if sum(bool(re.match(r"^\d{4}-?Q[1-4]$", str(c or "").strip())) for c in r) >= 4
                ),
                None,
            )
            if hdr is None:
                continue
            periods = {
                j: norm_period(c)
                for j, c in enumerate(rows[hdr])
                if re.match(r"^\d{4}-?Q[1-4]$", str(c or "").strip())
            }
            hit = next((r for r in rows[hdr + 1 :] if _norm_text(r[0]) == key), None)
            if hit is None:
                continue
            titles = [str(r[0]).strip() for r in rows[:hdr] if r and r[0]]
            unit = next((t for t in titles if re.search(r"million|thousand|billion|usd|uzs", t, re.I)), None)
            item = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
            data = [(item, p, hit[j] if j < len(hit) else None) for j, p in periods.items()]
            return Parsed(self.frame(data), meta={"unit": unit, "items": [item], "sheet": ws.title})
        raise SourceError(f"row {self.series['key']!r} not found under a quarterly header")


# ----------------------------------------------------------------------- CBU daily rate
@register
class CbuRate(Source):
    """Official UZS/USD rate. The archive answers one calendar day per call with the rate in
    force that day and the date it was set (``Date``). The monthly value is the mean over the
    calendar days of the month, and is only produced for complete months.

    A run fetches every day from the first day of the last stored month (to re-check it)
    up to the end of the previous month, and at least the last two complete months. The full
    history since 2018 was back-filled once (data/seed/usd_rate_daily_*.csv).
    """

    name = "cbu_rate_json"
    max_months = 36  # guard: never start a multi-thousand-call back-fill inside a monthly run

    @property
    def file_key(self) -> str:
        return "cbu_rate:USD"

    def months_to_fetch(self) -> list[tuple[int, int]]:
        run = self.ctx.run_date
        end = run.year * 12 + run.month - 2  # previous month, as absolute month index
        last = self.ctx.last_period.get(self.series["id"])
        if last:
            y, m = map(int, last.split("-"))
            start = min(y * 12 + m - 1, end - 1)
        else:
            y, m = map(int, str(self.series["first_period"]).split("-"))
            start = y * 12 + m - 1
        start = max(start, end - self.max_months + 1)
        return [(i // 12, i % 12 + 1) for i in range(start, end + 1)]

    def fetch(self) -> Raw:
        lines = ["date,rate,rate_date"]
        for y, m in self.months_to_fetch():
            for d in range(1, calendar.monthrange(y, m)[1] + 1):
                day = dt.date(y, m, d)
                url = self.series["url"].replace("{YYYY-MM-DD}", day.isoformat())
                j = self.get(url).json()
                if not j or "Rate" not in j[0]:
                    raise SourceError(f"no rate for {day}: {str(j)[:120]}")
                set_on = dt.datetime.strptime(j[0]["Date"], "%d.%m.%Y").date()
                lines.append(f"{day.isoformat()},{j[0]['Rate']},{set_on.isoformat()}")
        return Raw(
            ("\n".join(lines) + "\n").encode(), "csv", self.series["url"], info={"days": len(lines) - 1}
        )

    def parse(self, raw: Raw) -> Parsed:
        df = pd.read_csv(io.BytesIO(raw.content), dtype={"date": str, "rate_date": str})
        if not {"date", "rate"} <= set(df.columns):
            raise SourceError("rate file needs columns date,rate")
        df["date"] = pd.to_datetime(df["date"])
        df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
        df = df.dropna(subset=["rate"]).drop_duplicates("date", keep="last")
        df["m"] = df["date"].dt.to_period("M")
        g = df.groupby("m")["rate"].agg(["mean", "count"])
        days = pd.Series([p.days_in_month for p in g.index], index=g.index)
        full = g[g["count"] == days]
        rows = [("USD", str(p), v) for p, v in full["mean"].items()]
        return Parsed(
            self.frame(rows),
            meta={
                "unit": "UZS per USD, mean of calendar days",
                "items": ["USD"],
                "incomplete_months": [str(p) for p in g.index.difference(full.index)],
            },
        )


# ---------------------------------------------------------------- World Bank Pink Sheet
PINK_LINK = re.compile(r'href="([^"]*CMO-Historical-Data-Monthly\.xlsx)"', re.I)


@register
class WorldBank(Source):
    """The commodity-markets page links the monthly workbook; its URL changes every month, so the
    link is read from the page. Sheet 'Monthly Prices': dates in column A ('1960M01'), commodity
    names in a header row, units in the row below."""

    name = "worldbank_xlsx"

    @property
    def file_key(self) -> str:
        return "worldbank:pinksheet"

    def fetch(self) -> Raw:
        page = self.get(self.series["url"])
        m = PINK_LINK.search(page.text)
        if not m:
            raise SourceError("monthly Pink Sheet xlsx link not found on the page")
        href = m.group(1)
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://www.worldbank.org" + href
        r = self.get(href)
        return Raw(r.content, "xlsx", href, info={"page": self.series["url"]})

    def parse(self, raw: Raw) -> Parsed:
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw.content), read_only=True, data_only=True)
        except Exception as e:
            raise SourceError(f"not a readable xlsx: {e}") from e
        if "Monthly Prices" not in wb.sheetnames:
            raise SourceError(f"no 'Monthly Prices' sheet (sheets: {wb.sheetnames})")
        rows = list(wb["Monthly Prices"].iter_rows(values_only=True))
        first = next(
            (i for i, r in enumerate(rows) if r and re.match(r"^\d{4}M\d{2}$", str(r[0] or "").strip())), None
        )
        if first is None:
            raise SourceError("no monthly dates (YYYYMmm) in column A")
        col = unit = None
        for i in range(max(0, first - 8), first):
            for j, c in enumerate(rows[i]):
                if _norm_text(c) == "gold":
                    col = j
                    below = [rows[k][j] for k in range(i + 1, first) if j < len(rows[k]) and rows[k][j]]
                    unit = str(below[0]).strip() if below else None
                    break
            if col is not None:
                break
        if col is None:
            raise SourceError("no 'Gold' column in the header rows")
        data = []
        for r in rows[first:]:
            lab = str(r[0] or "").strip()
            if re.match(r"^\d{4}M\d{2}$", lab) and col < len(r):
                data.append(("GOLD", norm_period(lab), r[col]))
        return Parsed(self.frame(data), meta={"unit": unit, "items": ["GOLD"]})


# ------------------------------------------------------------------------ Uzstat NAG xlsx
@register
class StatNag(Source):
    """Quarterly national accounts workbook. Items are the NAG rows in
    ``nowcast.transform.target.ITEMS``, each stored three times: ``<item>.N`` (current prices),
    ``<item>.R`` (previous-year prices), ``<item>.G`` (published growth, %). All cumulative."""

    name = "stat_nag_xlsx"

    def fetch(self) -> Raw:
        r = self.get(self.series["url"])
        return Raw(r.content, "xlsx", self.series["url"])

    def parse(self, raw: Raw) -> Parsed:
        try:
            nag = target.read_nag(raw.content)
        except (ValueError, KeyError, StopIteration) as e:
            raise SourceError(f"NAG layout changed: {e}") from e
        known = [k for k, _ in target.ITEMS]
        codes = [str(c) for c in self.series.get("codes") or []]
        wanted = known if codes == ["*"] else codes
        bad = [c for c in wanted if c not in known]
        if not codes or bad:
            raise SourceError(f"{self.series['id']}: bad NAG codes {bad or codes}")
        rows = []
        for block, df in nag.items():
            for item in wanted:
                for q, v in df.loc[item].items():
                    rows.append((f"{item}.{block}", norm_period(q), v))
        return Parsed(
            self.frame(rows),
            meta={
                "unit": "N: bn UZS current prices; R: bn UZS previous-year prices; G: % (all cumulative)",
                "items": sorted(f"{i}.{b}" for i in wanted for b in nag),
            },
        )
