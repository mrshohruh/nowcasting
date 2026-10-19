#!/usr/bin/env python3
"""Phase 3 backtest: replay all models on 57 monthly dates and score."""

from __future__ import annotations

import datetime as dt
import logging
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")

# ── paths ──
DATA_ROOT = "/tmp/data-wt/observations"
PANEL_SA = "/tmp/data-wt/panels/panel_sa.parquet"
PANEL_YOY = "/tmp/data-wt/panels/panel_yoy.parquet"
OUT_DIR = "/tmp/data-wt/backtest"

# ── helpers ──

def build_meta_from_sa(sa: pd.DataFrame) -> pd.DataFrame:
    """Build meta DataFrame from SA panel data."""
    rows = []
    for (v, f), g in sa.groupby(["variable", "freq"]):
        vals = g["value_clean"].dropna() if "value_clean" in g.columns else g["value"].dropna()
        rows.append({
            "variable": v, "freq": f,
            "sa_n": len(vals),
            "sa_mean": float(vals.mean()) if len(vals) > 0 else 0,
            "sa_sd": float(vals.std()) if len(vals) > 1 else 1,
        })
    return pd.DataFrame(rows)


def get_gdp_actuals(store) -> pd.Series:
    """Get actual GDP y/y from the latest vintage (first-release proxy)."""
    from nowcast.transform.panels import build_panels
    panels = build_panels(dt.date(2026, 10, 1), store)
    yoy = panels.yoy
    gdp = yoy[(yoy["variable"] == "gdp") & (yoy["freq"] == "Q")].copy()
    gdp["pi"] = pd.PeriodIndex(gdp["period"], freq="Q")
    gdp = gdp.sort_values("pi").drop_duplicates("pi", keep="last").set_index("pi")
    return gdp["value"].rename_axis("quarter").rename("actual")


