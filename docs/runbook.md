# Runbook

Operational procedures for the IMRS GDP Nowcasting Platform.

## 1. Approve a Release

After the monthly pipeline runs, the dashboard is deployed to a **staging** environment for review. You must approve it before it goes live.

### Steps

1. Go to the GitHub repository → **Actions** tab.
2. Find the latest **monthly** workflow run.
3. The run will be paused at the **deploy-production** job, waiting for approval.
4. Click on the **deploy-production** job → **Review deployments**.
5. Check the staging site (the link is in the **deploy-staging** job output).

### What to check on staging

- **Headline number:** Does the GDP y/y estimate look reasonable? Compare with last month's estimate and the AR(1) benchmark.
- **Decomposition:** Are the news drivers plausible? Do the revision/news/re-estimation effects sum to the total change?
- **Sectors:** Do sector contributions make sense given recent economic news?
- **Data monitor:** Are all expected series present? Any unexpected gaps?
- **Charts:** Do the headline chart and uncertainty band look correct?
- **PDF note:** Download and review both EN and UZ versions.

### If something looks wrong

- **Do not approve.** The staging site stays up but production is unchanged.
- Check the workflow logs for errors.
- If a data source is the issue, see "Handle a Failed Source" below.
- If the model output is suspicious, consider a manual rerun with different parameters.
- You can always rerun the pipeline (see next section) after fixing the issue.

### Approve

6. Select the **production** environment checkbox → **Approve and deploy**.

The dashboard will be published to GitHub Pages within a few minutes.

## 2. Rerun the Pipeline

Go to **Actions** → **monthly** → **Run workflow** (button in the top right).

### Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `series` | Only download these series (comma-separated ids). Leave empty for all. | (empty = all) |
| `catch_up` | Retry only the series that failed in this month's last run. | false |
| `accept` | Accept failed quality checks for these series (after confirming the change is real). | (empty) |

### Common scenarios

- **Full rerun:** Leave all inputs empty. Downloads everything and rebuilds.
- **Retry failures:** Check `catch_up = true`. Only re-downloads series that failed.
- **Single series test:** Enter the series id in `series` (e.g., `ind_mining_real`).
- **Accept a flagged series:** Put the series id in `accept` (e.g., `exports_gold_usd`). This tells the quality checker to accept the current values even if they fail the usual checks.

## 3. Handle a Failed Source

When a data source fails during ingestion, the workflow:
1. Creates a **GitHub issue** titled "Panels not built in run [run_id]" (if the panels step fails).
2. Logs the failure in the run output.

### Diagnosis

1. Check the workflow log: **Actions** → click the failed run → expand the failed step.
2. Common causes:
   - **Source down:** The SIAT API or CBU website may be temporarily unavailable. Wait and rerun.
   - **Format changed:** The source changed its JSON/XML structure. The adapter may need updating.
   - **SSL/network error:** Transient. Rerun with `catch_up = true`.
   - **Quality check failed:** The data passed download but failed range/jump checks. Check if the values are correct (e.g., a genuine large change in gold exports). If real, rerun with the series in `accept`.

### Manual download path

If automatic download consistently fails:

1. Download the file manually from the source URL listed in `config/series_catalogue.yaml`.
2. Place it in `store/inbox/` on the `data` branch:
   ```
   git checkout data
   mkdir -p store/inbox
   cp downloaded_file.json store/inbox/{series_id}_{YYYY-MM-DD}.json
   git add store/inbox/
   git commit -m "Manual download: {series_id}"
   git push origin data
   ```
3. Rerun the pipeline — the ingest step will pick up files from the inbox.

## 4. Manual Data Inbox

For data that cannot be downloaded automatically (e.g., received by email):

### Format

Place a CSV file in `store/inbox/` with these columns:

```csv
period,value
2026-08,123.4
2026-09,125.6
```

File naming: `{series_id}_{date_received}.csv`

### Commit to data branch

```bash
git checkout data
cp mydata.csv store/inbox/ind_mining_real_2026-11-01.csv
git add store/inbox/
git commit -m "Manual: ind_mining_real Nov 2026"
git push origin data
```

The next pipeline run will incorporate the inbox data.

## 5. Add a New Series

Adding a new indicator to the model requires changes in several files:

### Steps

