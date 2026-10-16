"""Seed history (task 1.4).

An empty store is started from the files the project began with, so it has full history before
the first download:

* vintage 2026-10-14: the two uploaded workbooks (``data/seed/``). Every column used here was
  checked against the live source on 16 Oct 2026 and is identical (``docs/seed_check.md``).
  The NAG workbook goes through the same parser as a download.
* vintage 2026-10-16: the official UZS/USD rate for every calendar day since 1 Jan 2018,
  downloaded once from the central bank archive (the monthly run then fetches only new months).

Seed columns that no catalogue series uses (industry sub-sectors, CPI and PPI groups) are not
loaded: the raw SIAT files kept from every run hold them if Phase 3 needs them.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandas as pd

from nowcast.catalogue import ROOT
from nowcast.catalogue import load as load_catalogue
from nowcast.ingest.base import FetchContext, Raw, adapter_for, norm_period
from nowcast.store.changes import compare, delta_rows
from nowcast.store.vintages import Store, sha256

SEED_DIR = ROOT / "data" / "seed"
WORKBOOK = SEED_DIR / "uzbekistan_macro_monthly_quarterly_2026-10-14.xlsx"
NAG = SEED_DIR / "nag_uzbekistan_mcd_2026-10-14.xlsx"
USD_DAILY = SEED_DIR / "usd_rate_daily_2026-10-16.csv"
SEED_DATES = {"workbooks": dt.date(2026, 10, 14), "usd": dt.date(2026, 10, 16)}

# (sheet, column prefix, series id, item). Market services are added below from their labels.
COLUMNS: list[tuple[str, str, str, str]] = [
    ("Monthly_Data", "Industrial production — Industrial production (", "ind_total_nom", "B, C, D, E"),
    ("Monthly_Data", "Construction works", "constr_nom", "1700"),
    ("Monthly_Data", "Retail trade volume", "retail_nom", "1700"),
    ("Monthly_Data", "Wholesale trade volume", "wholesale_nom", "1700"),
    ("Monthly_Data", "CPI — Cumulative index", "cpi_mm", "1"),
    ("Monthly_Data", "PPI — Composite index", "ppi_mm", "1000"),
    ("Monthly_Data", "Export volume", "exports_usd", "1700"),
    ("Monthly_Data", "Import volume", "imports_usd", "1700"),
    ("Monthly_Data", "Gold (USD/troy oz)", "gold_price", "GOLD"),
    ("Monthly_Data", "M2", "m2", "FDSB_XDC"),
    ("Monthly_Data", "International reserves — Foreign", "reserves_fx", "UZB_ORA_RAFAFX_USD"),
    ("Monthly_Data", "International reserves — Gold", "reserves_gold", "UZB_ORA_RAFAGOLD_USD"),
    ("Monthly_Data", "Small business entities", "new_small_business", "1700"),
    ("Monthly_Data", "Operating enterprises", "operating_enterprises", "1700"),
    ("Monthly_Data", "Interest rates — Time deposits of individuals", "rate_deposit_hh", "FITD_NC_HH_WA_PA"),
    ("Monthly_Data", "Interest rates — Time deposits of legal", "rate_deposit_firms", "FITD_NC_B_WA_PA"),
    ("Monthly_Data", "Interest rates — Loans extended to individuals", "rate_loan_hh", "FILR_NC_HH_WA_PA"),
    ("Monthly_Data", "Interest rates — Loans extended to legal", "rate_loan_firms", "FILR_NC_B_WA_PA"),
    ("Quarterly_Data", "Remittance", "remittances_q", "secondary_income_credits"),
    ("Quarterly_Data", "Average Nominal Wages", "wages_q", "1700"),
    ("Quarterly_Data", "Investments in fixed assets", "invest_growth_q", "1700"),
    ("Quarterly_Data", "Investment financed through FDI", "invest_fdi_q", "1700"),
    ("Quarterly_Data", "Total income of the population", "income_q", "1700"),
    ("Quarterly_Data", "Growth rates of Gross domestic product", "gdp_growth_q", "1700"),
]
# SIAT 3218 codes for the 14 market-service labels in the workbook
SERVICES = {
    "Trade": "G",
    "Information and communication": "J",
    "Transport activities": "H",
    "of which: motor transport": "493-494",
    "Accommodation and food service activities": "I",
    "Financial activities": "K",
    "Real estate activities": "L",
    "Architectural and engineering activities, technical testing and analysis": "71",
    "Renting and leasing": "77",
    "Education": "P",
    "Human health activities": "Q",
    "Repair of computers and household goods": "95",
    "Personal": "96",
    "Other services": "100",
}


def workbook_series(path: Path = WORKBOOK) -> dict[str, pd.DataFrame]:
    """Seed workbook -> {series_id: DataFrame(item, ref_period, value)}."""
    sheets = {n: pd.read_excel(path, sheet_name=n, index_col=0) for n in ("Monthly_Data", "Quarterly_Data")}
    for df in sheets.values():
        df.index = [norm_period(i) for i in df.index]
    parts: dict[str, list[pd.DataFrame]] = {}

    def add(sid: str, item: str, col: pd.Series) -> None:
        s = col.dropna()
        parts.setdefault(sid, []).append(
            pd.DataFrame({"item": item, "ref_period": s.index, "value": s.values})
        )

    for sheet, prefix, sid, item in COLUMNS:
        cols = [c for c in sheets[sheet].columns if str(c).startswith(prefix)]
        if len(cols) != 1:
            raise ValueError(f"seed column {prefix!r}: found {cols}")
        add(sid, item, sheets[sheet][cols[0]])
    for c in sheets["Monthly_Data"].columns:
        m = re.match(r"Rendered market services — (.*) \(% of previous year\)", str(c))
        if m:
            add("mkt_services_growth", SERVICES[m.group(1).strip()], sheets["Monthly_Data"][c])
    return {
        sid: pd.concat(p, ignore_index=True).sort_values(["item", "ref_period"]) for sid, p in parts.items()
    }


def _parse_with_adapter(series: dict, path: Path, ext: str) -> pd.DataFrame:
    a = adapter_for(series, FetchContext(run_date=dt.date.today()))
    return a.parse(Raw(path.read_bytes(), ext, path.name)).data


def load_seed(store: Store, catalogue: list[dict] | None = None) -> list[str]:
    """Write the seed vintages into an empty store. Returns the run ids written."""
    if not store.is_empty():
        return []
    cat = {s["id"]: s for s in (catalogue or load_catalogue())}
    batches = {
        "workbooks": (
            {
                **workbook_series(),
                **{
                    sid: _parse_with_adapter(cat[sid], NAG, "xlsx")
                    for sid in ("gdp_nag", "gva_services_q")
                    if sid in cat
                },
            },
            {"workbook": sha256(WORKBOOK.read_bytes()), "nag": sha256(NAG.read_bytes())},
        ),
    }
    if USD_DAILY.exists() and "usd_rate" in cat:
        batches["usd"] = (
            {"usd_rate": _parse_with_adapter(cat["usd_rate"], USD_DAILY, "csv")},
            {"usd": sha256(USD_DAILY.read_bytes())},
        )
    written = []
    for name, (series, hashes) in batches.items():
        date = SEED_DATES[name]
        run_id = f"seed-{date.isoformat()}"
        fetched = dt.datetime.combine(date, dt.time(0, 0))
        rows, report = [], {}
        for sid, df in series.items():
            if sid not in cat:
                continue
            h = hashes["nag"] if sid in ("gdp_nag", "gva_services_q") else next(iter(hashes.values()))
            ch = compare(sid, pd.DataFrame(columns=["item", "ref_period", "value"]), df)
            rows.append(delta_rows(ch, date, run_id, h, fetched))
            report[sid] = {
                "status": "ok",
                "source": "seed",
                "points": len(df),
                "first": df["ref_period"].min(),
                "last": df["ref_period"].max(),
            }
        store.write_observations(run_id, pd.concat(rows, ignore_index=True))
        store.write_run_file(
            run_id,
            "report.json",
            {
                "run_id": run_id,
                "vintage_date": date.isoformat(),
                "trigger": "seed",
                "files": hashes,
                "series": report,
            },
        )
        written.append(run_id)
    return written
