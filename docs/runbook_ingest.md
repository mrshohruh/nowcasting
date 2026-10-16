# Runbook: monthly data ingestion

What runs, where to look, and what to do when a source fails. Code: `nowcast/ingest/`,
`nowcast/store/`; workflow: `.github/workflows/monthly.yml`; data: the `data` branch.

## What runs when

| When | What |
| --- | --- |
| 1st of the month, 06:00 Tashkent | Full run: every series in `config/series_catalogue.yaml` |
| 2nd of the month, 06:00 Tashkent | Catch-up: only the series that failed in this month's last run. Does nothing if none failed |
| Any time, by hand | Actions tab > **monthly** > **Run workflow**. Same pipeline as the scheduled run |

The manual run has three optional inputs: **series** (comma-separated ids, e.g.
`gold_price,remittances_q`; empty = all), **catch_up** (retry this month's failures), and
**accept** (see "A check failed" below). Two runs never overlap: a second one waits.

Each run:

1. downloads each file once (several series can share one file), or takes a file from the
   manual inbox;
2. saves it unchanged as `raw/<source>/<series>/<run>.<ext>` with its SHA-256 in `raw/index.csv`;
3. parses it, compares it with what the store already had (new, revised, removed periods), and
   runs the quality checks;
4. stores what changed in `observations/<run>.parquet` (a failed series is left out; the store
   keeps its previous values);
5. writes `runs/<run>/report.json`, `runs/<run>/changes.json`, `latest.csv`, commits to `data`;
6. sends the Telegram message, and opens or updates a GitHub issue for each failing source.

The run id is the date in Tashkent (`2026-12-01`); a second run on the same day is `2026-12-01.2`.

## Where to look

- **Telegram**: one message per run with counts, what was updated, every warning and failure, and
  a link to the run.
- **GitHub issues** with label `ingest-failure`: one per failing source. It gets a comment each
  time the source fails again and closes itself when the source works again.
- **The run page** in the Actions tab: the step "Download, check and store" prints one line per
  series; the run summary shows the same table.
- **The `data` branch**: `runs/<run>/report.json` (status, messages, URL, SHA-256, latest period,
  expected latest period) and `runs/<run>/changes.json` (every new, revised and removed value
  with old and new numbers).

## Status of a series

| Status | Meaning | Stored? |
| --- | --- | --- |
| ok | downloaded, parsed, every check passed | yes |
| warn | stored, but something needs a look: release later than the lag rule expects, a label or item changed, periods removed, a cumulative value fell within the year | yes |
| fail | could not download or parse, or a check failed: unit changed, duplicate periods, m/m index outside 80-125 | no, previous values kept |

A **late release** ("latest period is 2026-09; the lag rule expects 2026-10") is normal now and
then: Uzstat sometimes publishes a few days late. The next manual run or next month picks it up.
If the same series is late two months running, check its page on the publisher's site.

## A source failed

1. Open the issue (or the Telegram message). The error says what happened.
2. **Site down or timing out** (ConnectionError, Timeout, 5xx). Nothing to do: the catch-up on
   the 2nd retries it. If it is still down, start a manual run later with **series** set to the
   failed ids, or use the manual inbox.
3. **HTTP 429 from SIAT** (too many requests): the client already waits and retries. If it still
   fails, run just those series by hand an hour later.
4. **URL moved or file layout changed** (404, "codes [...] not in SIAT table", "INDICATOR ... not
   in file", "row ... not found", "NAG layout changed", "no 'Gold' column"). Find the new URL or
   code on the publisher's site, fix `config/series_catalogue.yaml` (or the adapter in
   `nowcast/ingest/adapters.py`), push to `main`, and start a manual run for those series.
   Until then the manual inbox keeps the data flowing.

## Manual inbox (fallback for any source)

Download the file by hand and put it in `inbox/<series_id>/` on the `data` branch (GitHub: switch
to branch `data`, open `inbox/`, Add file > Upload files, and drag in a folder named after the
series id with the file inside; GitHub keeps the folder). The next run takes the newest file in that folder instead of downloading, parses it
exactly like a download, and moves it to `inbox/_used/<run>/<series_id>/`.

| Source | File to put in the inbox |
| --- | --- |
| siat | the JSON data file: open `https://api.siat.stat.uz/sdmx/<table>/table/download/?download_format=json` in a browser and download the URL in its `file` field |
| cbu_sdmx | the XML file at the catalogue URL (e.g. `https://cbu.uz/sdmx/INR_Uzbekistan_Online_STA.xml`) |
| cbu_xlsx | `BOP_Analytical_Uzbekistan.xlsx` from the catalogue URL |
| cbu_rate_json (`usd_rate`) | a CSV with columns `date,rate` and one row for every calendar day of the months to add |
| worldbank_xlsx | `CMO-Historical-Data-Monthly.xlsx` (the "Monthly prices" link on the commodity-markets page) |
| stat_nag_xlsx | `nag_uzbekistan_mcd.xlsx` from the catalogue URL |

The file must be the publisher's own file, unchanged (official primary sources only). If the
parser cannot read it, the series fails as usual and the file stays in the inbox.

## A check failed (unit changed, index out of range)

These failures protect the models from a silent change in what a number means. First confirm on
the publisher's site that the change is real (e.g. Uzstat now publishes in billion instead of
million soums). If it is, start a manual run with **series** and **accept** both set to that
series id. The run stores the data with status warn ("accepted by hand"), and the new unit becomes
the reference for later runs. If the transformation in Phase 2 depends on the unit, update it in
the same change. `accept` never overrides a download or parse error.

## Alerts setup

- Telegram: create a bot with @BotFather, send it any message, and get the chat id from
  `https://api.telegram.org/bot<token>/getUpdates`. Add two repository secrets (Settings >
  Secrets and variables > Actions): `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Without them the
  run prints the message in its log instead of sending it.
- GitHub issues need no setup (the workflow's own token opens them).

## Self-hosted runner (task 1.9)

Not needed: on 16 Oct 2026 a GitHub-hosted runner reached every source
(`docs/reachability_github_runner.md`). If a publisher later blocks GitHub's addresses, the error
will be a connection failure on every run. Then register an Institute server as a self-hosted
runner (repository Settings > Actions > Runners > New self-hosted runner) and change
`runs-on: ubuntu-latest` to `runs-on: self-hosted` in `monthly.yml`. Until then, use the inbox.

## Reading the store

```python
from nowcast.store.vintages import Store

st = Store("path/to/data-branch-checkout")
st.latest()                         # every series, item, period: current value
st.as_of("2026-12-01")              # what the store knew on 1 Dec 2026
st.as_of("2026-12-01", ["m2"])      # one series
with st.connect() as con:           # DuckDB: observations, latest, as_of(d)
    con.sql("select * from as_of(DATE '2026-12-01') where series_id = 'cpi_mm'").df()
```

Items: SIAT classifier code (`1700` = republic, `B, C, D, E` = industry), SDMX indicator, `GOLD`,
`USD`, and for the NAG file `<item>.N` (current prices), `<item>.R` (previous-year prices),
`<item>.G` (published growth, %).

## Starting the store again

Delete the `data` branch and run the workflow: an empty store is seeded with the two workbooks
(vintage 2026-10-14) and the daily USD rate back-fill (vintage 2026-10-16) before the download.
Only do this on purpose: the vintages since then cannot be rebuilt (the sources keep only their
latest version).
