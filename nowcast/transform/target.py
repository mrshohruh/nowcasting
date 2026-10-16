"""GDP target (Phase 0, task 0.7).

Uzstat publishes quarterly GDP cumulatively (Q1, Jan-Jun, Jan-Sep, Jan-Dec) in two price bases:
current prices N and previous-year prices R. For quarter q of year t:

    cumulative growth  G_q,t = R_cum_q,t / N_cum_q,t-1                       (Uzstat's headline)
    discrete growth    g_q,t = (R_cum_q,t - R_cum_q-1,t) / (N_cum_q,t-1 - N_cum_q-1,t-1)

The NAG file only has current prices from 2018 on the 2018 basis, so last year's nominal values
for 2018 are recovered from the published percent block:  N_cum_q,2017 = R_cum_q,2018 / G_q,2018.

The two forms convert exactly: cumulative growth is the weighted mean of discrete growth with
weights = last year's nominal value of each quarter (``cumulative_from_discrete``).

Run ``python -m nowcast.transform.target`` to rebuild ``data/derived/gdp_target.csv``.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SEED_NAG = ROOT / "data" / "seed" / "nag_uzbekistan_mcd_2026-10-14.xlsx"
OUT = ROOT / "data" / "derived" / "gdp_target.csv"
SHEET = "Quarterly_2018-2026"

# Rows of the production block, in the order Uzstat prints them, with a keyword that must appear
# in the label (guards against a silent layout change).
ITEMS: list[tuple[str, str]] = [
    ("gdp", "gdp at"),
    ("gva", "gross value added"),
    ("agriculture", "agriculture"),
    ("industry", "industry"),
    ("mining", "mining"),
    ("manufacturing", "manufacturing"),
    ("electricity", "electricity"),
    ("water", "water supply"),
    ("construction", "construction"),
    ("trade_accommodation", "trade, accommodation"),
    ("trade", "wholesale and retail"),
    ("accommodation", "accommodation and food"),
    ("transport_ict", "transportation and storage, information"),
    ("transport", "transportation and storage"),
    ("ict", "information and communication"),
    ("other_services", "other branches"),
    ("net_taxes", "net taxes"),
]
BLOCK_HEADERS = {
    "N": "gdp at current prices calculated from the production side",
    "R": "gdp at constant prices calculated from the production side,  previous year prices",
    "G": "gdp at constant prices calculated from the production side, pevious year prices, percent",
}


def _norm(s: object) -> str:
    return " ".join(str(s or "").lower().split())


def read_nag(src: Path | bytes = SEED_NAG) -> dict[str, pd.DataFrame]:
    """Return {'N','R','G'}: DataFrames indexed by item id, columns = quarters ('2018Q1'...).

    ``src`` is a path or the downloaded bytes of nag_uzbekistan_mcd.xlsx.
    """
    wb = openpyxl.load_workbook(io.BytesIO(src) if isinstance(src, bytes) else src,
                                read_only=True, data_only=True)
    rows = list(wb[SHEET].iter_rows(values_only=True))
    header = next(r for r in rows if r[1] and str(r[1]).startswith("2018-Q1"))
    quarters = [str(c).replace("-", "") for c in header[1:] if c]
    out: dict[str, pd.DataFrame] = {}
    for key, head in BLOCK_HEADERS.items():
        target = " ".join(head.split())
        start = next(i for i, r in enumerate(rows) if _norm(r[0]).startswith(target))
        block = {}
        for k, (item, kw) in enumerate(ITEMS):
            label = _norm(rows[start + k][0])
            if kw not in label:
                raise ValueError(f"NAG layout changed: block {key} row {k} is {label!r}, expected {kw!r}")
            block[item] = [float(v) if v is not None else np.nan
                           for v in rows[start + k][1 : 1 + len(quarters)]]
        out[key] = pd.DataFrame(block, index=quarters).T
    return out


def _decumulate(cum: pd.Series) -> pd.Series:
    """Cumulative-within-year quarterly series -> discrete quarters."""
    q = pd.PeriodIndex(cum.index, freq="Q")
    df = pd.DataFrame({"v": cum.values, "y": q.year, "q": q.quarter}, index=cum.index)
    prev = df.groupby("y")["v"].shift(1)
    return (df["v"] - prev.where(df["q"] > 1, 0.0)).rename(cum.name)


def build_target(nag: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    nag = nag or read_nag()
    N, R, G = nag["N"], nag["R"], nag["G"]
    quarters = list(N.columns)
    rows = []
    for item in N.index:
        n_cum, r_cum, g_pub = N.loc[item], R.loc[item], G.loc[item]
        # last year's cumulative nominal value, quarter by quarter
        n_prev_cum = pd.Series(np.nan, index=quarters)
        for qt in quarters:
            y, q = int(qt[:4]), qt[4:]
            prev = f"{y - 1}{q}"
            if prev in n_cum.index:
                n_prev_cum[qt] = n_cum[prev]
            else:  # 2018: back out from the published growth rate
                n_prev_cum[qt] = r_cum[qt] / (g_pub[qt] / 100.0)
        r_disc = _decumulate(r_cum)
        n_prev_disc = _decumulate(n_prev_cum)
        for qt in quarters:
            y = int(qt[:4])
            source = "back-calculated" if y == 2018 else "nag"
            rows.append({
                "item": item,
                "quarter": qt,
                "nominal_cum": n_cum[qt],
                "real_prev_prices_cum": r_cum[qt],
                "nominal_prev_year_cum": n_prev_cum[qt],
                "growth_cum_published": g_pub[qt],
                "growth_cum_computed": 100.0 * r_cum[qt] / n_prev_cum[qt],
                "real_prev_prices_disc": r_disc[qt],
                "nominal_prev_year_disc": n_prev_disc[qt],
                "growth_disc": 100.0 * r_disc[qt] / n_prev_disc[qt],
                "prev_year_source": source,
            })
    return pd.DataFrame(rows)


def cumulative_from_discrete(growth_disc: pd.Series, weights_prev_nominal: pd.Series) -> pd.Series:
    """Re-aggregate discrete-quarter growth (index = 'YYYYQn') into Uzstat's cumulative growth.

    weights_prev_nominal: last year's nominal value of each discrete quarter.
    """
    df = pd.DataFrame({"g": growth_disc, "w": weights_prev_nominal})
    df["y"] = [int(i[:4]) for i in df.index]
    df["gw"] = df["g"] * df["w"]
    cum = df.groupby("y")[["gw", "w"]].cumsum()
    return cum["gw"] / cum["w"]


if __name__ == "__main__":
    t = build_target()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT, index=False, float_format="%.6f")
    gdp = t[t["item"] == "gdp"].set_index("quarter")
    print(gdp[["growth_cum_published", "growth_cum_computed", "growth_disc"]].round(2).tail(10))
    print(f"wrote {OUT}")
