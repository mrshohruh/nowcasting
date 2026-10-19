"""Replay engine: run all models on 57 monthly as-of dates and collect results.

Each run date simulates a nowcaster working on the 1st of each month from
Jan 2022 to Sep 2026. At each date, the panel is built with pseudo-real-time
data and all models produce a GDP y/y nowcast for the current quarter.

Horizon classification:
    Month 1 of quarter → "nowcast_m1" for current Q, "forecast" for next Q
    Month 2 → "nowcast_m2"
    Month 3 → "nowcast_m3"
    After quarter ends, before GDP release → "backcast"
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Result of one model on one run date for one target quarter."""
    run_date: dt.date
    target_quarter: str
    horizon: str
    model: str
    gdp_yoy: float
    info: dict = field(default_factory=dict)
    elapsed_sec: float = 0.0


def _classify_horizon(run_date: dt.date, target_quarter: pd.Period) -> str:
    """Classify the forecast horizon based on run_date and target quarter."""
    # What quarter does run_date fall in?
    run_quarter = pd.Period(run_date, freq="Q")

    if target_quarter > run_quarter:
        return "forecast"

    if target_quarter < run_quarter:
        return "backcast"

    # Same quarter: which month?
    month_in_quarter = (run_date.month - 1) % 3 + 1  # 1, 2, or 3
    return f"nowcast_m{month_in_quarter}"


def _target_quarters(run_date: dt.date) -> list[pd.Period]:
    """Determine which quarter(s) to nowcast for a given run date.

    Returns: current quarter (always), and if month 1, also next quarter.
    """
    current_q = pd.Period(run_date, freq="Q")
    month_in_q = (run_date.month - 1) % 3 + 1

    targets = [current_q]
    if month_in_q == 1:
        # Also forecast next quarter
        targets.append(current_q + 1)

    return targets


def replay(
    store,
    models: list,
    start: str = "2022-01-01",
    end: str = "2026-09-01",
    freq: str = "MS",
    build_panels_fn=None,
    build_meta_fn=None,
    progress_callback=None,
) -> pd.DataFrame:
    """Run all models on all replay dates.

    Parameters
    ----------
    store : Store
        Vintage store for building panels.
    models : list
        List of model instances with .predict(panels, target_quarter) -> dict.
    start, end : str
        Date range for replay run dates.
    freq : str
        Frequency for run dates (default "MS" = 1st of month).
    build_panels_fn : callable, optional
        Function(as_of_date, store) -> Panels. Defaults to build_panels.
    build_meta_fn : callable, optional
        Function(sa_panel) -> meta DataFrame. Used when panels don't have meta.
    progress_callback : callable, optional
        Called with (i, n_total, run_date_str) for progress reporting.

    Returns
    -------
    DataFrame with columns: run_date, target_quarter, horizon, model, gdp_yoy, elapsed_sec
    """
    from nowcast.transform.panels import build_panels as _build_panels

    if build_panels_fn is None:
        build_panels_fn = _build_panels

    run_dates = pd.date_range(start, end, freq=freq)
    results: list[dict] = []
    n_total = len(run_dates)

    for i, rd_ts in enumerate(run_dates):
        rd = rd_ts.date()
        if progress_callback:
            progress_callback(i, n_total, str(rd))

        # Build panels for this as-of date
        try:
            panels = build_panels_fn(rd, store)
        except Exception as e:
            logger.warning("Panel build failed for %s: %s", rd, e)
            continue

        # Ensure meta has required columns
        if build_meta_fn is not None and not hasattr(panels.meta, 'sa_n'):
            panels.meta = build_meta_fn(panels.sa)

        target_quarters = _target_quarters(rd)

        for tq in target_quarters:
            horizon = _classify_horizon(rd, tq)

            for model in models:
                t0 = time.monotonic()
                try:
                    pred = model.predict(panels, tq)
                except Exception as e:
                    logger.warning("%s failed on %s→%s: %s",
                                   model.name, rd, tq, e)
                    pred = {"gdp_yoy": np.nan, "model": model.name,
                            "info": {"error": str(e)}}
                elapsed = time.monotonic() - t0

                results.append({
                    "run_date": rd,
                    "target_quarter": str(tq),
                    "horizon": horizon,
                    "model": pred.get("model", model.name),
                    "gdp_yoy": pred.get("gdp_yoy", np.nan),
                    "elapsed_sec": elapsed,
                })

    df = pd.DataFrame(results)
    return df


def score(
    results: pd.DataFrame,
    actuals: pd.Series,
) -> pd.DataFrame:
    """Score model predictions against actual GDP y/y.

    Parameters
    ----------
    results : DataFrame
        Output of replay().
    actuals : Series
        GDP y/y indexed by quarter string (e.g. "2022Q1": 5.7).

    Returns
    -------
    DataFrame with RMSE and MAE by model and horizon.
    """
    df = results.copy()
    df["actual"] = df["target_quarter"].map(actuals)
    df["error"] = df["gdp_yoy"] - df["actual"]
    df = df.dropna(subset=["error", "gdp_yoy"])

    scores = []
    for (model, horizon), g in df.groupby(["model", "horizon"]):
        e = g["error"].values
        scores.append({
            "model": model,
            "horizon": horizon,
            "n": len(e),
            "rmse": float(np.sqrt(np.mean(e ** 2))),
            "mae": float(np.mean(np.abs(e))),
            "mean_error": float(np.mean(e)),
        })

    return pd.DataFrame(scores)


def combine_forecasts(
    results: pd.DataFrame,
    method: str = "equal",
    training_results: pd.DataFrame | None = None,
    actuals: pd.Series | None = None,
    fixed_weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Combine model forecasts into ensemble predictions.

    Parameters
    ----------
    method : str
        "equal" for equal weights, "inverse_mse" for inverse-MSE weights.
    training_results : DataFrame
        Past results for computing inverse-MSE weights.
    actuals : Series
        Actual GDP y/y for computing MSE weights.
    """
    combined = []

    for (rd, tq, hz), g in results.groupby(["run_date", "target_quarter", "horizon"]):
        preds = g.set_index("model")["gdp_yoy"].dropna()
        if preds.empty:
            continue

        if method == "fixed_weights" and fixed_weights is not None:
            total_w = sum(fixed_weights.get(m, 0) for m in preds.index)
            if total_w > 0:
                combo = sum(preds[m] * fixed_weights.get(m, 0) / total_w
                            for m in preds.index)
            else:
                combo = float(preds.mean())
        elif method == "equal":
            combo = float(preds.mean())
        elif method == "inverse_mse" and training_results is not None and actuals is not None:
            # Compute inverse-MSE weights from past performance
            weights = {}
            for m in preds.index:
                past = training_results[
                    (training_results["model"] == m) &
                    (training_results["run_date"] < rd)
                ]
                past = past.dropna(subset=["gdp_yoy"])
                past_actual = past["target_quarter"].map(actuals)
                errors = past["gdp_yoy"] - past_actual
                errors = errors.dropna()
                if len(errors) > 0:
                    mse = float(np.mean(errors ** 2))
                    weights[m] = 1.0 / max(mse, 0.01)
                else:
                    weights[m] = 1.0

            total_w = sum(weights.get(m, 1.0) for m in preds.index)
            combo = sum(
                preds[m] * weights.get(m, 1.0) / total_w
                for m in preds.index
            )
        else:
            combo = float(preds.mean())

        combined.append({
            "run_date": rd,
            "target_quarter": tq,
            "horizon": hz,
            "model": f"combo_{method}",
            "gdp_yoy": combo,
        })

    return pd.DataFrame(combined)
