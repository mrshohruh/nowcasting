# Nowcasting Platform — Handover Guide

**For: Colleagues at the Institute of Macroeconomic and Regional Studies**
**Last updated: 2026-10-21**

---

## 1. What This Platform Does

The Uzbekistan GDP Nowcasting Platform produces **real-time GDP growth estimates** before official statistics are released. It combines a Dynamic Factor Model (DFM) with bridge equations across 10 GVA sectors to generate nowcasts using ~20 monthly macroeconomic indicators.

Key outputs each month:
- **GDP growth nowcast** (year-on-year %) with 80% uncertainty band
- **News decomposition** showing what changed and why
- **Sector contributions** from bridge equations
- **Static dashboard** (HTML) in English and Uzbek
- **One-page PDF note** in both languages

---

## 2. Repository Structure

```
nowcasting/
├── config/
│   ├── catalogue.yaml      # Series definitions (IDs, sources, transforms)
│   └── model.yaml           # Model parameters (factors, lags, EM iterations)
├── data/                    # DuckDB + Parquet vintage store (on `data` branch)
├── docs/
│   ├── runbook.md           # Operational procedures (read this for day-to-day)
│   └── handover.md          # This file
├── nowcast/
│   ├── core/
│   │   ├── catalogue.py     # Series catalogue loader
│   │   ├── panels.py        # Panel data builder (monthly + quarterly)
│   │   └── vintage.py       # DuckDB vintage store
│   ├── models/
│   │   ├── dfm.py           # Dynamic Factor Model wrapper
│   │   ├── bridge.py        # Bridge equations (10 GVA sectors)
│   │   └── ar.py            # AR(1) benchmark
│   ├── explain/
│   │   ├── news.py          # Bańbura-Modugno news decomposition
│   │   ├── narrative.py     # Auto-generated narrative text
│   │   └── uncertainty.py   # Backtest-based uncertainty bands
│   └── report/
│       ├── site.py          # Static site generator (Jinja2 + Plotly)
│       ├── charts.py        # Plotly chart specifications
│       ├── pdf.py           # WeasyPrint PDF note generator
│       ├── templates/       # Jinja2 HTML templates (8 pages)
│       └── static/          # CSS, logo, assets
├── scripts/
│   └── produce.py           # Production pipeline (orchestrates everything)
├── tests/                   # 121+ tests (pytest)
└── .github/workflows/
    └── monthly.yml          # GitHub Actions: monthly run + staging + approval
```

---

## 3. How a Monthly Run Works

### Automatic (GitHub Actions)

The workflow runs on the **1st of each month at 06:00 Tashkent time** (01:00 UTC):

1. **Build panels** — loads latest data from the vintage store
2. **Run DFM** — produces headline GDP nowcast
3. **Run bridge equations** — produces sector-level estimates
4. **News decomposition** — explains what changed since last run
5. **Generate uncertainty bands** — from backtest error distribution
6. **Build dashboard** — static HTML site with all charts and tables
7. **Generate PDF notes** — bilingual one-page summaries
8. **Deploy to staging** — GitHub Pages preview
9. **Wait for approval** — manual review in `production` environment
10. **Publish** — deploy to production GitHub Pages

### Manual Run

```bash
# From the repo root:
python scripts/produce.py \
  --store data/store \
  --state-dir state/ \
  --out _site/ \
  --as-of 2027-02-01 \
  -v
```

---

## 4. Key Configuration Files

### `config/catalogue.yaml`

Defines every input series: its source (Uzstat, CBU, World Bank), frequency (monthly/quarterly), expected publication lag, and transformation (year-on-year growth, seasonal adjustment, etc.).

**To add a new indicator:** Add an entry with the series ID, label, source, frequency, and transform. Then add the raw data to the vintage store.

### `config/model.yaml`

Controls model parameters:
- `n_factors: 2` — number of common factors in the DFM
- `factor_order: 2` — VAR order for factor dynamics
- `idio: "AR(1)"` — idiosyncratic component specification
- `em_iterations: 250` — EM algorithm convergence iterations
- `bridge_method: "ridge"` — default bridge equation method

**When to change:** Generally stable. Increase `n_factors` only if adding many new series. Adjust `em_iterations` if convergence warnings appear.

---

## 5. Data Pipeline

### Vintage Store

All raw data lives in a **DuckDB + Parquet** vintage store on the `data` branch. Each monthly run creates a snapshot (vintage) that preserves the exact data available at that point in time — past vintages are never modified.

