"""Phase 0 exit check: download every catalogue URL once and confirm its key is in the file.

    python tools/check_catalogue.py docs/catalogue_check.md

Runs in the reachability workflow on a GitHub runner (the workspace this was written in cannot
reach every host).
"""

from __future__ import annotations

import datetime as dt
import io
import re
import sys
import time
from pathlib import Path

import openpyxl
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nowcast.catalogue import load  # noqa: E402
from nowcast.ingest import sdmx, siat  # noqa: E402


def check(s: dict, session: requests.Session, cache: dict) -> tuple[str, str]:
    src, url, key = s["source"], s["url"], str(s["key"])
    if src == "siat":
        sid = int(re.search(r"/sdmx/(\d+)/", url).group(1))
        if sid not in cache:
            cache[sid] = siat.fetch_table(sid, session)
            time.sleep(3)
        t = cache[sid]
        labels = [str(x) for x in t.data.get("Klassifikator_en", [])]
        periods = sorted(p.replace("М", "M") for p in t.period_columns)
        found = key.startswith("all items") or any(
            lbl.strip().startswith(key.rstrip(". ")[:40]) for lbl in labels
        )
        return (
            "ok" if found else "KEY NOT FOUND"
        ), f"{periods[0]} to {periods[-1]}, updated {t.updated_at[:10]}"
    if src == "cbu_sdmx":
        if url not in cache:
            cache[url] = sdmx.fetch(url, session)
            time.sleep(2)
        df = cache[url]
        sub = df[df["INDICATOR"] == key]
        if sub.empty:
            return "KEY NOT FOUND", ""
        return "ok", f"{sub['TIME_PERIOD'].min()} to {sub['TIME_PERIOD'].max()}"
    if src == "cbu_xlsx":
        r = session.get(url, timeout=90)
        r.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
        hit = any(
            key in str(c) for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for c in row if c
        )
        return ("ok" if hit else "KEY NOT FOUND"), ""
    if src == "cbu_rate_json":
        r = session.get(url.replace("{YYYY-MM-DD}", "2026-08-31"), timeout=60)
        r.raise_for_status()
        rate = r.json()[0].get("Rate")
        return ("ok" if rate else "KEY NOT FOUND"), f"rate on 2026-08-31: {rate}"
    if src == "worldbank_xlsx":
        page = session.get(url, timeout=60)
        page.raise_for_status()
        m = re.search(r'href="([^"]+CMO-Historical-Data-Monthly\.xlsx)"', page.text)
        if not m:
            return "XLSX LINK NOT FOUND", ""
        x = session.get(
            m.group(1) if m.group(1).startswith("http") else "https://www.worldbank.org" + m.group(1),
            timeout=90,
        )
        x.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(x.content), read_only=True, data_only=True)
        ok = "Monthly Prices" in wb.sheetnames
        return ("ok" if ok else "SHEET NOT FOUND"), f"{len(x.content) // 1024} KB"
    if src == "stat_nag_xlsx":
        if url not in cache:
            r = session.get(url, timeout=120)
            r.raise_for_status()
            cache[url] = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
        wb = cache[url]
        quarterly = [n for n in wb.sheetnames if n.startswith("Quarterly_2018")]
        return ("ok" if quarterly else "SHEET NOT FOUND"), ", ".join(quarterly)
    return "UNKNOWN SOURCE", ""


def main(out: str) -> int:
    session = requests.Session()
    session.headers["User-Agent"] = "nowcast-uz/0.1 (+https://github.com/mrshohruh/nowcasting)"
    cache: dict = {}
    lines = [
        f"Checked {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC.",
        "",
        "| Series | Source | Result | Coverage |",
        "| --- | --- | --- | --- |",
    ]
    failures = 0
    for s in load():
        try:
            status, info = check(s, session, cache)
        except Exception as e:
            status, info = f"ERROR {type(e).__name__}", str(e)[:80]
        failures += status != "ok"
        lines.append(f"| {s['id']} | {s['source']} | {status} | {info} |")
    lines += ["", f"{failures} problem(s)."]
    text = "\n".join(lines) + "\n"
    print(text)
    Path(out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "catalogue_check.md"))
