"""Phase 0 source inventory: download every candidate SIAT table once and record its metadata.

Usage:  python tools/inventory.py            -> writes docs/inventory_siat.csv
        python tools/inventory.py --search freight   -> searches the SIAT category tree

The ids are the 15 SIAT tables in now.docx plus the candidates for the added series.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowcast.ingest.siat import _session, fetch_table, fetch_tree  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# id -> why it is on the list
CANDIDATES: dict[int, str] = {
    # the 15 SIAT sources listed in now.docx
    590: "now.docx: industrial production by activity (nominal)",
    2403: "now.docx: construction works (nominal)",
    2699: "now.docx: retail trade (nominal)",
    964: "now.docx: wholesale trade (nominal)",
    1286: "now.docx: CPI COICOP-2018, m/m",
    1581: "now.docx: PPI industrial goods, m/m",
    1103: "now.docx: exports",
    1162: "now.docx: imports",
    3218: "now.docx: market services growth by type",
    2117: "now.docx: new small businesses",
    2138: "now.docx: operating enterprises",
    1331: "now.docx: investment in fixed assets growth (quarterly)",
    2643: "now.docx: investment via FDI and loans (quarterly)",
    3973: "now.docx: total income of population (quarterly)",
    3698: "now.docx: GDP growth, production method (quarterly)",
    # candidates for the added series (task 0.3)
    562: "added: industrial physical volume index by activity (real growth)",
    2406: "added: construction growth rate (real)",
    2700: "added: retail trade growth rate (real)",
    2696: "added: wholesale trade growth rate (real)",
    3217: "added: market services volume by type (check for history before 2024)",
    2943: "added: agriculture, forestry, fishing output (quarterly)",
    2944: "added: agriculture, forestry, fishing growth (quarterly)",
    3267: "added: freight turnover (quarterly)",
    3290: "added: freight turnover growth (quarterly)",
    3278: "added: passenger turnover (quarterly)",
    3079: "added: exports by HS 2-digit (gold = HS 71)",
    3080: "added: exports by HS 2-digit, per month (gold = HS 71)",
    2413: "added: export growth rate",
    4689: "added: CPI y/y (to the same month of the previous year)",
    3699: "added: GVA growth by industry, production method (quarterly)",
}


def run_inventory(out: Path) -> None:
    s = _session()
    done: dict[int, dict] = {}
    if out.exists():  # resume: keep tables already fetched
        with out.open(encoding="utf-8") as f:
            done = {int(r["siat_id"]): r for r in csv.DictReader(f) if r.get("status") == "ok"}
    rows = []
    for i, why in CANDIDATES.items():
        if i in done:
            rows.append(done[i])
            continue
        try:
            t = fetch_table(i, s)
            p = t.period_columns
            rows.append({
                "siat_id": i,
                "why": why,
                "name": t.metadata.get("Indicator name", ""),
                "periodicity": t.metadata.get("Periodicity", ""),
                "unit": t.metadata.get("Unit of measurement", ""),
                "updated_at": t.updated_at[:10],
                "first_period": p[0] if p else "",
                "last_period": p[-1] if p else "",
                "n_items": len(t.data),
                "items": " | ".join(map(str, list(t.data.get("Klassifikator_en", []))[:8])),
                "note": t.metadata.get("Note", "")[:200],
                "file_url": t.file_url,
                "status": "ok",
            })
        except Exception as e:  # record the failure, keep going
            rows.append({"siat_id": i, "why": why, "status": f"error: {e}"})
        time.sleep(3)  # SIAT rate-limits fast callers
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["siat_id", "why", "name", "periodicity", "unit", "updated_at", "first_period",
              "last_period", "n_items", "items", "note", "file_url", "status"]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} tables)")


def search(term: str) -> None:
    def walk(n, path):
        kids = n.get("children") or []
        if not kids and term.lower() in (n.get("name_en") or "").lower():
            print(n.get("id"), n.get("code"), n.get("period"), "|", n.get("name_en"), "|", " > ".join(path))
        for k in kids:
            walk(k, path + [n.get("name_en") or ""])

    for r in fetch_tree():
        walk(r, [])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--search")
    ap.add_argument("--out", default=str(ROOT / "docs" / "inventory_siat.csv"))
    a = ap.parse_args()
    if a.search:
        search(a.search)
    else:
        run_inventory(Path(a.out))
