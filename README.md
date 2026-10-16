# nowcast-uz

Monthly nowcasts of Uzbekistan's real GDP growth (production method), published as
Uzstat's cumulative year-to-date figure with the discrete quarter beside it. Every change
in the nowcast is explained by the data releases that caused it.

Built in six phases (see the project plan). Phase 0 status: data audit and GDP target.

## Layout

| Path | What |
| --- | --- |
| `config/series_catalogue.yaml` | every input series: publisher, URL, form, unit, model block |
| `nowcast/ingest/` | source clients: `siat.py` (Uzstat SIAT API), `sdmx.py` (NSDP / central bank SDMX) |
| `nowcast/transform/target.py` | GDP target: discrete-quarter and cumulative real growth |
| `nowcast/catalogue.py` | catalogue loader and validator |
| `tools/` | one-off scripts: source inventory, reachability check, data audit |
| `data/seed/` | the two workbooks the project started from (14 Oct 2026) |
| `docs/` | data audit, inventory, decisions, reachability results |

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
pytest -m "not network"
python -m nowcast.transform.target          # rebuild the GDP target from the seed NAG file
```
