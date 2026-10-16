"""Task 0.4: can a GitHub-hosted runner download from every source host?

Runs one request per host and writes a markdown table. Used by
.github/workflows/reachability.yml, and can be run locally:  python tools/reachability.py out.md
"""

from __future__ import annotations

import datetime as dt
import sys
import time

import requests

CHECKS = [
    ("api.siat.stat.uz", "SIAT pointer (industry, 590)",
     "https://api.siat.stat.uz/sdmx/590/table/download/?download_format=json"),
    ("api.siat.stat.uz", "SIAT data file (industry, 590)",
     "https://api.siat.stat.uz/media/uploads/sdmx/sdmx_data_590.json"),
    ("siat.stat.uz", "SIAT web page", "https://siat.stat.uz/data/590/?lang=en"),
    ("stat.uz", "NAG national accounts xlsx",
     "https://stat.uz/img/uploads/download_xlsx/nag_uzbekistan_mcd.xlsx"),
    ("nsdp.stat.uz", "NSDP page", "https://nsdp.stat.uz/"),
    ("cbu.uz", "M2 SDMX (DCS)", "https://cbu.uz/sdmx/DCS_Uzbekistan_Online.xml"),
    ("cbu.uz", "BoP analytical xlsx (remittances)",
     "https://cbu.uz/sdmx/public/BOP_Analytical_Uzbekistan.xlsx"),
    ("cbu.uz", "Official USD rate JSON", "https://cbu.uz/en/arkhiv-kursov-valyut/json/USD/2026-08-31/"),
    ("www.worldbank.org", "Commodity markets page", "https://www.worldbank.org/en/research/commodity-markets"),
    ("thedocs.worldbank.org", "Pink Sheet monthly xlsx",
     "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
     "CMO-Historical-Data-Monthly.xlsx"),
]


def main(out_path: str) -> None:
    s = requests.Session()
    s.headers["User-Agent"] = "nowcast-uz/0.1 (+https://github.com/mrshohruh/nowcasting)"
    lines = [
        f"Run at {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC from a GitHub-hosted runner.",
        "",
        "| Host | What | HTTP status | Size (KB) | Seconds | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for host, what, url in CHECKS:
        t0 = time.time()
        try:
            r = s.get(url, timeout=90)
            code, size = r.status_code, len(r.content) / 1024
            ok = "ok" if r.ok and size > 0.2 else "FAIL"
        except Exception as e:  # timeouts, DNS, TLS
            code, size, ok = type(e).__name__, 0.0, "FAIL"
        lines.append(f"| {host} | {what} | {code} | {size:.0f} | {time.time() - t0:.1f} | {ok} |")
        time.sleep(3)
    text = "\n".join(lines) + "\n"
    print(text)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "reachability_result.md")
