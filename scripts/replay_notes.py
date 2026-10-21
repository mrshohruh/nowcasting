#!/usr/bin/env python3
"""Replay notes across the backtest window and select 6 sample notes.

This script:
1. Iterates over monthly run dates (like the replay engine)
2. At each date, builds panels, runs the DFM, and if a previous state exists,
   runs the four-run decomposition
3. Saves the run state so the next iteration can decompose against it
4. Generates English and Uzbek notes for each run date
5. Selects 6 diverse sample notes for user approval

Usage:
    python scripts/replay_notes.py --store store --out docs/sample_notes

The 6 samples are chosen to cover:
    - An early run (no decomposition, first note)
    - A large upward revision
    - A large downward revision
    - A month-1 nowcast
    - A month-3 nowcast
    - One with interesting sector dynamics
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nowcast.explain.narrative import number_check, render_note  # noqa: E402
from nowcast.explain.news import decompose  # noqa: E402
from nowcast.explain.sectors import sector_contributions  # noqa: E402
from nowcast.explain.state import save_state  # noqa: E402
from nowcast.models.dfm import DFM  # noqa: E402
from nowcast.models.replay import _classify_horizon, _target_quarters  # noqa: E402
from nowcast.store.vintages import Store  # noqa: E402
from nowcast.transform.panels import build_panels  # noqa: E402

logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load model configuration."""
    cfg_path = ROOT / "config" / "model.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def run_replay_notes(
    store_path: str,
    out_dir: str,
    start: str = "2022-01-01",
    end: str = "2026-09-01",
    max_dates: int | None = None,
) -> list[dict]:
    """Run the full replay with notes.

    Returns a list of note metadata dicts for sample selection.
    """
    store = Store(store_path)
    cfg = load_config()
    dfm_cfg = cfg.get("dfm", {})

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Temp state dir for replay (simulates sequential runs)
    state_dir = out / "_state"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir()

    # DFM variable list
    var_sections = cfg.get("variables", {})
    variable_ids = []
    for freq_block in var_sections.values():
        for block_vars in freq_block.values():
            variable_ids.extend(block_vars)

    dfm = DFM(
        n_factors=dfm_cfg.get("n_factors", 2),
        factor_orders=dfm_cfg.get("factor_orders", 2),
        idiosyncratic_ar1=dfm_cfg.get("idiosyncratic_ar1", True),
        em_max_iter=dfm_cfg.get("em_max_iter", 250),
        variable_ids=variable_ids,
    )

    run_dates = pd.date_range(start, end, freq="MS")
    if max_dates:
        run_dates = run_dates[:max_dates]

    all_notes: list[dict] = []
    n_total = len(run_dates)

    for i, rd_ts in enumerate(run_dates):
        rd = rd_ts.date()
        pct = (i + 1) / n_total * 100
        logger.info("[%3d/%d  %5.1f%%] %s", i + 1, n_total, pct, rd)

        # Build panels
        try:
            panels = build_panels(rd, store=store)
        except Exception as e:
            logger.warning("Panel build failed for %s: %s", rd, e)
            continue

        target_quarters = _target_quarters(rd)

        for tq in target_quarters:
            horizon = _classify_horizon(rd, tq)

            # Run DFM
            t0 = time.monotonic()
            try:
                pred = dfm.predict(panels, tq)
            except Exception as e:
                logger.warning("DFM failed on %s -> %s: %s", rd, tq, e)
                continue
            elapsed = time.monotonic() - t0

            gdp_yoy = pred.get("gdp_yoy", np.nan)
            if np.isnan(gdp_yoy):
                continue

            # Run decomposition against previous state
            try:
                decomp = decompose(
                    panels_new=panels,
                    target_quarter=tq,
                    state_dir=state_dir,
                    variable_ids=variable_ids,
                    n_factors=dfm_cfg.get("n_factors", 2),
                    factor_orders=dfm_cfg.get("factor_orders", 2),
                    em_max_iter=dfm_cfg.get("em_max_iter", 250),
                )
            except Exception as e:
                logger.warning("Decomposition failed for %s -> %s: %s", rd, tq, e)
                decomp = None

            # Run sector contributions
            try:
                bridge_cfg = cfg.get("bridges", {})
                sectors = sector_contributions(panels, tq, bridge_map=bridge_cfg)
            except Exception as e:
                logger.warning("Sector analysis failed for %s -> %s: %s", rd, tq, e)
                sectors = []

            # Render notes
            note_en = render_note(
                decomp, sectors, lang="en", horizon=horizon,
                run_date=str(rd), target_quarter=str(tq), new_nowcast=gdp_yoy,
            )
            note_uz = render_note(
                decomp, sectors, lang="uz", horizon=horizon,
                run_date=str(rd), target_quarter=str(tq), new_nowcast=gdp_yoy,
            )

            # Number check
            warnings_en = number_check(note_en)
            warnings_uz = number_check(note_uz)

            meta = {
                "run_date": str(rd),
                "target_quarter": str(tq),
                "horizon": horizon,
                "gdp_yoy": gdp_yoy,
                "has_decomposition": decomp is not None,
                "total_change": decomp.total_change if decomp else None,
                "revision_effect": decomp.revision_effect if decomp else None,
                "news_effect": decomp.news_effect if decomp else None,
                "reestimation_effect": decomp.reestimation_effect if decomp else None,
                "residual": decomp.residual if decomp else None,
                "n_news_items": len(decomp.news_items) if decomp else 0,
                "n_sectors": len(sectors),
                "warnings_en": warnings_en,
                "warnings_uz": warnings_uz,
                "elapsed_sec": elapsed,
                "note_en": note_en,
                "note_uz": note_uz,
            }
            all_notes.append(meta)

        # Save state for next iteration (only for current quarter, latest target)
        if target_quarters:
            tq_main = target_quarters[0]
            pred_main = dfm.predict(panels, tq_main)
            gdp_main = pred_main.get("gdp_yoy", np.nan)
            if not np.isnan(gdp_main):
                try:
                    # Get the DFM result object for parameter persistence
                    dfm_result = getattr(dfm, "_last_result", None)
                    save_state(
                        state_dir, rd, panels,
                        {str(tq_main): gdp_main},
                        dfm_result=dfm_result,
                        dfm_config={
                            **dfm_cfg,
                            "n_monthly_vars": pred_main.get("info", {}).get("n_monthly_vars", 0),
                            "n_quarterly_vars": pred_main.get("info", {}).get("n_quarterly_vars", 0),
                        },
                    )
                except Exception as e:
                    logger.warning("State save failed for %s: %s", rd, e)

    # Save all notes metadata
    meta_path = out / "all_notes.json"
    # Don't save full note text in JSON (it's in the individual files)
    meta_for_json = []
    for m in all_notes:
        m_copy = {k: v for k, v in m.items() if k not in ("note_en", "note_uz")}
        meta_for_json.append(m_copy)
    meta_path.write_text(json.dumps(meta_for_json, indent=2, default=str), encoding="utf-8")

    return all_notes


