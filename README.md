# nowcast-uz

Monthly nowcasts of Uzbekistan's real GDP growth (production method), published as
Uzstat's cumulative year-to-date figure with the discrete quarter beside it. Every change
in the nowcast is explained by the data releases that caused it.

Built in six phases (see the project plan). Phase 0: data audit and GDP target. Phase 1: monthly
ingestion into a vintage store, with checks and alerts.

## Layout

| Path | What |
| --- | --- |
| `config/series_catalogue.yaml` | every input series: publisher, URL, form, unit, model block |
| `nowcast/ingest/` | fetch framework (`base.py`, `http.py`), one adapter per source (`adapters.py`), quality checks (`quality.py`), one run (`run.py`), alerts (`alerts.py`) |
| `nowcast/store/` | vintage store (`vintages.py`: raw files, observations, `latest`, `as_of`), change detector (`changes.py`), seed history (`seed.py`) |
| `.github/workflows/monthly.yml` | scheduled run on the 1st, catch-up on the 2nd, manual run; commits each vintage to the `data` branch |
| `nowcast/transform/target.py` | GDP target: discrete-quarter and cumulative real growth |
| `nowcast/catalogue.py` | catalogue loader and validator |
| `tools/` | one-off scripts: source inventory, reachability check, data audit |
| `data/seed/` | the two workbooks the project started from (14 Oct 2026), daily USD rates since 2018 |
| `data` branch | the store: raw files, observations, run reports (see its README) |
| `docs/` | data audit, decisions, reachability results, `runbook_ingest.md` (what to do when a source fails) |

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
pytest -m "not network"
python -m nowcast.transform.target          # rebuild the GDP target from the seed NAG file
python -m nowcast.ingest.run --store store  # one ingestion run into ./store (the workflow uses the data branch)
```