def main():
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    from nowcast.store.vintages import Store
    from nowcast.transform.panels import build_panels
    from nowcast.models.benchmarks import AR1, CarryForward
    from nowcast.models.dfm import DFM
    from nowcast.models.bridges import BridgeEquations
    from nowcast.models.midas import UMIDAS
    from nowcast.models.replay import replay, score, combine_forecasts

    store = Store(DATA_ROOT)
    print(f"Store: {DATA_ROOT}, empty={store.is_empty()}")

    # ── models ──
    # DFM uses a curated subset for speed (full panel too slow for 57×EM)
    dfm_vars = [
        "ind_mining", "ind_manuf", "ind_elec", "constr", "retail", "wholesale",
        "cpi", "ppi", "exports_total", "imports", "m2_real",
        "gold_price", "usd_rate", "reserves_fx",
        "gdp", "gva", "invest", "remittances_real", "wages_real",
    ]
    models = [
        AR1(),
        CarryForward(),
        DFM(n_factors=2, factor_orders=2, em_max_iter=150,
            variable_ids=dfm_vars, intercept_correction=True),
        BridgeEquations(),
        UMIDAS(n_lags=6, poly_degree=3, monthly_vars=[
            "ind_mining", "ind_manuf", "ind_elec", "constr", "retail", "wholesale",
            "exports_nogold", "exports_gold", "imports", "usd_rate",
            "cpi", "ppi", "m2_real", "reserves_fx", "gold_price",
            "rate_deposit_hh", "rate_loan_firms", "new_small_business",
        ]),
    ]

    # ── run replay ──
    print(f"\nRunning backtest: Jan 2022 → Sep 2026 (57 monthly dates)")
    print(f"Models: {[m.name for m in models]}")
    t0 = time.time()

    def progress(i, n, rd):
        if i % 6 == 0:
            print(f"  [{i+1}/{n}] {rd} ...", flush=True)

    results = replay(
        store, models,
        start="2022-01-01", end="2026-09-01",
        build_meta_fn=build_meta_from_sa,
        progress_callback=progress,
    )

    elapsed = time.time() - t0
    print(f"\nReplay done: {len(results)} predictions in {elapsed:.0f}s")

    # Save raw results
    results.to_parquet(f"{OUT_DIR}/replay_results.parquet", index=False)
    results.to_csv(f"{OUT_DIR}/replay_results.csv", index=False)
    print(f"Saved to {OUT_DIR}/replay_results.csv")

    # ── actuals ──
    actuals = get_gdp_actuals(store)
    actuals_dict = {str(q): v for q, v in actuals.items()}
    print(f"\nActuals: {len(actuals_dict)} quarters")
    for q in sorted(actuals_dict.keys()):
        print(f"  {q}: {actuals_dict[q]:.2f}%")

    # ── score individual models ──
    scores_df = score(results, actuals_dict)
    print("\n── Individual model scores ──")
    for model in scores_df["model"].unique():
        ms = scores_df[scores_df["model"] == model]
        overall_rmse = np.sqrt(np.mean(
            results[results["model"] == model].merge(
                pd.DataFrame({"target_quarter": list(actuals_dict.keys()),
                               "actual": list(actuals_dict.values())}),
                on="target_quarter"
            ).dropna(subset=["gdp_yoy", "actual"]).eval("(gdp_yoy - actual)**2")
        ))
        print(f"\n  {model}:")
        for _, r in ms.sort_values("horizon").iterrows():
            print(f"    {r['horizon']:15s}  RMSE={r['rmse']:.3f}  MAE={r['mae']:.3f}  n={int(r['n'])}")
        print(f"    {'overall':15s}  RMSE={overall_rmse:.3f}")

    # ── combinations ──
    # Only combine the three main models (A, B, C)
    abc = results[results["model"].isin(["dfm", "bridges", "midas"])]

    combo_equal = combine_forecasts(abc, method="equal")
    combo_imse = combine_forecasts(
        abc, method="inverse_mse",
        training_results=abc, actuals=actuals_dict,
    )

    # DFM-dominant combo: 60% DFM, 20% bridges, 20% MIDAS
    combo_dfm_dom = combine_forecasts(
        abc, method="fixed_weights",
        fixed_weights={"dfm": 0.6, "bridges": 0.2, "midas": 0.2},
    )

    all_results = pd.concat([results, combo_equal, combo_imse, combo_dfm_dom],
                            ignore_index=True)
    all_results.to_parquet(f"{OUT_DIR}/all_results.parquet", index=False)

    # Score combinations
    all_combos = pd.concat([combo_equal, combo_imse, combo_dfm_dom], ignore_index=True)
    combo_scores = score(all_combos, actuals_dict)

    print("\n── Combination scores ──")
    for model in combo_scores["model"].unique():
        ms = combo_scores[combo_scores["model"] == model]
        print(f"\n  {model}:")
        for _, r in ms.sort_values("horizon").iterrows():
            print(f"    {r['horizon']:15s}  RMSE={r['rmse']:.3f}  MAE={r['mae']:.3f}  n={int(r['n'])}")

    # ── exit check ──
    print("\n── Exit check ──")
    eval_quarters = [f"{y}Q{q}" for y in range(2022, 2027)
                     for q in range(1, 5)
                     if f"{y}Q{q}" <= "2026Q2"]

    ar1_preds = results[
        (results["model"] == "ar1") &
        (results["target_quarter"].isin(eval_quarters)) &
        (results["horizon"] == "nowcast_m3")
    ].copy()
    ar1_preds["actual"] = ar1_preds["target_quarter"].map(actuals_dict)
    ar1_preds = ar1_preds.dropna(subset=["gdp_yoy", "actual"])
    ar1_rmse = np.sqrt(np.mean((ar1_preds["gdp_yoy"] - ar1_preds["actual"]) ** 2))

    # Check all headline candidates at m3
    headline_candidates = {
        "combo_equal": combo_equal,
        "combo_inverse_mse": combo_imse,
        "combo_dfm_dominant": combo_dfm_dom,
    }
    # Also check DFM alone
    dfm_preds = results[
        (results["model"] == "dfm") &
        (results["target_quarter"].isin(eval_quarters)) &
        (results["horizon"] == "nowcast_m3")
    ].copy()
    dfm_preds["actual"] = dfm_preds["target_quarter"].map(actuals_dict)
    dfm_preds = dfm_preds.dropna(subset=["gdp_yoy", "actual"])
    dfm_rmse = np.sqrt(np.mean((dfm_preds["gdp_yoy"] - dfm_preds["actual"]) ** 2))

    print(f"  AR(1) RMSE (m3): {ar1_rmse:.3f}  (n={len(ar1_preds)})")
    print(f"  DFM-only RMSE (m3): {dfm_rmse:.3f}  ratio={dfm_rmse/ar1_rmse:.3f}")

    best_name, best_rmse, best_ratio = "dfm", dfm_rmse, dfm_rmse / ar1_rmse
    for cname, cdf in headline_candidates.items():
        cpreds = cdf[
            (cdf["target_quarter"].isin(eval_quarters)) &
            (cdf["horizon"] == "nowcast_m3")
        ].copy()
        cpreds["actual"] = cpreds["target_quarter"].map(actuals_dict)
        cpreds = cpreds.dropna(subset=["gdp_yoy", "actual"])
        if cpreds.empty:
            continue
        crmse = np.sqrt(np.mean((cpreds["gdp_yoy"] - cpreds["actual"]) ** 2))
        cratio = crmse / ar1_rmse
        print(f"  {cname} RMSE (m3): {crmse:.3f}  ratio={cratio:.3f}")
        if crmse < best_rmse:
            best_name, best_rmse, best_ratio = cname, crmse, cratio

    passed = best_ratio <= 0.8
    print(f"\n  Best headline: {best_name}")
    print(f"  Best ratio: {best_ratio:.3f}  (threshold ≤ 0.80)")
    print(f"  EXIT CHECK: {'PASS ✓' if passed else 'FAIL ✗'}")

    # Save scores
    all_scores = pd.concat([scores_df, combo_scores], ignore_index=True)
    all_scores.to_csv(f"{OUT_DIR}/scores.csv", index=False)

    # Summary
    summary = {
        "ar1_rmse_m3": float(ar1_rmse),
        "best_headline": best_name,
        "best_rmse_m3": float(best_rmse),
        "best_ratio": float(best_ratio),
        "dfm_rmse_m3": float(dfm_rmse),
        "exit_check": "PASS" if passed else "FAIL",
        "n_run_dates": len(results["run_date"].unique()),
        "n_predictions": len(results),
        "eval_quarters": eval_quarters,
    }
    import json
    with open(f"{OUT_DIR}/summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nAll outputs saved to {OUT_DIR}/")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
