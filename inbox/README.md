# Manual inbox

If a source is broken, download its file by hand and upload it to `inbox/<series_id>/` on this
branch (GitHub: Add file > Upload files, branch `data`). The next run takes the newest file in
that folder instead of downloading, parses it exactly like a download, and moves it to
`inbox/_used/<run>/<series_id>/`. The file must be in the format the source publishes (SIAT JSON,
SDMX XML, xlsx; for usd_rate a CSV with columns date,rate). See docs/runbook_ingest.md on `main`.
