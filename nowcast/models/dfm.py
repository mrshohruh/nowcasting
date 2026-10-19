"""Model A: DynamicFactorMQ from statsmodels on the SA panel.

The factor model estimates latent common factors from the mixed-frequency SA panel
(monthly + quarterly, dlog×100) and extracts a GDP nowcast by:

1. Build monthly endog (z-scored SA dlog) and quarterly endog (z-scored SA dlog) DataFrames.
2. Fit DynamicFactorMQ with the EM algorithm.
3. Get predicted values — the Kalman smoother fills the ragged edge and quarterly gaps.
4. Convert the GDP prediction (z-scored SA dlog) back to y/y growth using GDP SA levels.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from nowcast.transform.panels import Panels

logger = logging.getLogger(__name__)


def _build_endog(
    panels: Panels,
    variable_ids: list[str] | None,
    target_quarter: pd.Period,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build monthly and quarterly DataFrames for DynamicFactorMQ.

    Returns (monthly_df, quarterly_df, meta_dict).
    monthly_df: DatetimeIndex (month-end), columns = monthly variable names, values = z
    quarterly_df: DatetimeIndex (quarter-end), columns = quarterly variable names, values = z
    meta_dict: {variable: {mu, sd, freq}} for unscaling predictions
    """
    sa = panels.sa
    meta = panels.meta
    freq_map = dict(zip(meta["variable"], meta["freq"]))
    sa_vars = meta[meta["sa_n"].notna()]["variable"].tolist()

    if variable_ids is not None:
        sa_vars = [v for v in variable_ids if v in sa_vars]

    # Ensure GDP is included
    if "gdp" not in sa_vars and "gdp" in meta["variable"].values:
        sa_vars.append("gdp")

    monthly_vars = [v for v in sa_vars if freq_map.get(v) == "M"]
    quarterly_vars = [v for v in sa_vars if freq_map.get(v) == "Q"]

    meta_dict = {}
    for v in sa_vars:
        row = meta[meta["variable"] == v].iloc[0]
        meta_dict[v] = {
            "mu": row.get("sa_mean", 0),
            "sd": row.get("sa_sd", 1),
            "freq": freq_map.get(v, "M"),
        }

    # Monthly panel — need clean DatetimeIndex with freq="ME"
    m_parts = {}
    for v in monthly_vars:
        d = sa[(sa["variable"] == v) & (sa["freq"] == "M")][["period", "z"]].copy()
        pi = pd.PeriodIndex(d["period"], freq="M")
        # to_timestamp('start') gives clean dates, then shift to month-end
        d["dt"] = pi.to_timestamp(how="start") + pd.offsets.MonthEnd(0)
        m_parts[v] = d.set_index("dt")["z"]

    if m_parts:
        monthly_df = pd.DataFrame(m_parts)
        monthly_df = monthly_df.sort_index()
        monthly_df.index.freq = pd.tseries.frequencies.to_offset("ME")
        # Extend to cover the target quarter's last month
        target_end_p = target_quarter.asfreq("M", how="end")
        target_end = target_end_p.to_timestamp(how="start") + pd.offsets.MonthEnd(0)
        if target_end > monthly_df.index[-1]:
            ext = pd.date_range(monthly_df.index[-1] + pd.offsets.MonthEnd(1),
                                target_end, freq="ME")
            ext_df = pd.DataFrame(np.nan, index=ext, columns=monthly_df.columns)
            monthly_df = pd.concat([monthly_df, ext_df])
    else:
        monthly_df = pd.DataFrame()

    # Quarterly panel — need clean DatetimeIndex with freq="QE"
    q_parts = {}
    for v in quarterly_vars:
        d = sa[(sa["variable"] == v) & (sa["freq"] == "Q")][["period", "z"]].copy()
        pi = pd.PeriodIndex(d["period"], freq="Q")
        d["dt"] = pi.to_timestamp(how="start") + pd.offsets.QuarterEnd(0)
        q_parts[v] = d.set_index("dt")["z"]

    if q_parts:
        quarterly_df = pd.DataFrame(q_parts)
        quarterly_df = quarterly_df.sort_index()
        quarterly_df.index.freq = pd.tseries.frequencies.to_offset("QE")
    else:
        quarterly_df = pd.DataFrame()

    return monthly_df, quarterly_df, meta_dict


