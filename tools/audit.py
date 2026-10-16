"""Task 0.6: audit the seed workbook. Prints a per-series table used in docs/data_audit.md.

    python tools/audit.py  -> docs/audit_monthly.csv, docs/audit_quarterly.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SEED = ROOT / "data" / "seed" / "uzbekistan_macro_monthly_quarterly_2026-10-14.xlsx"


def classify_monthly(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"form_detected": "empty"}
    years = s.index.year
    # cumulative within year: never falls inside a year, and drops at most Januaries
    falls_inside = 0
    jan_drops = 0
    for y in sorted(set(years)):
        v = s[years == y]
        d = v.diff().dropna()
        falls_inside += int((d < -1e-9).sum())
    jan = s[s.index.month == 1]
    for t in jan.index:
        prev = t - pd.offsets.MonthBegin(1)
        if prev in s.index and s[t] < s[prev]:
            jan_drops += 1
    n_years = len(set(years))
    looks_cum = falls_inside == 0 and jan_drops >= max(1, n_years - 2)
    near_100 = s.between(80, 130).mean() > 0.95
    if (s == 0).all():
        form = "all zero"
    elif looks_cum:
        form = "cumulative YTD"
    elif near_100:
        form = "index or growth rate (~100)"
    else:
        form = "level / rate"
    full = pd.date_range(s.index.min(), s.index.max(), freq="MS")
    return {
        "form_detected": form,
        "first": s.index.min().strftime("%Y-%m"),
        "last": s.index.max().strftime("%Y-%m"),
        "n": len(s),
        "gaps_inside": len(full) - len(s),
        "falls_within_year": falls_inside,
        "min": round(float(s.min()), 2),
        "max": round(float(s.max()), 2),
    }


def main() -> None:
    m = pd.read_excel(SEED, sheet_name="Monthly_Data", index_col=0)
    m.index = pd.to_datetime(m.index)
    rows = []
    for c in m.columns:
        r = {"series": c}
        r.update(classify_monthly(m[c]))
        rows.append(r)
    am = pd.DataFrame(rows)
    am.to_csv(ROOT / "docs" / "audit_monthly.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    pd.set_option("display.max_rows", 200)
    print(am.to_string())

    q = pd.read_excel(SEED, sheet_name="Quarterly_Data", index_col=0)
    qrows = []
    for c in q.columns:
        s = q[c].dropna()
        idx = pd.PeriodIndex(s.index, freq="Q")
        rises_q2_q4 = np.mean([
            all(np.diff(s[idx.year == y].values) > 0) for y in sorted(set(idx.year))
            if (idx.year == y).sum() == 4
        ]) if len(s) >= 8 else np.nan
        qrows.append({"series": c, "first": str(s.index.min()), "last": str(s.index.max()),
                      "n": len(s), "share_of_years_rising_Q1_to_Q4": round(float(rises_q2_q4), 2),
                      "Q1..Q4 of last full year": list(np.round(s[idx.year == 2025].values, 1))})
    aq = pd.DataFrame(qrows)
    aq.to_csv(ROOT / "docs" / "audit_quarterly.csv", index=False)
    print(aq.to_string())


if __name__ == "__main__":
    main()
