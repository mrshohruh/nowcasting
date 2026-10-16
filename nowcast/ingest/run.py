"""One ingestion run: download every series, check it, store the vintage, report.

    python -m nowcast.ingest.run --store <folder> [--catch-up] [--series id,id] [--run-date YYYY-MM-DD]

* full run (default): every catalogue series;
* ``--catch-up``: only the series that failed in this month's latest run (the scheduled run on
  the 2nd); if this month has no run yet it does a full run, and if nothing failed it does
  nothing;
* ``--series``: just these series;
* ``--accept``: series whose failed *checks* (not download or parse errors) are accepted for this
  run, after someone has confirmed on the publisher's site that the change is real (e.g. a new
  unit). The run then stores the data and the new unit becomes the reference.

A source that breaks never stops the run: its series are marked fail, the store keeps what it
had for them, and every other series is processed. Results:
``runs/<run_id>/report.json`` (status of each series) and ``runs/<run_id>/changes.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

from nowcast.catalogue import load as load_catalogue
from nowcast.catalogue import validate
from nowcast.ingest import quality
from nowcast.ingest.base import FetchContext, Raw, adapter_for
from nowcast.store.changes import compare, delta_rows
from nowcast.store.seed import load_seed
from nowcast.store.vintages import Store

TASHKENT = dt.timezone(dt.timedelta(hours=5))  # Uzbekistan has no daylight saving time
EXT = {".json": "json", ".xml": "xml", ".xlsx": "xlsx", ".csv": "csv"}


def today_tashkent() -> dt.date:
    return dt.datetime.now(TASHKENT).date()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


STORE_README = """# Data store of the nowcasting platform

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

Read it in Python with `nowcast.store.vintages.Store("<this folder>")`: `.latest()`, `.as_of("2026-12-01")`.
"""
INBOX_README = """# Manual inbox