class DFM:
    """DynamicFactorMQ nowcasting model."""

    name = "dfm"

    def __init__(
        self,
        n_factors: int = 2,
        factor_orders: int = 2,
        idiosyncratic_ar1: bool = True,
        em_max_iter: int = 250,
        variable_ids: list[str] | None = None,
        intercept_correction: bool = True,
    ):
        self.n_factors = n_factors
        self.factor_orders = factor_orders
        self.idiosyncratic_ar1 = idiosyncratic_ar1
        self.em_max_iter = em_max_iter
        self.variable_ids = variable_ids
        self.intercept_correction = intercept_correction

    def predict(self, panels: Panels, target_quarter: pd.Period) -> dict:
        """Estimate DFM and produce GDP y/y nowcast for target_quarter."""
        from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ

        monthly_df, quarterly_df, meta_dict = _build_endog(
            panels, self.variable_ids, target_quarter
        )

        if monthly_df.empty or monthly_df.shape[1] < 2:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "insufficient monthly SA data"}}

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = DynamicFactorMQ(
                    endog=monthly_df,
                    endog_quarterly=quarterly_df if not quarterly_df.empty else None,
                    factors=self.n_factors,
                    factor_orders=self.factor_orders,
                    idiosyncratic_ar1=self.idiosyncratic_ar1,
                    standardize=False,  # already z-scored
                )
                result = model.fit(
                    disp=False,
                    maxiter=self.em_max_iter,
                    em_initialization=True,
                )
        except Exception as e:
            logger.warning("DFM estimation failed: %s", e)
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"estimation failed: {e}"}}

        # Get predicted values (Kalman smoother fills missing/future)
        try:
            pred = result.get_prediction()
            pred_mean = pred.predicted_mean
        except Exception as e:
            logger.warning("DFM prediction failed: %s", e)
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"prediction failed: {e}"}}

        # Extract GDP prediction for the target quarter
        if "gdp" not in pred_mean.columns:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "GDP not in prediction output"}}

        gdp_z = pred_mean["gdp"]
        gdp_meta = meta_dict.get("gdp", {"mu": 0, "sd": 1})
        mu, sd = gdp_meta["mu"], gdp_meta["sd"]

        # GDP is quarterly in the SA panel: the predicted values are at monthly freq
        # but represent the quarterly variable. Sum the three months of the quarter
        # to get the quarterly prediction.
        target_start = target_quarter.asfreq("M", how="start").to_timestamp()
        target_end = target_quarter.asfreq("M", how="end").to_timestamp(how="end")

        # Get GDP z-score for the target quarter's last month (that's where quarterly
        # data is placed in the DFM internal representation)
        target_month_end = target_quarter.asfreq("M", how="end").to_timestamp(how="end")
        if target_month_end in gdp_z.index:
            gdp_z_val = gdp_z[target_month_end]
        else:
            # Try nearest
            close = gdp_z.index[gdp_z.index >= target_start]
            if len(close) == 0:
                return {"gdp_yoy": np.nan, "model": self.name,
                        "info": {"error": f"no GDP prediction for {target_quarter}"}}
            gdp_z_val = gdp_z[close[-1]]

        if np.isnan(gdp_z_val):
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "GDP prediction is NaN"}}

        # Unscale z to SA dlog×100
        gdp_dlog = gdp_z_val * sd + mu

        # Clamp dlog to prevent exp() overflow (±50% q/q is extreme)
        gdp_dlog = float(np.clip(gdp_dlog, -50.0, 50.0))

        # Convert dlog to y/y growth using SA levels
        gdp_yoy = self._dlog_to_yoy(gdp_dlog, panels, target_quarter)

        # Intercept correction: compute DFM's in-sample prediction errors
        # on known GDP quarters and subtract the mean bias
        if self.intercept_correction:
            gdp_yoy = self._apply_intercept_correction(
                gdp_yoy, pred_mean, meta_dict, panels, target_quarter
            )

        # Winsorise: GDP y/y should be in a plausible range
        # Use last 8 quarters of actual y/y for bounds (±3σ)
        yoy_panel = panels.yoy
        gdp_hist = yoy_panel[(yoy_panel["variable"] == "gdp") & (yoy_panel["freq"] == "Q")]
        if not gdp_hist.empty:
            hist_vals = gdp_hist["value"].dropna()
            if len(hist_vals) >= 4:
                y_mean = float(hist_vals.tail(12).mean())
                y_std = float(hist_vals.tail(12).std())
                lo = y_mean - 3 * max(y_std, 1.0)
                hi = y_mean + 3 * max(y_std, 1.0)
                gdp_yoy = float(np.clip(gdp_yoy, lo, hi))

        return {
            "gdp_yoy": float(gdp_yoy),
            "model": self.name,
            "info": {
                "n_monthly_vars": monthly_df.shape[1],
                "n_quarterly_vars": quarterly_df.shape[1] if not quarterly_df.empty else 0,
                "n_obs_monthly": int(monthly_df.notna().sum().sum()),
                "target": str(target_quarter),
                "gdp_z": float(gdp_z_val),
                "gdp_dlog": float(gdp_dlog),
            },
        }

    def _apply_intercept_correction(
        self,
        raw_yoy: float,
        pred_mean: pd.DataFrame,
        meta_dict: dict,
        panels: Panels,
        target_quarter: pd.Period,
    ) -> float:
        """Apply expanding-window intercept correction using in-sample errors.

        Computes the DFM's prediction for known GDP quarters and corrects
        the out-of-sample prediction by the mean recent bias.
        """
        yoy_panel = panels.yoy
        gdp_hist = yoy_panel[(yoy_panel["variable"] == "gdp") & (yoy_panel["freq"] == "Q")].copy()
        if gdp_hist.empty:
            return raw_yoy

        gdp_hist["pi"] = pd.PeriodIndex(gdp_hist["period"], freq="Q")
        gdp_hist = gdp_hist.sort_values("pi").drop_duplicates("pi", keep="last").set_index("pi")
        actuals = gdp_hist["value"]

        # Get DFM's in-sample GDP predictions for known quarters
        if "gdp" not in pred_mean.columns:
            return raw_yoy

        gdp_meta = meta_dict.get("gdp", {"mu": 0, "sd": 1})
        mu, sd = gdp_meta["mu"], gdp_meta["sd"]
        gdp_z = pred_mean["gdp"]

        errors = []
        for q in actuals.index[-8:]:  # Last 8 known quarters
            if q >= target_quarter:
                continue
            # Find the DFM prediction for this quarter
            q_end = q.asfreq("M", how="end").to_timestamp(how="end")
            if q_end in gdp_z.index:
                z_val = gdp_z[q_end]
                if np.isfinite(z_val):
                    dlog_pred = z_val * sd + mu
                    dlog_pred = float(np.clip(dlog_pred, -50, 50))
                    yoy_pred = self._dlog_to_yoy(dlog_pred, panels, q)
                    if np.isfinite(yoy_pred) and q in actuals.index:
                        errors.append(yoy_pred - actuals[q])

        if len(errors) >= 3:
            bias = float(np.mean(errors))
            # Shrink correction to avoid over-correcting
            correction = 0.5 * bias
            return raw_yoy - correction

        return raw_yoy

    @staticmethod
    def _dlog_to_yoy(
        dlog_pct: float,
        panels: Panels,
        target_quarter: pd.Period,
    ) -> float:
        """Convert a quarterly SA dlog×100 prediction to y/y growth.

        Uses GDP SA levels from the panel to anchor. The dlog represents the
        q/q growth of the SA level. y/y = (level_t / level_{t-4} - 1) * 100.
        """
        sa_level = panels.levels.get("gdp", {}).get("sa_level")
        if sa_level is None or sa_level.empty:
            # Fallback: use y/y panel directly with AR persistence
            return _fallback_yoy(dlog_pct, panels, target_quarter)

        # Aggregate monthly SA level to quarterly (sum for GDP)
        if hasattr(sa_level.index, "freqstr") and sa_level.index.freqstr and "M" in sa_level.index.freqstr:
            sa_q = sa_level.groupby(sa_level.index.to_timestamp().to_period("Q")).sum()
        else:
            sa_q = sa_level.copy()

        if sa_q.empty:
            return _fallback_yoy(dlog_pct, panels, target_quarter)

        last_q = sa_q.index[-1]
        last_level = sa_q.iloc[-1]

        # Project forward using dlog: level_next = level_prev * exp(dlog/100)
        steps = int((target_quarter - last_q).n) if target_quarter > last_q else 0
        projected = last_level
        for _ in range(max(steps, 1)):
            projected = projected * np.exp(dlog_pct / 100.0)

        # Guard against overflow/underflow
        if not np.isfinite(projected) or projected <= 0:
            return _fallback_yoy(dlog_pct, panels, target_quarter)

        # y/y: compare with same quarter a year ago
        year_ago = target_quarter - 4
        if year_ago in sa_q.index:
            year_ago_level = sa_q[year_ago]
            if year_ago_level > 0:
                yoy = (projected / year_ago_level - 1) * 100.0
                if np.isfinite(yoy):
                    return yoy

        return _fallback_yoy(dlog_pct, panels, target_quarter)


def _fallback_yoy(dlog_pct: float, panels: Panels, target_quarter: pd.Period) -> float:
    """Fallback: use last known y/y + AR(1) adjustment."""
    yoy = panels.yoy
    gdp = yoy[(yoy["variable"] == "gdp") & (yoy["freq"] == "Q")].copy()
    if gdp.empty:
        return np.nan
    gdp["pi"] = pd.PeriodIndex(gdp["period"], freq="Q")
    gdp = gdp.sort_values("pi").set_index("pi")
    last_yoy = gdp["value"].iloc[-1]
    last_q = gdp.index[-1]
    steps = max(1, int((target_quarter - last_q).n))
    # Drift toward long-run mean
    mean_yoy = gdp["value"].tail(8).mean()
    phi = 0.6
    forecast = last_yoy
    for _ in range(steps):
        forecast = mean_yoy + phi * (forecast - mean_yoy)
    return float(forecast)
