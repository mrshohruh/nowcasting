# Methodology

## Overview

The IMRS GDP Nowcasting Platform produces monthly estimates of Uzbekistan's real GDP growth (year-on-year) before official statistics are released. The headline estimate comes from a Dynamic Factor Model (DFM) that extracts common trends from a mixed-frequency panel of macroeconomic indicators. Sector-level contributions are derived from bridge equations linking monthly indicators to quarterly GVA components. Each release includes a news decomposition that attributes the change in the nowcast to data revisions, new data releases, and model re-estimation.

The platform is fully automated: data is downloaded from official sources on the 1st of each month, models are estimated, and a dashboard with all results is generated for review before publication.

## Data Sources

The model draws on 42 series from three official publishers:

| Publisher | Source system | Data type | Frequency | Typical lag |
|-----------|-------------|-----------|-----------|-------------|
| **Uzstat** (Statistics Agency) | SIAT API (JSON) | Industrial production, construction, trade, services, prices, investment, wages, GDP (NAG) | M / Q | 2 months |
| **Central Bank of Uzbekistan** | SDMX XML, XLSX, JSON | Monetary aggregates (M2), FX reserves, interest rates, BOP, remittances, exchange rate | M / Q | 1–4 months |
| **World Bank** | Commodity price XLSX | Gold price (USD/oz) | M | 2 months |

**Monthly indicators (24 core series):** Industrial production by sector (mining, manufacturing, electricity), construction, retail and wholesale trade, 5 market services sectors (trade, transport, ICT, finance, accommodation), exports (total, gold, non-gold), imports, exchange rate, CPI, PPI, M2, FX reserves, gold price, deposit and lending rates, new small businesses.

**Quarterly indicators (7 series):** GDP and GVA by sector (from the NAG file, 17 production-side components), real remittances, real wages, investment, BOP goods exports, BOP goods imports.

All series are downloaded automatically from their official APIs or files. A backtest rule determines when each series becomes available: `months_after` specifies how many months after the reference period a data point is published (typically 2 months for Uzstat, 1 month for the exchange rate, 4 months for BOP).

## Data Transformations

Raw data undergoes several transformations before entering the models:

1. **Decumulation.** Uzstat publishes most series as cumulative year-to-date values (e.g., industrial production from January to the current month). These are converted to discrete monthly or quarterly values using the previous month's cumulative and, where needed, nominal weights from the same table.

2. **Level construction.** For real growth series, a chained real level is constructed using the first full year of nominal weights as the base (real_growth recipe). Price indices (CPI, PPI) are built by chaining month-on-month indices into a level with 2021 = 100 (price_index recipe).

3. **Seasonal adjustment.** Series marked `sa: true` are seasonally adjusted using robust STL decomposition on log-transformed levels. Financial variables without a seasonal pattern (exchange rate, gold price, reserves, interest rates) are used as-is.

4. **Growth rates.** Two panels are produced:
   - **y/y panel:** Year-on-year growth rates for all variables (63 variables). Used by the bridge equations and MIDAS.
   - **SA panel:** Dlog × 100 of seasonally adjusted levels (47 variables). Used by the DFM.

5. **Standardization.** For the DFM, each variable in the SA panel is z-scored (demeaned and divided by its standard deviation) to put all series on a comparable scale.

## Models

### Model A: Dynamic Factor Model (DFM)

The headline nowcast comes from a Dynamic Factor Model estimated using the `DynamicFactorMQ` class from `statsmodels`. The model extracts latent common factors from the mixed-frequency panel and uses them to predict GDP.

**Specification:**
- **Panel:** SA dlog × 100 (z-scored), 19 monthly variables + quarterly GDP and other quarterly indicators
- **Factors:** 2 global factors
- **Factor dynamics:** VAR(2) — each factor depends on two lags of all factors
- **Idiosyncratic component:** AR(1) for each series
- **Estimation:** EM algorithm with 250 maximum iterations
- **Missing data:** Handled natively by the Kalman filter, which fills the ragged edge (months where not all series have been published yet)

**Prediction pipeline:**
1. Build the mixed-frequency endog matrices (monthly and quarterly) from the SA panel
2. Estimate the model via EM
3. Extract predicted values using the Kalman smoother
4. Convert the GDP prediction from z-scored SA dlog back to y/y growth:
   - Unscale the z-score to SA dlog × 100
   - Convert dlog to a SA level using `exp(dlog/100)`
   - Compute y/y as `(SA_level_t / SA_level_{t-4} - 1) × 100`
5. Winsorize: clip the y/y prediction to ±3σ of the last 12 quarters' actual GDP growth

**Backtest performance:** At the m3 horizon (all three months of the quarter available), the DFM achieves RMSE = 0.957 pp, which is 0.891 × the AR(1) benchmark — a 10.9% improvement.

### Model B: Bridge Equations

Bridge equations link monthly indicators to quarterly GVA sectors, providing a sector-level breakdown of GDP growth. Each of 10 GVA sectors is predicted by one or more monthly indicators:

