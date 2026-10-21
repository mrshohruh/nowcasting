"""Model B: Sector bridge equations on the y/y panel.

Each GVA sector is bridged to monthly indicators via simple OLS:
    gva_sector_yoy_q = c + β₁·indicator₁_yoy_q + β₂·indicator₂_yoy_q + ε

Steps:
1. For each GVA sector, take the bridge mapping from config (monthly indicators).
2. Aggregate monthly y/y indicators to quarterly by averaging the available months
   (handles ragged edge: if only 1 or 2 months are available, use those).
3. Estimate the bridge equation by OLS on the common sample.
4. Predict the target quarter GVA using the (partially) available indicator data.
5. Aggregate sector GVA predictions to GDP y/y using the most recent GVA weights.

Sectors mapped to AR(1) (no monthly indicator): agriculture, net_taxes.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from nowcast.transform.panels import Panels

logger = logging.getLogger(__name__)

# Default bridge mappings — overridden by config
DEFAULT_BRIDGES: dict[str, dict[str, Any]] = {
    "gva_agriculture": {"indicators": [], "method": "AR"},
    "gva_mining": {"indicators": ["ind_mining"]},
    "gva_manufacturing": {"indicators": ["ind_manuf"]},
    "gva_electricity": {"indicators": ["ind_elec"]},
    "gva_construction": {"indicators": ["constr"]},
    "gva_trade_accommodation": {
        "indicators": ["retail", "wholesale", "mkt_trade", "mkt_accommodation"]
    },
    "gva_transport": {"indicators": ["mkt_transport"]},
    "gva_ict": {"indicators": ["mkt_ict"]},
    "gva_other_services": {"indicators": ["mkt_finance"]},
    "net_taxes": {"indicators": [], "method": "AR"},
}

# Approximate GVA weights (share of GDP, from recent NAG data)
# These are used to aggregate sector predictions to GDP.
# Updated from 2024 data: agriculture ~22%, industry ~26%, construction ~6%,
# trade+accommodation ~10%, transport ~5%, ICT ~4%, other services ~14%, net_taxes ~13%
DEFAULT_GVA_WEIGHTS: dict[str, float] = {
    "gva_agriculture": 0.22,
    "gva_mining": 0.08,
    "gva_manufacturing": 0.13,
    "gva_electricity": 0.02,
    "gva_water": 0.01,
    "gva_construction": 0.06,
    "gva_trade_accommodation": 0.10,
    "gva_transport": 0.05,
    "gva_ict": 0.04,
    "gva_other_services": 0.14,
    "net_taxes": 0.13,
}


def _extract_yoy_quarterly(
    yoy: pd.DataFrame, variable: str
) -> pd.Series:
    """Get quarterly y/y series for a variable, indexed by Period."""
    d = yoy[(yoy["variable"] == variable) & (yoy["freq"] == "Q")].copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["pi"] = pd.PeriodIndex(d["period"], freq="Q")
    d = d.sort_values("pi").drop_duplicates("pi", keep="last").set_index("pi")
    return d["value"]


def _monthly_to_quarterly_avg(
    yoy: pd.DataFrame, variable: str, target_quarter: pd.Period
) -> pd.Series:
    """Average monthly y/y values within each quarter.

    For the target quarter, uses whatever months are available (ragged edge).
    Returns quarterly series indexed by Period.
    """
    d = yoy[(yoy["variable"] == variable) & (yoy["freq"] == "M")].copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["pi"] = pd.PeriodIndex(d["period"], freq="M")
    d = d.sort_values("pi").drop_duplicates("pi", keep="last").set_index("pi")

    # Group by quarter and average
    d["quarter"] = d.index.asfreq("Q")
    q_avg = d.groupby("quarter")["value"].mean()
    q_avg.index = pd.PeriodIndex(q_avg.index, freq="Q")
    return q_avg


def _ar1_forecast(series: pd.Series, target: pd.Period) -> float:
    """AR(1) forecast for a quarterly y/y series."""
    y = series.dropna().values
    if len(y) < 4:
        return float(y[-1]) if len(y) > 0 else np.nan

    Y = y[1:]
    X = np.column_stack([np.ones(len(Y)), y[:-1]])
    try:
        beta = np.linalg.lstsq(X, Y, rcond=None)[0]
    except np.linalg.LinAlgError:
        return float(y[-1])

    c, phi = beta
    last_period = series.dropna().index[-1]
    last_val = series.dropna().iloc[-1]
    steps = max(1, int((target - last_period).n))

    forecast = last_val
    for _ in range(steps):
        forecast = c + phi * forecast
    return float(forecast)


def _bridge_ols(
    dep: pd.Series, indep: pd.DataFrame, target_quarter: pd.Period,
    target_indep: pd.Series,
    ridge_lambda: float = 1.0,
) -> tuple[float, dict]:
    """Ridge-regularised bridge equation: dep ~ c + indep.

    Uses ridge regression (L2) to stabilise estimates on short samples.
    Returns (prediction, info_dict).
    """
    # Align on common quarters
    common = dep.dropna().index.intersection(indep.dropna(how="all").index)
    if len(common) < 6:
        # Too few observations — fallback to AR
        return _ar1_forecast(dep, target_quarter), {
            "method": "AR_fallback", "reason": "< 6 common obs"
        }

    y = dep.loc[common].values
    X_raw = indep.loc[common].values

    # Drop columns with all NaN
    good_cols = ~np.isnan(X_raw).all(axis=0)
    X_raw = X_raw[:, good_cols]
    col_names = indep.columns[good_cols].tolist()

    if X_raw.shape[1] == 0:
        return _ar1_forecast(dep, target_quarter), {
            "method": "AR_fallback", "reason": "no valid indicator columns"
        }

    # Fill remaining NaN with column means (rare interior gaps)
    col_means = np.nanmean(X_raw, axis=0)
    for j in range(X_raw.shape[1]):
        mask = np.isnan(X_raw[:, j])
        if mask.any():
            X_raw[mask, j] = col_means[j]

    X = np.column_stack([np.ones(len(y)), X_raw])

    # Ridge regression: (X'X + λI)^{-1} X'y (don't penalise intercept)
    penalty = ridge_lambda * np.eye(X.shape[1])
    penalty[0, 0] = 0  # no penalty on intercept
    try:
        beta = np.linalg.solve(X.T @ X + penalty, X.T @ y)
    except np.linalg.LinAlgError:
        return _ar1_forecast(dep, target_quarter), {
            "method": "AR_fallback", "reason": "ridge solve failed"
        }

    # Predict target quarter
    x_target = target_indep[indep.columns[good_cols]].values
    # Fill NaN in target with last known value
    for j in range(len(x_target)):
        if np.isnan(x_target[j]):
            col = indep.columns[good_cols][j]
            last_known = indep[col].dropna()
            x_target[j] = last_known.iloc[-1] if not last_known.empty else col_means[j]

    x_row = np.concatenate([[1.0], x_target])
    prediction = float(x_row @ beta)

    # Winsorise extreme predictions: clip to ±3σ of dependent variable
    y_mean, y_std = float(np.mean(y)), float(np.std(y))
    lo, hi = y_mean - 3 * max(y_std, 1.0), y_mean + 3 * max(y_std, 1.0)
    prediction = float(np.clip(prediction, lo, hi))

    resid = y - X @ beta
    rmse = float(np.sqrt(np.mean(resid ** 2)))

    return prediction, {
        "method": "ridge",
        "n_obs": len(y),
        "r2": float(1 - np.var(resid) / np.var(y)) if np.var(y) > 0 else 0,
        "rmse_in": rmse,
        "coefficients": dict(zip(["const"] + col_names, beta.tolist(), strict=False)),
    }


class BridgeEquations:
    """Sector bridge equation nowcasting model."""

    name = "bridges"

    def __init__(
        self,
        bridge_map: dict[str, dict] | None = None,
        gva_weights: dict[str, float] | None = None,
        ar_shrinkage: float = 0.3,
    ):
        self.bridge_map = bridge_map or DEFAULT_BRIDGES
        self.gva_weights = gva_weights or DEFAULT_GVA_WEIGHTS
        self.ar_shrinkage = ar_shrinkage  # blend weight toward AR(1)

    def predict(self, panels: Panels, target_quarter: pd.Period) -> dict:
        """Predict GDP y/y by bridging GVA sectors to monthly indicators."""
        yoy = panels.yoy
        sector_results: dict[str, dict] = {}

        for sector, spec in self.bridge_map.items():
            indicators = spec.get("indicators", [])
            method = spec.get("method", "OLS")

            # Get the sector's quarterly y/y series
            dep = _extract_yoy_quarterly(yoy, sector)

            if method == "AR" or not indicators:
                # AR(1) forecast
                pred = _ar1_forecast(dep, target_quarter)
                sector_results[sector] = {
                    "prediction": pred,
                    "info": {"method": "AR", "n_obs": len(dep.dropna())},
                }
                continue

            # Build quarterly indicator matrix by averaging monthly y/y
            indep_dict = {}
            for ind in indicators:
                q_series = _monthly_to_quarterly_avg(yoy, ind, target_quarter)
                if not q_series.empty:
                    indep_dict[ind] = q_series

            if not indep_dict:
                # No indicator data — fallback to AR
                pred = _ar1_forecast(dep, target_quarter)
                sector_results[sector] = {
                    "prediction": pred,
                    "info": {"method": "AR_fallback", "reason": "no indicator data"},
                }
                continue

            indep = pd.DataFrame(indep_dict)

            # Build target quarter indicator values
            target_indep = pd.Series(dtype=float)
            for ind in indep.columns:
                if target_quarter in indep.index:
                    target_indep[ind] = indep.loc[target_quarter, ind]
                else:
                    # Use last available value
                    last = indep[ind].dropna()
                    target_indep[ind] = last.iloc[-1] if not last.empty else np.nan

            bridge_pred, info = _bridge_ols(dep, indep, target_quarter, target_indep)

            # Shrink toward AR(1): pred = (1-λ)·bridge + λ·AR
            # Higher shrinkage for lower R² (less reliable bridges)
            ar_pred = _ar1_forecast(dep, target_quarter)
            r2 = info.get("r2", 0)
            # Adaptive shrinkage: more shrinkage when R² is low
            shrink = self.ar_shrinkage * (1 - max(r2, 0))
            pred = (1 - shrink) * bridge_pred + shrink * ar_pred
            info["ar_pred"] = ar_pred
            info["shrinkage"] = shrink

            sector_results[sector] = {"prediction": pred, "info": info}

        # Aggregate to GDP using GVA weights
        gdp_yoy = self._aggregate_to_gdp(sector_results, yoy, target_quarter)

        return {
            "gdp_yoy": gdp_yoy,
            "model": self.name,
            "info": {
                "target": str(target_quarter),
                "sectors": {
                    s: {"prediction": r["prediction"], **r["info"]}
                    for s, r in sector_results.items()
                },
            },
        }

    def _aggregate_to_gdp(
        self,
        sector_results: dict[str, dict],
        yoy: pd.DataFrame,
        target_quarter: pd.Period,
    ) -> float:
        """Aggregate sector GVA predictions to GDP y/y.

        Uses the most recent actual GVA shares to compute weights, falling
        back to DEFAULT_GVA_WEIGHTS if actuals are unavailable.
        """
        # Try to compute weights from actual GDP/GVA data
        weights = self._compute_weights(yoy, target_quarter)
        if weights is None:
            weights = self.gva_weights

        total_weight = 0.0
        weighted_sum = 0.0

        for sector, result in sector_results.items():
            pred = result["prediction"]
            if np.isnan(pred):
                continue
            w = weights.get(sector, 0.0)
            weighted_sum += w * pred
            total_weight += w

        # Add water sector (not bridged) — use AR or last value
        if "gva_water" not in sector_results:
            water = _extract_yoy_quarterly(yoy, "gva_water")
            if not water.empty:
                water_pred = _ar1_forecast(water, target_quarter)
                w = weights.get("gva_water", 0.01)
                weighted_sum += w * water_pred
                total_weight += w

        if total_weight > 0:
            return float(weighted_sum / total_weight)
        return np.nan

    @staticmethod
    def _compute_weights(
        yoy: pd.DataFrame, target_quarter: pd.Period
    ) -> dict[str, float] | None:
        """Compute GDP composition weights from the latest available data."""
        # Get GDP and all GVA components for the latest available quarter
        gdp = _extract_yoy_quarterly(yoy, "gdp")
        if gdp.empty:
            return None

        # We need level-based weights, but we only have y/y growth.
        # Use the growth contributions as an approximation:
        # weight_i ≈ (gva_i_yoy / gdp_yoy) × (known_share_i)
        # For simplicity, return None and use default weights
        return None
