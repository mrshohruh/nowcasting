"""Benchmark models: AR(1) and carry-forward.

AR(1)
    Estimate AR(1) on discrete-quarter y/y GDP growth up to the last released quarter,
    then iterate forward to produce a nowcast for the target quarter.

Carry-forward
    Use the last published GDP y/y growth for the target quarter.
    This is the "no-change" benchmark: the naivest possible forecast.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from nowcast.transform.panels import Panels


def _quarter_of(as_of: dt.date) -> pd.Period:
    """The quarter being nowcast: the current quarter of as_of."""
    return pd.Period(as_of, freq="Q")


def _prev_quarter(q: pd.Period) -> pd.Period:
    return q - 1


def _gdp_yoy_series(panels: Panels) -> pd.Series:
    """Extract GDP y/y series from the panel, sorted by period."""
    yoy = panels.yoy
    gdp = yoy[(yoy["variable"] == "gdp") & (yoy["freq"] == "Q")].copy()
    if gdp.empty:
        return pd.Series(dtype=float)
    gdp["period_idx"] = pd.PeriodIndex(gdp["period"], freq="Q")
    gdp = gdp.sort_values("period_idx").set_index("period_idx")
    return gdp["value"]


class AR1:
    """AR(1) benchmark on quarterly GDP y/y growth."""

    name = "ar1"

    def predict(self, panels: Panels, target_quarter: pd.Period) -> dict:
        """Estimate AR(1) on available GDP y/y and forecast target_quarter.

        Returns dict with gdp_yoy (point forecast) and info.
        """
        gdp = _gdp_yoy_series(panels)
        if len(gdp) < 4:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "too few GDP observations"}}

        # Only use observations up to the last quarter that should be released by as_of
        # (the panel already handles this via as_of filtering)
        y = gdp.values
        n = len(y)

        # OLS: y_t = c + phi * y_{t-1} + e
        Y = y[1:]
        X = np.column_stack([np.ones(n - 1), y[:-1]])
        try:
            beta = np.linalg.lstsq(X, Y, rcond=None)[0]
        except np.linalg.LinAlgError:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "lstsq failed"}}

        c, phi = beta[0], beta[1]

        # Iterate forward from last observation to target_quarter
        last_period = gdp.index[-1]
        last_val = gdp.iloc[-1]
        steps = int((target_quarter - last_period).n)

        forecast = last_val
        for _ in range(steps):
            forecast = c + phi * forecast

        resid = Y - X @ beta
        rmse_in = float(np.sqrt(np.mean(resid ** 2)))

        return {
            "gdp_yoy": float(forecast),
            "model": self.name,
            "info": {
                "c": float(c),
                "phi": float(phi),
                "n_obs": n,
                "last_obs_period": str(last_period),
                "last_obs_value": float(last_val),
                "steps_ahead": steps,
                "rmse_in_sample": rmse_in,
            },
        }


class CarryForward:
    """Carry-forward benchmark: use the last published GDP y/y growth."""

    name = "carry_forward"

    def predict(self, panels: Panels, target_quarter: pd.Period) -> dict:
        gdp = _gdp_yoy_series(panels)
        if gdp.empty:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "no GDP data"}}

        last_val = float(gdp.iloc[-1])
        last_period = gdp.index[-1]

        return {
            "gdp_yoy": last_val,
            "model": self.name,
            "info": {
                "last_obs_period": str(last_period),
                "last_obs_value": last_val,
            },
        }
