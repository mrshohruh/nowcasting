# Data store of the nowcasting platform

Written by the `monthly` workflow of https://github.com/mrshohruh/nowcasting (code on `main`).
Do not edit by hand, except `inbox/`.

| Path | What |
| --- | --- |
| `raw/<source>/<series>/<run>.<ext>` | every downloaded file as received; `raw/index.csv` has SHA-256 |
| `observations/<run>.parquet` | values that were new, revised or removed in that run |
| `runs/<run>/report.json` | status (ok, warn, fail) and messages for every series in the run |
| `runs/<run>/changes.json` | new, revised and removed periods per series |
| `latest.csv` | current value of every series, item and period |
| `meta/<series>.json` | unit, items and labels last accepted (used by the checks) |
| `inbox/<series>/` | manual fallback: see `docs/runbook_ingest.md` on `main` |
| `panels/` | model-ready y/y and SA panels built after every run (`docs/transformations.md` on `main`) |

Read it in Python with `nowcast.store.vintages.Store("<this folder>")`: `.latest()`, `.as_of("2026-12-01")`.
