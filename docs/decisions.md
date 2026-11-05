# Decisions (Phase 0, task 0.8)

## Decided

| Date | Decision |
| --- | --- |
| 16 Oct 2026 | Headline is Uzstat's cumulative year-to-date real GDP growth; the discrete quarter is shown beside it. |
| 16 Oct 2026 | Runs on GitHub Actions (repository mrshohruh/nowcasting); may move to an Institute server later as a self-hosted runner. |
| 16 Oct 2026 | Scheduled run only on the 1st of each month (06:00 Tashkent); manual runs any time from the Actions tab. A catch-up on the 2nd retries only failed sources. |
| 16 Oct 2026 | Output is public, on the Institute website. |
| 16 Oct 2026 | Data only from official primary publishers: Uzstat, the Central Bank, the Ministry of Economy and Finance, and the World Bank for the gold price. |
| 16 Oct 2026 | Languages: Uzbek and English; Russian may be added later. |
| 16 Oct 2026 | Model output only: no analyst overrides and no analyst comments. The release approval only checks that the run worked. |
| 16 Oct 2026 | No release calendar. Backtest rule: a period is used from the 1st of the second month after it ends (August data from 1 October; Q2 from 1 August); remittances from the fourth month (Q2 from 1 October). |
| 16 Oct 2026 | Quarterly GVA of the service sectors enters the services block alongside the monthly market-service rates. |
| 16 Oct 2026 | Phase 1: the SIAT adapter reads the JSON download (the plan said CSV). Phase 0 verified the JSON, and it carries the same classifier `Code` column the rows are selected by. |
| 16 Oct 2026 | Phase 1: the store is one parquet file of changed values per run (`observations/<run>.parquet`) on the `data` branch, read by DuckDB (`observations`, `latest`, `as_of(d)`). Not a DuckDB database file: a binary file rewritten every month would grow the branch by its full size each time and could be corrupted by a failed run; per-run files are append-only. |
| 16 Oct 2026 | Phase 1: observations carry an `item` column (SIAT code, SDMX indicator, NAG `<item>.<N/R/G>`), because several series have more than one row (14 market-service types, 17 NAG items in 3 price bases). |
| 16 Oct 2026 | Phase 1: a vintage is dated by the run day in Tashkent; run ids are `YYYY-MM-DD`, then `YYYY-MM-DD.2` for a second run that day. |
| 16 Oct 2026 | Phase 1: the monthly UZS/USD rate is the mean over calendar days of the official rate in force each day, for complete months only; daily rates since 2018 were back-filled once (seed vintage 2026-10-16). |
| 16 Oct 2026 | Phase 1: a failed data check (unit changed, index out of range) can be accepted for one run with the manual-run input `accept`, after the change is confirmed on the publisher's site. It never overrides download or parse errors, and it does not touch model output. |
| 16 Oct 2026 | Phase 1, task 1.9 skipped: a GitHub-hosted runner reaches every source, so no self-hosted runner is needed. |
| 16 Oct 2026 | Phase 2: construction and market-services growth are cumulative from January (implied deflators 4-6x smoother than the discrete reading; match the NAG cumulative sector growth within 0.8-1.2 pp). Average wages are averages from January (new form `average_ytd`). |
| 16 Oct 2026 | Phase 2: real terms come from Uzstat's cumulative real growth wherever it exists (industry and sub-sectors, construction, retail, wholesale, market services, investment), turned into discrete growth with last year's nominal weights. Deflating nominal output by PPI or CPI is not used. CPI deflation remains for M2, wages, income, FDI investment and remittances (after UZS conversion). |
| 16 Oct 2026 | Phase 2: three weight series added to the catalogue: industry sub-sector output (codes B, C, D of SIAT 590), investment in current prices (SIAT 1561), market-service volumes (SIAT 3217). Without them the code falls back to total-industry or equal weights and flags it. |
| 16 Oct 2026 | Phase 2: real levels chain each month (quarter) to the same period a year earlier, as the plan says for GDP, also for monthly indicators; annual-overlap chain-linking was tested and was noisier. |
| 16 Oct 2026 | Phase 2: the plan's test "deflated growth within 1 pp of Uzstat's real growth" is applied to wages (the deflated series with an official real figure): 9.35% vs 9.3% (2025), 10.88% vs 10.9% (Jan-Jun 2026). For series where Uzstat's real growth is used directly, the test is that re-cumulating our discrete growth gives Uzstat's figure back exactly. |
| 16 Oct 2026 | Phase 2: panels start in 2013 (central bank series); interest rates enter as levels' changes in pp; exchange rate, reserves, gold price and rates are not seasonally adjusted. The monthly workflow rebuilds the panels into `panels/` on the data branch after each run. |
| 28 Oct 2026 | DFM output: GDP growth is read from the Kalman smoother (all data on the run date), and y/y chains each quarter's own model growth from the last published GDP level. Fixes the forecast-horizon blow-ups (16.9% for 2023Q3, 0.7% for 2024Q3) and lets the latest month of data enter. |
| 28 Oct 2026 | Data Monitor lists only the DFM inputs, with publisher, release lag and next due date from the catalogue; quarterly inputs get their own table. Methodology page rewritten to match the code (robust STL, SA log changes, pseudo-real-time backtest), in English and Uzbek. |
| 5 Nov 2026 | NAG workbook: Uzstat changed `nag_uzbekistan_mcd.xlsx` between 2 and 4 Nov 2026 (sheet names with spaces instead of underscores, one row per e-GDDS indicator code, Jan-Sep 2026 added). The reader finds the quarterly sheet by pattern (latest end year) and reads both layouts; rows are selected by indicator code and the label keyword is still checked. Until the fix, `gdp_nag` and `gva_services_q` failed from the 4 Nov run and 2026Q3 GDP stayed out of the store (SIAT 3698 already had it from 1 Nov). |
| 5 Nov 2026 | NAG consistency: where R / N(previous year) differs from Uzstat's published cumulative growth by more than 0.05 pp, the target uses the published growth (R_cum = G x N_prev_cum) for that quarter and records `real_basis = published growth`. Identical elsewhere (the old workbook agreed to 0.0 pp everywhere). Needed because the 2025Q3 column of the new workbook has levels 14-15% above the old file while its growth is 108.0 (was 107.6): raw levels would give discrete growth of 147% (2025Q3) and 73% (2025Q4). A run warns once when it loads such a revision. The levels are stored as published (no override). Checked 5 Nov against UzDaily's report of Uzstat's 30 Oct release: Jan-Sep 2025 nominal GDP is 1,494.9 trn and Jan-Sep 2024 is 1,224.4 trn, so the file's 2025Q3 column is the current one and the stale values are the 2024 quarters (2024Q3 is 1,079.9 trn in the file). The file mixes bases between 2025Q2 and 2025Q3; published cumulative growth (8.0% and 8.3%) is right, the quarter derived from it is approximate (about 0.2 pp for 2026Q3 if H1 2025 is on the new basis). |
| 5 Nov 2026 | Nowcast Timeline: the blue line is quarterly growth derived from Uzstat's cumulative figures, not an official number (Uzstat publishes only Q1, Jan-Jun, Jan-Sep, Jan-Dec; 2026Q3 = 8.3% cumulative, 7.98% derived). It is labelled that way, with Uzstat's cumulative rate on hover and in the footnote. The backtest builds its actuals as of today instead of a fixed 1 Oct 2026. |

## Open

| Item | Needed by |
| --- | --- |
| Russian translation | after go-live |
| Institute web team contact and publishing method | Phase 5 |
