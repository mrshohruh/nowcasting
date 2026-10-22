"""Production nowcasting pipeline.

Orchestrates: panels → DFM → bridges → decomposition → narrative → site → PDF → downloads.

Usage::

    python scripts/produce.py --store store --state-dir state --out _site
    python scripts/produce.py --store store --state-dir state --out _site --as-of 2026-10-01

This script is called by the GitHub Actions monthly workflow *after* ingestion
has committed the data vintage to the ``data`` branch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

# Ensure project root is importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────


def determine_target_quarter(as_of: dt.date) -> pd.Period:
    """Quarter the nowcast targets (the quarter *as_of* falls in)."""
    return pd.Period(as_of, freq="Q")


def determine_horizon(as_of: dt.date, target_quarter: pd.Period) -> str:
    """Classify the horizon by month-within-quarter."""
    q_start = target_quarter.start_time.date()
    if as_of < q_start:
        return "forecast"
    month_in_q = ((as_of.month - q_start.month) % 12) + 1
    if month_in_q >= 4:
        return "backcast"
    return {1: "nowcast_m1", 2: "nowcast_m2", 3: "nowcast_m3"}[month_in_q]


def load_model_config() -> dict:
    """Load ``config/model.yaml``."""
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "model.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f)
    return {}


# ── monitor ────────────────────────────────────────────────────────────────


def build_monitor_data(panels, as_of: dt.date) -> list[dict]:
    """Build data-availability monitor rows (one per variable)."""
    from nowcast.explain.news import VARIABLE_LABELS

    cat_path = Path(__file__).resolve().parent.parent / "config" / "series_catalogue.yaml"
    catalogue: dict = {}
    if cat_path.exists():
        with open(cat_path) as f:
            catalogue = yaml.safe_load(f) or {}

    monitor: list[dict] = []
    for _, row in panels.meta.iterrows():
        var_id = row["variable"]
        freq = row.get("freq", "M")
        var_data = panels.yoy[panels.yoy["variable"] == var_id]
        if var_data.empty:
            continue
        available = var_data.dropna(subset=["value"])
        last_period = str(available["period"].max()) if not available.empty else ""

        cat_info = catalogue.get(var_id, {})
        months_after = cat_info.get("months_after", 1)

        period_status: dict[str, str] = {}
        if freq == "M":
            for i in range(6):
                p = pd.Period(as_of, freq="M") - i
                p_str = str(p)
                has = var_data[var_data["period"] == p_str].dropna(subset=["value"]).shape[0] > 0
                if has:
                    period_status[p_str] = "available"
                elif (as_of - p.end_time.date()).days < months_after * 30:
                    period_status[p_str] = "not_due"
                else:
                    period_status[p_str] = "missing"

        monitor.append({
            "variable": var_id,
            "label": VARIABLE_LABELS.get(var_id, var_id),
            "freq": freq,
            "source": cat_info.get("source", ""),
            "last_period": last_period,
            "periods": period_status,
        })
    return monitor


# ── history ────────────────────────────────────────────────────────────────


def build_nowcast_history(state_dir: Path) -> list[dict]:
    """Previous nowcasts read from saved state."""
    from nowcast.explain.state import load_state

    history: list[dict] = []
    state = load_state(state_dir)
    if state and state.nowcast_table:
        for quarter, yoy in state.nowcast_table.items():
            history.append({
                "run_date": state.run_date,
                "target_quarter": quarter,
                "gdp_yoy": yoy,
            })
    return history


def build_nowcast_paths(
    past_nowcasts: list[dict],
    current_quarter: str,
    live_points: list[dict],
    n_past: int = 6,
) -> dict[str, list[dict]]:
    """Nowcast evolution per target quarter, for the Nowcast Path page.

    Past quarters come from the backtest replays (one point per monthly run, ending with an
    ``official`` marker when GDP has been published); the current quarter takes the live runs.
    """
    horizons = {"forecast", "nowcast_m1", "nowcast_m2", "nowcast_m3"}
    by_q: dict[str, list[dict]] = {}
    actual: dict[str, float] = {}
    for r in past_nowcasts:
        if r.get("horizon") not in horizons:
            continue
        by_q.setdefault(r["target_quarter"], []).append(
            {"run_date": r["run_date"], "gdp_yoy": r["forecast"], "horizon": r["horizon"]})
        if r.get("actual") is not None:
            actual[r["target_quarter"]] = r["actual"]

    paths: dict[str, list[dict]] = {}
    for q in [q for q in sorted(by_q) if q != current_quarter][-n_past:]:
        pts = sorted(by_q[q], key=lambda p: p["run_date"])
        if q in actual:
            pts.append({"run_date": pts[-1]["run_date"], "gdp_yoy": actual[q], "official": True})
        paths[q] = pts

    # Current quarter: any early replays plus the live runs (a live run replaces a replay of that date)
    live = {p["run_date"]: p for p in live_points}
    earlier = [p for p in by_q.get(current_quarter, []) if p["run_date"] not in live]
    paths[current_quarter] = sorted(earlier + list(live.values()), key=lambda p: p["run_date"])
    return paths


# ── downloads ──────────────────────────────────────────────────────────────


def generate_downloads(site_ctx, decomposition, sectors, out_dir: Path) -> list[dict]:
    """CSV + Excel downloads into ``<out_dir>/downloads/``."""
    from nowcast.explain.sectors import format_sector_table

    dl = out_dir / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []

    # Nowcast history
    if site_ctx.nowcast_history:
        df = pd.DataFrame(site_ctx.nowcast_history)
        df.to_csv(dl / "nowcast_history.csv", index=False)
        files.append({"name": "nowcast_history.csv",
                       "path": "downloads/nowcast_history.csv",
                       "category": "history"})

    # Impact table
    if decomposition and decomposition.news_items:
        rows = [{
            "variable": it.variable, "label": it.label, "period": it.period,
            "surprise": it.surprise, "weight": it.weight,
            "impact": it.impact, "group": it.group,
        } for it in decomposition.news_items]
        pd.DataFrame(rows).to_csv(dl / "impact_table.csv", index=False)
        files.append({"name": "impact_table.csv",
                       "path": "downloads/impact_table.csv",
                       "category": "impacts"})

    # Sector contributions
    if sectors:
        format_sector_table(sectors).to_csv(dl / "sector_contributions.csv", index=False)
        files.append({"name": "sector_contributions.csv",
                       "path": "downloads/sector_contributions.csv",
                       "category": "sectors"})

    # Combined Excel workbook
    try:
        xlsx_name = f"nowcast_{site_ctx.target_quarter}.xlsx"
        xlsx_path = dl / xlsx_name
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as wr:
            if site_ctx.nowcast_history:
                pd.DataFrame(site_ctx.nowcast_history).to_excel(wr, sheet_name="History", index=False)
            if decomposition and decomposition.news_items:
                pd.DataFrame([{
                    "variable": it.variable, "label": it.label,
                    "period": it.period, "impact": it.impact, "group": it.group,
                } for it in decomposition.news_items]).to_excel(wr, sheet_name="Impacts", index=False)
            if sectors:
                format_sector_table(sectors).to_excel(wr, sheet_name="Sectors", index=False)
        files.append({"name": xlsx_name,
                       "path": f"downloads/{xlsx_name}",
                       "category": "combined"})
    except Exception as exc:
        logger.warning("Could not create Excel file: %s", exc)

    return files


# ── serialisation ──────────────────────────────────────────────────────────


def _serialize_decomposition(d) -> dict:
    """Decomposition → JSON-safe dict."""
    return {
        "target_quarter": d.target_quarter,
        "old_nowcast": d.old_nowcast,
        "new_nowcast": d.new_nowcast,
        "total_change": d.total_change,
        "revision_effect": d.revision_effect,
        "news_effect": d.news_effect,
        "reestimation_effect": d.reestimation_effect,
        "news_items": [{
            "variable": it.variable, "label": it.label,
            "period": it.period, "surprise": it.surprise,
            "weight": it.weight, "impact": it.impact, "group": it.group,
        } for it in d.news_items],
        "y_hat_2": d.y_hat_2,
        "y_hat_3": d.y_hat_3,
        "run_date": d.run_date,
        "prev_run_date": d.prev_run_date,
    }


# ── main pipeline ──────────────────────────────────────────────────────────


def run_pipeline(
    store_dir: str = "store",
    state_dir: str = "state",
    out_dir: str = "_site",
    as_of: dt.date | None = None,
    backtest_path: str | Path | None = None,
    available_quarters: list[str] | None = None,
) -> dict:
    """Execute the full production pipeline and return a summary dict."""
    from nowcast.explain.narrative import render_note
    from nowcast.explain.news import decompose
    from nowcast.explain.sectors import sector_contributions
    from nowcast.explain.state import load_state, save_state
    from nowcast.models.dfm import DFM
    from nowcast.report.pdf import NoteContext, generate_pdf_note
    from nowcast.report.site import SiteContext, build_site
    from nowcast.store.vintages import Store
    from nowcast.transform.panels import build_panels

    store_path = Path(store_dir)
    state_path = Path(state_dir)
    out_path = Path(out_dir)

    if as_of is None:
        as_of = dt.date.today()

    logger.info("=== Production pipeline: as_of=%s ===", as_of)

    # ── 1. Build panels ───────────────────────────────────────────────────
    logger.info("Step 1/9: Building panels...")
    store = Store(store_path)
    panels = build_panels(as_of=as_of, store=store)
    target_quarter = determine_target_quarter(as_of)
    horizon = determine_horizon(as_of, target_quarter)
    logger.info("  Target: %s  Horizon: %s", target_quarter, horizon)

    # ── 2. Model config ───────────────────────────────────────────────────
    config = load_model_config()
    dfm_cfg = config.get("dfm", {})
    bridge_cfg = config.get("bridges", {})

    # ── 3. Run DFM ────────────────────────────────────────────────────────
    logger.info("Step 2/9: Running DFM...")
    dfm = DFM(
        n_factors=dfm_cfg.get("n_factors", 2),
        factor_orders=dfm_cfg.get("factor_orders", 2),
        em_max_iter=dfm_cfg.get("em_max_iter", 250),
        variable_ids=dfm_cfg.get("variable_ids"),
    )
    dfm_result = dfm.predict(panels, target_quarter)
    gdp_yoy = dfm_result["gdp_yoy"]
    if np.isnan(gdp_yoy):
        logger.error("DFM returned NaN — aborting pipeline")
        sys.exit(1)
    logger.info("  DFM GDP y/y: %.2f%%", gdp_yoy)

    # ── 4. Bridge equations ───────────────────────────────────────────────
    logger.info("Step 3/9: Running bridge equations...")
    sectors = sector_contributions(
        panels, target_quarter, bridge_map=bridge_cfg,
    )

    # ── 5. News decomposition ─────────────────────────────────────────────
    logger.info("Step 4/9: Running news decomposition...")
    prev_state = load_state(state_path)
    decomposition = None
    if prev_state is not None:
        try:
            decomposition = decompose(
                panels_new=panels,
                target_quarter=target_quarter,
                state_dir=state_path,
                prev_state=prev_state,
                variable_ids=dfm_cfg.get("variable_ids"),
                n_factors=dfm_cfg.get("n_factors", 2),
                factor_orders=dfm_cfg.get("factor_orders", 2),
                em_max_iter=dfm_cfg.get("em_max_iter", 250),
            )
        except Exception as exc:
            logger.warning("Decomposition failed: %s", exc)
    else:
        logger.info("  First run — no decomposition available")

    # ── 6. Narratives ─────────────────────────────────────────────────────
    logger.info("Step 5/9: Rendering narrative notes...")
    narrative_en = render_note(
        decomposition, sectors, lang="en",
        run_date=str(as_of), target_quarter=str(target_quarter),
        horizon=horizon, new_nowcast=gdp_yoy,
    )
    narrative_uz = render_note(
        decomposition, sectors, lang="uz",
        run_date=str(as_of), target_quarter=str(target_quarter),
        horizon=horizon, new_nowcast=gdp_yoy,
    )

    # ── 7. Uncertainty bands ──────────────────────────────────────────────
    unc = config.get("uncertainty", {})
    percentiles = unc.get("percentiles", [10, 90])
    ssf = unc.get("small_sample_factor", 1.2)

    # Backtest scores (Models page) and per-prediction errors (bands).
    # Written by scripts/run_backtest.py into the state directory.
    bt_file = Path(backtest_path) if backtest_path else state_path / "backtest_summary.json"
    backtest: list = []
    past_nowcasts: list = []
    band_half = 1.2  # default ±1.2 pp, used only when no backtest is available
    if bt_file.exists():
        bt = json.loads(bt_file.read_text())
        backtest = bt.get("scores", [])
        # Headline model's past nowcasts next to GDP actuals (Nowcast Timeline chart)
        past_nowcasts = [r for r in bt.get("history", []) if r.get("model") == "dfm"]
        # Empirical band from backtest errors at this horizon
        errors = [r["error"] for r in bt.get("errors", [])
                  if r.get("horizon") == horizon and r.get("error") is not None]
        if len(errors) >= 5:
            lo_pct = percentiles[0] if len(percentiles) >= 2 else 10
            hi_pct = percentiles[1] if len(percentiles) >= 2 else 90
            band_half = max(
                abs(np.percentile(errors, lo_pct)),
                abs(np.percentile(errors, hi_pct)),
            ) * ssf
        else:
            logger.warning("Only %d backtest errors at %s; using default band", len(errors), horizon)
    else:
        logger.warning("No backtest file at %s; Models page empty and default band used", bt_file)

    band_lo = gdp_yoy - band_half
    band_hi = gdp_yoy + band_half

    # ── 8. Monitor + history ──────────────────────────────────────────────
    monitor = build_monitor_data(panels, as_of)
    nowcast_history = build_nowcast_history(state_path)
    nowcast_history.append({
        "run_date": str(as_of),
        "target_quarter": str(target_quarter),
        "gdp_yoy": gdp_yoy,
        "band_lo": band_lo,
        "band_hi": band_hi,
    })

    # ── 9. Assemble site context ──────────────────────────────────────────
    site_ctx = SiteContext(
        run_date=str(as_of),
        target_quarter=str(target_quarter),
        horizon=horizon,
        new_nowcast=gdp_yoy,
        old_nowcast=decomposition.old_nowcast if decomposition else None,
        total_change=decomposition.total_change if decomposition else None,
        band_lo=band_lo,
        band_hi=band_hi,
        decomposition=(
            _serialize_decomposition(decomposition) if decomposition else None
        ),
        sectors=[{
            "sector": s.sector, "label": s.label, "weight": s.weight,
            "growth_yoy": s.growth_yoy, "contribution_pp": s.contribution_pp,
            "method": s.method,
        } for s in sectors],
        nowcast_history=nowcast_history,
        monitor_data=monitor,
        backtest_results=backtest,
        narrative_en=narrative_en,
        narrative_uz=narrative_uz,
        nowcast_vs_actual=past_nowcasts,
        nowcast_paths=build_nowcast_paths(
            past_nowcasts, str(target_quarter),
            [{"run_date": h["run_date"], "gdp_yoy": h["gdp_yoy"], "horizon": horizon}
             for h in nowcast_history if h["target_quarter"] == str(target_quarter)],
        ),
        available_quarters=available_quarters or [str(target_quarter)],
    )

    # ── 10. Downloads ─────────────────────────────────────────────────────
    logger.info("Step 6/9: Generating downloads...")
    site_ctx.download_files = generate_downloads(
        site_ctx, decomposition, sectors, out_path,
    )

    # ── 11. Static site ───────────────────────────────────────────────────
    logger.info("Step 7/9: Building static site...")
    build_site(site_ctx, out_dir=str(out_path))

    # ── 12. PDF notes ─────────────────────────────────────────────────────
    logger.info("Step 8/9: Generating PDF notes...")
    note_ctx = NoteContext(
        run_date=str(as_of),
        target_quarter=str(target_quarter),
        horizon=horizon,
        new_nowcast=gdp_yoy,
        old_nowcast=decomposition.old_nowcast if decomposition else None,
        total_change=decomposition.total_change if decomposition else None,
        revision_effect=decomposition.revision_effect if decomposition else 0.0,
        news_effect=decomposition.news_effect if decomposition else 0.0,
        reestimation_effect=(
            decomposition.reestimation_effect if decomposition else 0.0
        ),
        top_drivers=[
            {"label": n.label, "impact": n.impact}
            for n in decomposition.top_news(5)
        ] if decomposition else [],
        sectors=[{
            "label": s.label,
            "growth_yoy": s.growth_yoy,
            "contribution_pp": s.contribution_pp,
        } for s in sectors],
        narrative=narrative_en,
        has_decomposition=decomposition is not None,
    )

    pdf_dir = out_path / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    generate_pdf_note(note_ctx, lang="en", out_path=pdf_dir / "note_en.pdf")
    generate_pdf_note(note_ctx, lang="uz", out_path=pdf_dir / "note_uz.pdf")

    # ── 13. Save run state ────────────────────────────────────────────────
    logger.info("Step 9/9: Saving run state...")
    nowcast_table = {str(target_quarter): gdp_yoy}
    save_state(
        state_dir=state_path,
        run_date=as_of,
        panels=panels,
        nowcast_table=nowcast_table,
        dfm_result=getattr(dfm, "_last_result", None),
        dfm_config={
            "n_factors": dfm.n_factors,
            "factor_orders": dfm.factor_orders,
            "n_monthly_vars": dfm_result.get("info", {}).get("n_monthly_vars", 0),
            "n_quarterly_vars": dfm_result.get("info", {}).get("n_quarterly_vars", 0),
            "em_max_iter": dfm.em_max_iter,
        },
    )

    logger.info("=== Pipeline complete ===")
    summary = {
        "run_date": str(as_of),
        "target_quarter": str(target_quarter),
        "horizon": horizon,
        "gdp_yoy": gdp_yoy,
        "band": [band_lo, band_hi],
        "has_decomposition": decomposition is not None,
        "n_sectors": len(sectors),
        "site_dir": str(out_path),
    }

    # Write summary for GitHub Actions output
    summary_path = out_path / "run_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


# ── multi-quarter driver ───────────────────────────────────────────────────


def default_quarters(today: dt.date) -> list[pd.Period]:
    """The quarter being nowcast. Earlier quarters appear in the Nowcast Timeline chart, so they
    do not need a page of their own. Add more quarters to this list to publish them with a selector."""
    return [pd.Period(today, freq="Q")]


def as_of_for_quarter(quarter: pd.Period, today: dt.date) -> dt.date:
    """Run date for a quarter's nowcast: today, or the quarter's last day if it has already ended."""
    return min(today, quarter.end_time.date())


def run_all(
    store_dir: str,
    state_dir: str,
    out_dir: str,
    quarters: list[pd.Period],
    today: dt.date,
    backtest_path: str | Path | None = None,
) -> dict[str, dict]:
    """Run the pipeline once per quarter, each into its own folder, and write a root index."""
    out_root = Path(out_dir)
    state_root = Path(state_dir)
    names = [str(q) for q in quarters]
    results: dict[str, dict] = {}
    for q in quarters:
        as_of = as_of_for_quarter(q, today)
        logger.info("=== Quarter %s, as of %s ===", q, as_of)
        results[str(q)] = run_pipeline(
            store_dir=store_dir,
            state_dir=str(state_root / str(q)),
            out_dir=str(out_root / str(q)),
            as_of=as_of,
            backtest_path=backtest_path or state_root / "backtest_summary.json",
            available_quarters=names,
        )

    # Root page: open the latest quarter
    latest = names[-1]
    (out_root / "index.html").write_text(
        '<!DOCTYPE html>\n<html><head>\n'
        f'<meta http-equiv="refresh" content="0;url={latest}/en/headline.html">\n'
        '</head><body></body></html>\n',
        encoding="utf-8",
    )
    return results


# ── CLI entry point ────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Production nowcasting pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/produce.py --store store --state-dir store/state --out _site\n"
            "  python scripts/produce.py --store store --state-dir state --out _site --as-of 2026-10-22 -v\n"
            "\nBuilds the current quarter in _site/<quarter>/ (see default_quarters).\n"
        ),
    )
    parser.add_argument("--store", default="store",
                        help="Vintage store directory (default: store)")
    parser.add_argument("--state-dir", default="state",
                        help="Run state directory; each quarter uses a subfolder (default: state)")
    parser.add_argument("--out", default="_site",
                        help="Output directory for the site (default: _site)")
    parser.add_argument("--as-of",
                        help="Pretend today is this date YYYY-MM-DD (default: today)")
    parser.add_argument("--backtest",
                        help="Backtest file (default: <state-dir>/backtest_summary.json)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="DEBUG-level logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    today = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    results = run_all(
        store_dir=args.store,
        state_dir=args.state_dir,
        out_dir=args.out,
        quarters=default_quarters(today),
        today=today,
        backtest_path=args.backtest,
    )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