| Sector | Indicators | Method |
|--------|-----------|--------|
| Agriculture | (none) | AR |
| Mining | Mining output | Ridge |
| Manufacturing | Manufacturing output | Ridge |
| Electricity | Electricity output | Ridge |
| Construction | Construction output | Ridge |
| Trade & accommodation | Retail, wholesale, mkt_trade, mkt_accommodation | Ridge |
| Transport | Transport services | Ridge |
| ICT | ICT services | Ridge |
| Other services | Financial services | Ridge |
| Net taxes | (none) | AR |

Each sector's contribution to GDP is computed as `weight × sector_growth`, where weights are the sector's share of GDP (from National Accounts). The bridge GDP is the sum of all sector contributions.

### Benchmark: AR(1)

A simple AR(1) model serves as the benchmark. It projects GDP y/y by shrinking the last known value toward the long-run mean with persistence parameter φ = 0.6. The exit check requires the headline model to beat AR(1) by at least 10% in RMSE.

### Model C: MIDAS (Experimental)

A Mixed Data Sampling model using elastic net with Almon polynomial weighting. It operates on the y/y panel with 6 monthly lags and polynomial degree 3. Currently experimental — included in model combinations but not used as the headline.

## Uncertainty Bands

The 80% prediction interval is constructed from the historical distribution of backtest errors:

1. Run the DFM on every past vintage from 2022-01 to 2026-09 (57 run dates)
2. At each horizon (m1, m2, m3), compute the nowcast error vs. first-release GDP
3. Take the 10th and 90th percentiles of these errors
4. Widen by a small-sample factor of 1.2 (20%) to account for the limited backtest window

The band is centered on the point estimate and represents the range within which 80% of past nowcasts fell relative to the official outcome.

## News Decomposition

Each monthly update includes a decomposition of the change in the nowcast, following Bańbura and Modugno (2011). The decomposition runs four DFM estimations:

| Run | Parameters | Data | Captures |
|-----|-----------|------|----------|
| 1 | Old | Old | Baseline (previous nowcast) |
| 2 | Old | Revised | **Revision effect** (old data updated) |
| 3 | Old | New | **News effect** (new observations) |
| 4 | New | New | **Re-estimation effect** (parameter update) |

The total change is:

    Δ nowcast = revision + news + re-estimation

For the news effect, the Kalman filter's gain coefficients attribute the forecast revision to individual data releases: each new observation contributes `surprise × weight`, where surprise is the difference between the actual value and the model's forecast.

The decomposition is available from the second run onward (the first run has no previous state to compare against).

## Backtest

The backtest replays the nowcasting pipeline on historical vintages to measure out-of-sample accuracy:

- **Period:** January 2022 to September 2026
- **Frequency:** Monthly (1st of each month)
- **Run dates:** 57
- **Horizons:** forecast (next quarter), nowcast_m1/m2/m3 (current quarter with 1/2/3 months of data), backcast (quarter ended, awaiting release)
- **Target:** First-release GDP y/y (also evaluated against latest vintage)
- **Benchmark:** AR(1) with φ = 0.6

Key results at m3 horizon:
- DFM RMSE: 0.957 pp
- AR(1) RMSE: 1.074 pp
- Ratio: 0.891 (11% improvement)

## Release Schedule

- **Primary run:** 1st of each month at 06:00 Tashkent time (01:00 UTC)
- **Catch-up run:** 2nd of each month — retries any series that failed on the 1st
- **Manual run:** Any time via the GitHub Actions "Run workflow" button

After the pipeline completes, the dashboard is deployed to a staging environment for review. A designated reviewer approves the release before it goes to the production site.

## Revision Policy

Past nowcasts are never overwritten. Each monthly run is stored as a dated vintage in the data store (DuckDB + Parquet on the `data` branch). When source agencies revise their data, the new values appear in the next run — both the new nowcast and the decomposition reflect these revisions.

All historical nowcasts, decomposition results, and data vintages are available for download on the dashboard.

## Disclaimer

The GDP nowcasts published on this platform are preliminary estimates produced by the Institute of Macroeconomic and Regional Studies (IMRS) using statistical models. They are **not official statistics** and should not be cited as such.

These estimates are subject to revision as new data becomes available and as source agencies revise their published figures. The models are estimated on a limited historical sample and their accuracy may vary over time.

The platform is intended for research and policy analysis purposes. Users should consult the official GDP releases from the Statistics Agency of Uzbekistan for definitive figures.

## References

- Bańbura, M. and Modugno, M. (2011). "Maximum likelihood estimation of factor models on datasets with arbitrary pattern of missing data." *Journal of Applied Econometrics*, 29(1), 133–160.
- Bok, B., Caratelli, D., Giannone, D., Sbordone, A., and Tambalotti, A. (2018). "Macroeconomic Nowcasting and Forecasting with Big Data." *Annual Review of Economics*, 10, 615–643.
- Giannone, D., Reichlin, L., and Small, D. (2008). "Nowcasting: The real-time informational content of macroeconomic data." *Journal of Monetary Economics*, 55(4), 665–676.
