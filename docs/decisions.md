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

## Open

| Item | Needed by |
| --- | --- |
| Russian translation | after go-live |
| Institute web team contact and publishing method | Phase 5 |