If a source is broken, download its file by hand and upload it to `inbox/<series_id>/` on this
branch (GitHub: Add file > Upload files, branch `data`). The next run takes the newest file in
that folder instead of downloading, parses it exactly like a download, and moves it to
`inbox/_used/<run>/<series_id>/`. The file must be in the format the source publishes (SIAT JSON,
SDMX XML, xlsx; for usd_rate a CSV with columns date,rate). See docs/runbook_ingest.md on `main`.
"""


def ensure_readmes(store: Store) -> None:
    files = {store.root / "README.md": STORE_README, store.inbox_dir / "README.md": INBOX_README}
    for path, text in files.items():
        if not path.exists():
            path.write_text(text, encoding="utf-8")


def inbox_file(store: Store, series_id: str) -> Path | None:
    d = store.inbox_dir / series_id
    if not d.is_dir():
        return None
    files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in EXT)
    return files[-1] if files else None


def select(
    store: Store, catalogue: list[dict], run_date: dt.date, only: list[str] | None, catch_up: bool
) -> tuple[list[dict], str, str | None]:
    """Return (series to run, mode, previous run id used for catch-up)."""
    if only:
        unknown = sorted(set(only) - {s["id"] for s in catalogue})
        if unknown:
            raise SystemExit(f"unknown series: {unknown}")
        return [s for s in catalogue if s["id"] in only], "selected", None
    if catch_up:
        month = run_date.strftime("%Y-%m")
        this_month = [r for r in store.runs() if not r.startswith("seed-") and r.startswith(month)]
        if this_month:
            prev = this_month[-1]
            failed = {sid for sid, s in store.report(prev)["series"].items() if s["status"] == "fail"}
            return [s for s in catalogue if s["id"] in failed], "catch-up", prev
    return list(catalogue), "full", None


def run(
    store_dir: str | Path,
    run_date: dt.date | None = None,
    only: list[str] | None = None,
    catch_up: bool = False,
    trigger: str = "manual",
    catalogue: list[dict] | None = None,
    session=None,
    accept: list[str] | None = None,
) -> dict:
    run_date = run_date or today_tashkent()
    accept = set(accept or [])
    store = Store(store_dir)
    catalogue = catalogue or load_catalogue()
    problems = validate(catalogue)
    if problems:
        raise SystemExit("catalogue is invalid:\n" + "\n".join(problems))
    seeded = load_seed(store, catalogue)
    ensure_readmes(store)

    todo, mode, prev_run = select(store, catalogue, run_date, only, catch_up)
    if not todo:
        return {
            "skipped": True,
            "mode": mode,
            "previous_run": prev_run,
            "message": f"catch-up: nothing failed in {prev_run}, nothing to retry",
        }

    run_id = store.new_run_id(run_date)
    started = _now()
    latest = store.latest()
    prev_by_series = {sid: g[["item", "ref_period", "value"]] for sid, g in latest.groupby("series_id")}
    ctx = FetchContext(run_date=run_date, last_period=store.last_periods())
    if session is not None:
        ctx.session = session

    # download each file once, in catalogue order
    fetched: dict[str, tuple[Raw | None, BaseException | None, dt.datetime]] = {}
    adapters = {s["id"]: adapter_for(s, ctx) for s in todo}
    inbox = {s["id"]: inbox_file(store, s["id"]) for s in todo}

    series_report: dict[str, dict] = {}
    changes_out: dict[str, dict] = {}
    rows: list[pd.DataFrame] = []
    used_inbox: list[tuple[str, Path]] = []

    for s in todo:
        sid, a = s["id"], adapters[s["id"]]
        rep: dict = {
            "source": s["source"],
            "file": a.file_key,
            "url": s["url"],
            "status": "ok",
            "messages": [],
        }
        try:
            if inbox[sid] is not None:
                p = inbox[sid]
                raw = Raw(p.read_bytes(), EXT[p.suffix.lower()], f"inbox/{sid}/{p.name}", {"inbox": p.name})
                fetched_at = _now()
                rep["via_inbox"] = p.name
            else:
                if a.file_key not in fetched:
                    t0 = time.monotonic()
                    try:
                        fetched[a.file_key] = (a.fetch(), None, _now())
                    except Exception as e:  # noqa: BLE001 - any source failure is reported, not raised
                        fetched[a.file_key] = (None, e, _now())
                    rep["seconds"] = round(time.monotonic() - t0, 1)
                    if a.pause_after_fetch:
                        time.sleep(a.pause_after_fetch)
                raw, err, fetched_at = fetched[a.file_key]
                if err is not None:
                    raise err
            assert raw is not None
            rep["raw"], rep["sha256"] = store.save_raw(run_id, s, raw, fetched_at)
            if raw.info.get("updated_at"):
                rep["updated_at"] = raw.info["updated_at"]
            parsed = a.parse(raw)
            prev = prev_by_series.get(sid, pd.DataFrame(columns=["item", "ref_period", "value"]))
            ch = compare(sid, prev, parsed.data)
            prev_latest = prev["ref_period"].max() if len(prev) else None
            q = quality.check(s, parsed, store.load_meta(sid), ch, prev_latest, run_date)
            rep.update(
                status=q.status,
                messages=q.messages,
                points=len(parsed.data),
                **q.info,
                changes=ch.summary(),
                new=len(ch.new),
                revised=len(ch.revised),
                removed=len(ch.removed),
            )
            if q.status == "fail" and sid in accept:
                q.status = "warn"
                q.messages.append("warn: failed checks accepted by hand for this run (--accept)")
                rep.update(status=q.status, messages=q.messages, accepted=True)
            if q.status != "fail":
                rows.append(delta_rows(ch, run_date, run_id, rep["sha256"], fetched_at))
                store.save_meta(sid, parsed.meta)
                changes_out[sid] = ch.to_json()
                if inbox[sid] is not None:
                    used_inbox.append((sid, inbox[sid]))
            else:
                rep["messages"].append("fail: left out of the store for this run")
        except Exception as e:  # noqa: BLE001
            rep["status"] = "fail"
            rep["error"] = f"{type(e).__name__}: {e}"[:500]
            rep["messages"].append(f"fail: {rep['error']}")
            rep["traceback"] = traceback.format_exc(limit=4)[-2000:]
        series_report[sid] = rep

    if rows:
        store.write_observations(run_id, pd.concat(rows, ignore_index=True))
    for sid, p in used_inbox:
        dest = store.inbox_dir / "_used" / run_id / sid
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), dest / p.name)

    counts = {k: sum(r["status"] == k for r in series_report.values()) for k in ("ok", "warn", "fail")}
    sources: dict[str, dict] = {}
    for sid, r in series_report.items():
        src = sources.setdefault(
            r["file"], {"source": r["source"], "url": r["url"], "series": [], "failed": [], "errors": []}
        )
        src["series"].append(sid)
        if r["status"] == "fail":
            src["failed"].append(sid)
            if r.get("error") and r["error"] not in src["errors"]:
                src["errors"].append(r["error"])
    report = {
        "run_id": run_id,
        "vintage_date": run_date.isoformat(),
        "trigger": trigger,
        "mode": mode,
        "previous_run": prev_run,
        "seeded": seeded,
        "started_at": started.isoformat(),
        "finished_at": _now().isoformat(),
        "counts": counts,
        "new_points": sum(r.get("new", 0) for r in series_report.values() if r["status"] != "fail"),
        "revised_points": sum(r.get("revised", 0) for r in series_report.values() if r["status"] != "fail"),
        "removed_points": sum(r.get("removed", 0) for r in series_report.values() if r["status"] != "fail"),
        "series": series_report,
        "sources": sources,
    }
    store.write_run_file(run_id, "report.json", report)
    store.write_run_file(
        run_id,
        "changes.json",
        {
            "run_id": run_id,
            "vintage_date": run_date.isoformat(),
            "compared_with": "the store before this run",
            "series": changes_out,
        },
    )
    store.export_latest_csv()
    return report


def summary_text(report: dict) -> str:
    if report.get("skipped"):
        return report["message"]
    c = report["counts"]
    lines = [
        f"Run {report['run_id']} ({report['mode']}, {report['trigger']}): "
        f"{c['ok']} ok, {c['warn']} warn, {c['fail']} fail; {report['new_points']} new points, "
        f"{report['revised_points']} revised, {report['removed_points']} removed."
    ]
    for sid, r in report["series"].items():
        line = f"  {r['status']:4} {sid:22} {r.get('changes', '')}"
        extra = [m.split(": ", 1)[1] for m in r["messages"] if not m.startswith("ok")]
        if extra:
            line += "  | " + "; ".join(extra)
        lines.append(line)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", required=True, help="store folder (the data branch checkout)")
    ap.add_argument("--run-date", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--series", default="", help="comma-separated series ids; empty = all")
    ap.add_argument("--catch-up", action="store_true")
    ap.add_argument("--trigger", default="manual")
    ap.add_argument("--accept", default="", help="series whose failed checks are accepted for this run")
    args = ap.parse_args(argv)
    only = [x.strip() for x in args.series.split(",") if x.strip()] or None
    accept = [x.strip() for x in args.accept.split(",") if x.strip()]
    report = run(args.store, args.run_date, only, args.catch_up, args.trigger, accept=accept)
    text = summary_text(report)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("```\n" + text + "\n```\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(
                f"run_id={report.get('run_id', '')}\nskipped={str(report.get('skipped', False)).lower()}\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
