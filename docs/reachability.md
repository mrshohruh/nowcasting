# Reachability (Phase 0, task 0.4)

Every source host answered from a GitHub-hosted runner on 16 Oct 2026, so **no self-hosted runner
on the Institute server is needed** for now. Raw result: `docs/reachability_github_runner.md`
(written by `.github/workflows/reachability.yml`; rerun it from the Actions tab at any time).

| Host | Used for | From GitHub |
| --- | --- | --- |
| api.siat.stat.uz | all SIAT tables (pointer + data file) | ok |
| siat.stat.uz | web pages (not needed by the pipeline) | ok |
| stat.uz | NAG national accounts xlsx | ok (15 s) |
| nsdp.stat.uz | NSDP page | ok |
| cbu.uz | M2, reserves, rates SDMX; BoP xlsx; daily USD rate JSON | ok |
| www.worldbank.org, thedocs.worldbank.org | Pink Sheet gold price | ok |

Two behaviours the Phase 1 fetchers must handle:

- **SIAT rate limit.** api.siat.stat.uz returns HTTP 429 after roughly 15-20 quick requests. The
  client waits 3 s between tables and backs off on 429 (`nowcast/ingest/siat.py`).
- **SIAT two-step download.** The `table/download/` URL returns a small pointer with `updated_at` and
  the real file URL. `updated_at` is a free "has this table changed?" check.