def select_samples(notes: list[dict], n: int = 6) -> list[dict]:
    """Select n diverse sample notes for user approval.

    Selection criteria:
    1. First run (no decomposition) — shows the baseline note format
    2. Largest upward revision
    3. Largest downward revision
    4. A month-1 (early) nowcast with decomposition
    5. A month-3 (full data) nowcast with decomposition
    6. Most diverse sector dynamics (biggest spread in sector contributions)
    """
    selected = []
    used_dates = set()

    def _pick(candidate: dict | None, reason: str):
        if candidate and candidate["run_date"] not in used_dates:
            candidate["selection_reason"] = reason
            selected.append(candidate)
            used_dates.add(candidate["run_date"])

    # 1. First run without decomposition
    no_decomp = [n for n in notes if not n["has_decomposition"]]
    _pick(no_decomp[0] if no_decomp else None, "First run (no prior comparison)")

    # 2. Largest upward revision
    with_change = [n for n in notes if n["total_change"] is not None
                   and n["run_date"] not in used_dates]
    if with_change:
        up = max(with_change, key=lambda x: x["total_change"] or 0)
        if up["total_change"] and up["total_change"] > 0:
            _pick(up, f"Largest upward revision (+{up['total_change']:.2f} pp)")

    # 3. Largest downward revision
    if with_change:
        down = min(with_change, key=lambda x: x["total_change"] or 0)
        if down["total_change"] and down["total_change"] < 0:
            _pick(down, f"Largest downward revision ({down['total_change']:.2f} pp)")

    # 4. Month-1 nowcast
    m1 = [n for n in notes if n["horizon"] == "nowcast_m1"
          and n["has_decomposition"] and n["run_date"] not in used_dates]
    if m1:
        _pick(m1[len(m1) // 2], "Month-1 early nowcast")

    # 5. Month-3 nowcast
    m3 = [n for n in notes if n["horizon"] == "nowcast_m3"
          and n["has_decomposition"] and n["run_date"] not in used_dates]
    if m3:
        _pick(m3[len(m3) // 2], "Month-3 full-data nowcast")

    # 6. Fill remaining slots with diverse dates
    remaining = [n for n in notes if n["run_date"] not in used_dates
                 and n["has_decomposition"] and n.get("n_news_items", 0) > 0]
    remaining.sort(key=lambda x: x.get("n_news_items", 0), reverse=True)
    for r in remaining:
        if len(selected) >= n:
            break
        _pick(r, f"Rich decomposition ({r.get('n_news_items', 0)} news items)")

    return selected[:n]


def write_samples(samples: list[dict], out_dir: Path):
    """Write selected sample notes to individual files."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, s in enumerate(samples, 1):
        # English note
        en_path = out_dir / f"sample_{i:02d}_{s['run_date']}_en.txt"
        header = f"# Sample {i}: {s.get('selection_reason', '')}\n"
        header += f"# Run date: {s['run_date']}, Target: {s['target_quarter']}, Horizon: {s['horizon']}\n"
        header += f"# GDP y/y: {s['gdp_yoy']:.1f}%\n"
        if s.get("warnings_en"):
            header += f"# WARNINGS: {'; '.join(s['warnings_en'])}\n"
        header += "\n"
        en_path.write_text(header + s["note_en"], encoding="utf-8")

        # Uzbek note
        uz_path = out_dir / f"sample_{i:02d}_{s['run_date']}_uz.txt"
        header_uz = f"# Namuna {i}: {s.get('selection_reason', '')}\n"
        header_uz += f"# Sana: {s['run_date']}, Chorak: {s['target_quarter']}, Gorizont: {s['horizon']}\n"
        header_uz += f"# YaIM: {s['gdp_yoy']:.1f}%\n"
        if s.get("warnings_uz"):
            header_uz += f"# OGOHLANTIRISHLAR: {'; '.join(s['warnings_uz'])}\n"
        header_uz += "\n"
        uz_path.write_text(header_uz + s["note_uz"], encoding="utf-8")

    # Write summary
    summary_path = out_dir / "SUMMARY.md"
    lines = ["# Sample Notes for Approval\n\n"]
    for i, s in enumerate(samples, 1):
        lines.append(f"## Sample {i}: {s.get('selection_reason', '')}\n")
        lines.append(f"- **Run date**: {s['run_date']}")
        lines.append(f"- **Target**: {s['target_quarter']}")
        lines.append(f"- **Horizon**: {s['horizon']}")
        lines.append(f"- **GDP y/y**: {s['gdp_yoy']:.1f}%")
        if s["total_change"] is not None:
            lines.append(f"- **Change**: {s['total_change']:+.2f} pp")
        if s.get("residual") is not None:
            lines.append(f"- **Decomposition residual**: {s['residual']:.4f} pp")
        lines.append("")
        lines.append("**English:**\n")
        lines.append(f"```\n{s['note_en']}\n```\n")
        lines.append("**O'zbekcha:**\n")
        lines.append(f"```\n{s['note_uz']}\n```\n")
        lines.append("---\n")

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote %d samples to %s", len(samples), out_dir)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description="Replay notes across the backtest.")
    ap.add_argument("--store", required=True, help="Path to vintage store (data branch checkout)")
    ap.add_argument("--out", default="docs/sample_notes", help="Output directory")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--max-dates", type=int, default=None, help="Limit run dates (for testing)")
    args = ap.parse_args(argv)

    logger.info("Starting replay notes: %s to %s", args.start, args.end)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        notes = run_replay_notes(
            args.store, args.out, args.start, args.end, args.max_dates,
        )

    if not notes:
        logger.error("No notes generated — check the store and date range")
        return 1

    logger.info("Generated %d notes across %d run dates",
                len(notes), len({n["run_date"] for n in notes}))

    # Check decomposition residuals
    residuals = [n["residual"] for n in notes if n["residual"] is not None]
    if residuals:
        max_resid = max(residuals)
        logger.info("Max decomposition residual: %.4f pp (threshold: 0.001)", max_resid)
        if max_resid > 0.001:
            logger.warning("Residual exceeds 0.001 pp threshold in %d notes",
                           sum(1 for r in residuals if r > 0.001))

    # Select and write samples
    samples = select_samples(notes)
    write_samples(samples, Path(args.out))

    logger.info("Done! Review samples in %s/SUMMARY.md", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
