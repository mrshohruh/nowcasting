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

## Open

| Item | Needed by |
| --- | --- |
| Russian translation | after go-live |
| Institute web team contact and publishing method | Phase 5 |