### Adding New Data

1. Prepare data in the expected format (series ID, date, value)
2. Use the vintage store API to append:
   ```python
   from nowcast.core.vintage import VintageStore
   store = VintageStore("data/store")
   store.append(series_id="new_series", data=df)
   ```
3. Add the series to `config/catalogue.yaml`
4. Re-run to verify

### Data Sources

| Source | Series | Frequency | Typical Lag |
|--------|--------|-----------|-------------|
| Uzstat | Industrial production, construction, trade, services, CPI, PPI | Monthly | 1-2 months |
| CBU | M2, FX reserves, policy rate, exchange rates | Monthly | 1 month |
| World Bank | Gold price | Monthly | < 1 month |
| Uzstat | GDP (national accounts), wages, investment | Quarterly | 1 quarter |

---

## 6. Models

### Dynamic Factor Model (DFM)

The headline model. Extracts 2 common factors from the monthly indicator panel using `statsmodels.DynamicFactorMQ`. Handles mixed frequencies (monthly + quarterly) and missing data natively through the EM algorithm.

**Output:** Single GDP growth y/y estimate with uncertainty band.

### Bridge Equations

10 separate models, one per GVA sector. Each maps monthly indicators to quarterly sectoral growth using ridge regression. Sectors with insufficient monthly data fall back to AR(1).

**Output:** Sector-level contributions that sum to an alternative GDP estimate. The bridge sum and DFM headline are reported side by side.

### News Decomposition

Following Bańbura & Modugno (2014), changes between consecutive nowcasts are decomposed into:
- **Data revisions** — previously released data was revised
- **New data (news)** — new observations arrived
- **Re-estimation** — model parameters updated

This uses a 4-run strategy comparing models with old/new data and old/new parameters.

---

## 7. Dashboard

The dashboard is a **static HTML site** generated by `nowcast/report/site.py`. It uses:
- **Jinja2** for templating (8 pages: headline, path, what changed, sectors, monitor, models, downloads, methodology)
- **Plotly.js** (from CDN) for interactive charts
- **Inter** font from Google Fonts
- **Bilingual** support (English + Uzbek, Russian-ready)

To rebuild the dashboard manually:
```bash
python -m nowcast.report.site --demo --out _site    # with sample data
python -m nowcast.report.site --context ctx.json --out _site  # with real data
```

Open `_site/index.html` in a browser to preview.

---

## 8. PDF Notes

Generated by `nowcast/report/pdf.py` using WeasyPrint. Produces A4 one-page notes with:
- KPI strip (quarter, GDP growth, change from previous)
- Decomposition bars
- Top drivers list
- Sector contribution table
- Narrative summary
- Disclaimer footer

Falls back to HTML output if WeasyPrint is not installed.

---

## 9. Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=nowcast

# Run specific test modules
pytest tests/test_dfm.py
pytest tests/test_bridge.py
pytest tests/test_news.py
```

Tests cover: data loading, panel construction, DFM estimation, bridge equations, news decomposition, uncertainty bands, narrative generation, and site building.

---

## 10. Troubleshooting

| Problem | Solution |
|---------|----------|
| DFM doesn't converge | Increase `em_iterations` in model.yaml, or check for all-NaN series |
| Missing data warning | Check the data monitor page — some series may have publication delays |
| Bridge equation fallback | Normal for sectors with few monthly indicators (agriculture, other services) |
| WeasyPrint not installed | PDF falls back to HTML; install with `pip install weasyprint` |
| Site build fails | Check that all templates exist in `nowcast/report/templates/` |
| GitHub Actions timeout | The full pipeline takes ~5 minutes; increase timeout if adding series |

---

## 11. Contacts

- **Platform developer:** Shokhrukhkhon Nishonkulov, Leading Specialist, IMRS
- **Repository:** https://github.com/mrshohruh/nowcasting
- **Methodology reference:** Bańbura, M. & Modugno, M. (2014). "Maximum likelihood estimation of factor models on datasets with arbitrary pattern of missing data." Journal of Applied Econometrics.

---

## 12. Release Calendar

| Date | Event |
|------|-------|
| Feb 1, 2027 | Shadow run #1 (internal) |
| Mar 1, 2027 | Shadow run #2 (internal) |
| Apr 1, 2027 | First public release |
| Monthly, 1st | Recurring production run |
