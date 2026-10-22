#!/usr/bin/env python3
"""Shadow-run harness for pre-launch dry runs.

Usage
-----
    # Shadow run for Feb 1 2027 (first dry run):
    python scripts/shadow_run.py --as-of 2027-02-01 --store store --out _shadow/2027-02-01

    # Shadow run for Mar 1 2027 (second dry run):
    python scripts/shadow_run.py --as-of 2027-03-01 --store store --out _shadow/2027-03-01

    # Full comparison of two shadow runs:
    python scripts/shadow_run.py --compare _shadow/2027-02-01 _shadow/2027-03-01

A shadow run executes the full production pipeline (produce.py) but writes to
an isolated output directory and never touches the live state or site.  It also
generates a diagnostics report comparing outputs to expectations.

Shadow runs are scheduled for:
  - 2027-02-01: First complete dry run.  Validates ingest → DFM → site → PDF.
  - 2027-03-01: Second dry run.  Also validates news decomposition (needs two
    consecutive runs) and the revision pathway between shadow states.

Go-live: 2027-04-01 — first public release.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SHADOW_CHECKLIST = [
    ("Pipeline exit code 0", "pipeline_ok"),
    ("Site directory generated", "site_exists"),
    ("All 16 HTML pages present (8 × 2 langs)", "pages_complete"),
    ("PDF notes generated (EN + UZ)", "pdfs_ok"),
    ("Nowcast value is plausible (1–15% y/y)", "nowcast_plausible"),
    ("Charts render valid JSON", "charts_valid"),
    ("Downloads directory populated", "downloads_ok"),
    ("State file written", "state_ok"),
]


def run_shadow(as_of: str, store: str, state_dir: str, out: str, verbose: bool = False) -> dict:
    """Execute a shadow run and return a diagnostics dict."""
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)

    site_dir = out_path / "site"
    pdf_dir = out_path / "pdf"
    shadow_state = out_path / "state"
    shadow_state.mkdir(exist_ok=True)

    diag: dict = {"as_of": as_of, "timestamp": datetime.now().isoformat(), "checks": {}}

    # 1. Run the production pipeline
    cmd = [
        sys.executable, "scripts/produce.py",
        "--store", store,
        "--state-dir", str(shadow_state),
        "--out", str(site_dir),
        "--as-of", as_of,
    ]
    if verbose:
        cmd.append("-v")

    logger.info("Running shadow pipeline: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    diag["checks"]["pipeline_ok"] = result.returncode == 0
    diag["stdout"] = result.stdout[-2000:] if result.stdout else ""
    diag["stderr"] = result.stderr[-2000:] if result.stderr else ""

    if result.returncode != 0:
        logger.error("Pipeline failed (exit %d):\n%s", result.returncode, result.stderr[-500:])
        # Still check what was produced
    else:
        logger.info("Pipeline completed successfully")

    # 2. Check site files
    diag["checks"]["site_exists"] = site_dir.exists()

    html_files = list(site_dir.rglob("*.html")) if site_dir.exists() else []
    diag["checks"]["pages_complete"] = len(html_files) >= 17  # 8×2 + index.html
    diag["html_count"] = len(html_files)

    # 3. Check PDFs
    pdf_files = list(site_dir.rglob("*.pdf")) if site_dir.exists() else []
    diag["checks"]["pdfs_ok"] = len(pdf_files) >= 2
    diag["pdf_count"] = len(pdf_files)

    # 4. Check nowcast plausibility
    state_files = list(shadow_state.glob("*.json"))
    nowcast_val = None
    if state_files:
        diag["checks"]["state_ok"] = True
        try:
            state_data = json.loads(state_files[0].read_text())
            nowcast_val = state_data.get("new_nowcast", state_data.get("nowcast"))
            if nowcast_val is not None:
                diag["checks"]["nowcast_plausible"] = 1.0 <= float(nowcast_val) <= 15.0
                diag["nowcast_value"] = nowcast_val
            else:
                diag["checks"]["nowcast_plausible"] = False
        except (json.JSONDecodeError, ValueError):
            diag["checks"]["nowcast_plausible"] = False
    else:
        diag["checks"]["state_ok"] = False
        diag["checks"]["nowcast_plausible"] = False

    # 5. Check chart JSON validity
    charts_valid = True
    for html_file in html_files:
        content = html_file.read_text(encoding="utf-8")
        if "var spec = " in content:
            # Extract JSON between "var spec = " and ";\n"
            for line in content.split("\n"):
                if "var spec = " in line:
                    json_str = line.split("var spec = ", 1)[1].rstrip(";").strip()
                    try:
                        json.loads(json_str)
                    except json.JSONDecodeError:
                        charts_valid = False
                        logger.warning("Invalid chart JSON in %s", html_file)
    diag["checks"]["charts_valid"] = charts_valid

    # 6. Check downloads
    dl_dir = site_dir / "downloads" if site_dir.exists() else None
    if dl_dir and dl_dir.exists():
        dl_files = list(dl_dir.iterdir())
        diag["checks"]["downloads_ok"] = len(dl_files) > 0
        diag["download_count"] = len(dl_files)
    else:
        diag["checks"]["downloads_ok"] = False
        diag["download_count"] = 0

    # 7. Summary
    passed = sum(1 for v in diag["checks"].values() if v)
    total = len(diag["checks"])
    diag["summary"] = f"{passed}/{total} checks passed"
    diag["all_passed"] = passed == total

    # Write diagnostics
    diag_path = out_path / "diagnostics.json"
    diag_path.write_text(json.dumps(diag, indent=2, default=str), encoding="utf-8")
    logger.info("Diagnostics written to %s", diag_path)

    # Write human-readable report
    report_lines = [
        f"# Shadow Run Report — {as_of}",
        f"Timestamp: {diag['timestamp']}",
        f"Result: {diag['summary']}",
        "",
        "## Checklist",
    ]
    for label, key in SHADOW_CHECKLIST:
        status = "PASS" if diag["checks"].get(key) else "FAIL"
        report_lines.append(f"  [{status}] {label}")

    if nowcast_val is not None:
        report_lines.extend(["", f"## Nowcast Value: {nowcast_val}% y/y"])

    report_lines.extend([
        "",
        f"## Files: {diag.get('html_count', 0)} HTML, {diag.get('pdf_count', 0)} PDF, {diag.get('download_count', 0)} downloads",
    ])

    report_path = out_path / "report.txt"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    logger.info("Report written to %s", report_path)

    return diag


def compare_runs(dir1: str, dir2: str) -> None:
    """Compare diagnostics from two shadow runs."""
    d1_path = Path(dir1) / "diagnostics.json"
    d2_path = Path(dir2) / "diagnostics.json"

    if not d1_path.exists() or not d2_path.exists():
        logger.error("Both directories must contain diagnostics.json")
        sys.exit(1)

    d1 = json.loads(d1_path.read_text())
    d2 = json.loads(d2_path.read_text())

    print(f"\n{'='*60}")
    print(f"Shadow Run Comparison")
    print(f"{'='*60}")
    print(f"  Run 1: {d1['as_of']}  ({d1['summary']})")
    print(f"  Run 2: {d2['as_of']}  ({d2['summary']})")
    print()

    n1 = d1.get("nowcast_value")
    n2 = d2.get("nowcast_value")
    if n1 is not None and n2 is not None:
        change = float(n2) - float(n1)
        print(f"  Nowcast: {n1}% → {n2}%  (change: {change:+.2f} pp)")
    print()

    # Check regressions
    for label, key in SHADOW_CHECKLIST:
        s1 = d1["checks"].get(key, False)
        s2 = d2["checks"].get(key, False)
        if s1 and not s2:
            print(f"  ⚠ REGRESSION: {label} (passed in run 1, failed in run 2)")
        elif not s1 and s2:
            print(f"  ✓ FIXED: {label} (failed in run 1, passed in run 2)")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow run harness")
    parser.add_argument("--as-of", help="Run date (YYYY-MM-DD)")
    parser.add_argument("--store", default="store", help="Data store path")
    parser.add_argument("--state-dir", help="State directory (default: <out>/state)")
    parser.add_argument("--out", help="Output directory for this shadow run")
    parser.add_argument("--compare", nargs=2, metavar="DIR", help="Compare two shadow runs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.compare:
        compare_runs(*args.compare)
    elif args.as_of and args.out:
        diag = run_shadow(
            as_of=args.as_of,
            store=args.store,
            state_dir=args.state_dir or str(Path(args.out) / "state"),
            out=args.out,
            verbose=args.verbose,
        )
        # Exit with error if any check failed
        if not diag["all_passed"]:
            logger.warning("Some checks failed: %s", diag["summary"])
            sys.exit(1)
    else:
        parser.error("Provide --as-of and --out, or --compare DIR1 DIR2")


if __name__ == "__main__":
    main()