1. **`config/series_catalogue.yaml`** — Add a new entry with all fields (id, name, publisher, source, url, key, codes, frequency, unit, form, block, first_period, months_after, status).

2. **Adapter** — If the source type is new (not siat, cbu_sdmx, cbu_xlsx, cbu_rate_json, worldbank_xlsx, or stat_nag_xlsx), write a new adapter in `nowcast/ingest/`.

3. **`config/transforms.yaml`** — Add the variable definition with recipe, SA flag, input mapping, and weights.

4. **`config/model.yaml`** — If the variable should enter the DFM, add it to the `variables` section under the appropriate block. If it maps to a GVA sector, add or update the bridge mapping under `bridges`.

5. **Backtest** — Run the backtest to validate:
   ```bash
   python scripts/run_backtest.py --store store
   ```
   Check that RMSE does not deteriorate.

6. **Test** — Run the test suite:
   ```bash
   pytest tests/
   ```

7. **Commit** — Commit all changes together.

## 6. Change Model Parameters

Model configuration lives in `config/model.yaml`.

### Common changes

| Change | Where | What to edit |
|--------|-------|-------------|
| Add/remove a variable from DFM | `variables.monthly` or `variables.quarterly` | Add/remove the variable id |
| Change number of factors | `dfm.n_factors` | Integer (start with 1–3) |
| Change factor dynamics | `dfm.factor_orders` | Integer (1 = VAR(1), 2 = VAR(2)) |
| Change EM iterations | `dfm.em_max_iter` | Integer (more = slower but potentially better convergence) |
| Change bridge mapping | `bridges.{sector}.indicators` | List of monthly variable ids |
| Change uncertainty bands | `uncertainty.percentiles` | e.g., [5, 95] for 90% bands |

### Validation

After any parameter change:
1. Run the backtest: `python scripts/run_backtest.py --store store`
2. Compare RMSE at m3 horizon before and after.
3. Check that the exit check still passes (RMSE ≤ 0.9 × AR(1)).

## 7. Rotate Secrets

### Current secrets

| Secret | Status | Notes |
|--------|--------|-------|
| `TELEGRAM_BOT_TOKEN` | Not set | Telegram alerting is not active |
| `TELEGRAM_CHAT_ID` | Not set | Telegram alerting is not active |
| `GITHUB_TOKEN` | Automatic | Provided by GitHub Actions, no rotation needed |

No manual secret rotation is currently required. If Telegram alerting is enabled in the future:
1. Go to **Settings** → **Secrets and variables** → **Actions**.
2. Update `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## 8. Common Issues

### "Panels not built"

**Cause:** The vintage was stored but `python -m nowcast.transform.panels` failed.

**Fix:**
1. Check which series are missing in the vintage: look at the run log.
2. Missing series may cause the panel builder to fail if required variables have no data.
3. Rerun with `catch_up = true` to retry failed downloads.
4. If the series is genuinely unavailable, the panel builder should still work with the remaining data — file a bug if it doesn't.

### "DFM estimation failed"

**Cause:** The DFM could not converge, usually because too few variables have data for the target quarter.

**Fix:**
1. Check data availability on the Data Monitor page.
2. Early in the quarter (m1), many series may not yet be available — this is expected.
3. If the variable count is very low (<5 monthly variables), the DFM may not be identifiable with 2 factors. Consider reducing to 1 factor temporarily.

### "Decomposition skipped"

**Cause:** No previous run state exists. This is normal for the first run.

**Fix:** No action needed. The decomposition requires state from a previous run (saved in `store/state/`). After the second run, decomposition will be available.

### "Site build failed"

**Cause:** Missing report dependencies or a template rendering error.

**Fix:**
1. Ensure `pip install -e ".[report]"` installs successfully (plotly, weasyprint, and their dependencies).
2. Check the error log for missing template variables or chart generation failures.
3. WeasyPrint requires system libraries (libpango, libcairo). On Ubuntu: `apt-get install libpango-1.0-0 libcairo2`.

### "Quality check failed for series X"

**Cause:** A data point failed the range or jump check (value outside expected bounds or a large revision).

**Fix:**
1. Check whether the value is genuinely anomalous or reflects a real economic event.
2. If real: rerun with `--accept X` to suppress the check for that series.
3. If an error: report to the data source. Use the previous vintage's value or manual inbox.
