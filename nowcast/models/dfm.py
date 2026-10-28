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
    freq_map = dict(zip(meta["variable"], meta["freq"], strict=False))
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
        self.intercept_correction = intercept_correction  # reserved for future use

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

        # GDP q/q growth for every quarter, from the smoothed estimates (all data in the panel)
        try:
            gdp_dlog_q = quarterly_gdp_dlog(result, meta_dict)
        except Exception as e:
            logger.warning("DFM prediction failed: %s", e)
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"prediction failed: {e}"}}
        if gdp_dlog_q is None:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "GDP not in prediction output"}}

        gdp_dlog = gdp_dlog_q.get(target_quarter, np.nan)
        if not np.isfinite(gdp_dlog):
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"no GDP prediction for {target_quarter}"}}

        # Chain the quarterly growth path from the last official GDP level to the target
        gdp_yoy = dlog_path_to_yoy(gdp_dlog_q, panels, target_quarter)
        gdp_z_val = (gdp_dlog - meta_dict["gdp"]["mu"]) / meta_dict["gdp"]["sd"]

        # Winsorise: GDP y/y should be in a plausible range
        # Bounds: mean ± 3 SD (SD at least 1 pp) of the last 12 quarters of official y/y
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

        out = {
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
        # Stash the result object for Phase 4 news decomposition
        self._last_result = result
        return out


def quarterly_gdp_dlog(result, meta_dict: dict) -> pd.Series | None:
    """GDP q/q growth (SA dlog x 100) for every quarter in the sample, one value per quarter.

    Uses the smoothed estimates, which condition on every observation in the panel. The
    one-step-ahead predictions (the statsmodels default) leave out the latest month of data.
    DynamicFactorMQ places a quarterly variable at the last month of its quarter, so the value
    for quarter q is the smoothed estimate at q's third month.
    """
    pred = result.get_prediction(information_set="smoothed").predicted_mean
    if "gdp" not in pred.columns:
        return None
    gdp = pred["gdp"]
    idx = gdp.index
    if not isinstance(idx, pd.PeriodIndex):
        idx = pd.DatetimeIndex(idx).to_period("M")
    gdp = pd.Series(gdp.to_numpy(), index=idx)
    gdp = gdp[gdp.index.month % 3 == 0]
    gdp.index = gdp.index.asfreq("Q")
    meta = meta_dict.get("gdp", {"mu": 0.0, "sd": 1.0})
    dlog = gdp * meta["sd"] + meta["mu"]
    # Clamp to keep exp() finite (+-50% q/q is far outside anything observed)
    return dlog.clip(-50.0, 50.0)


def gdp_sa_quarterly(panels: Panels) -> pd.Series | None:
    """Quarterly SA GDP level (PeriodIndex, freq Q), or None when the panel has none."""
    sa_level = panels.levels.get("gdp", {}).get("sa_level")
    if sa_level is None or sa_level.empty:
        return None
    if not isinstance(sa_level.index, pd.PeriodIndex):
        sa_level = sa_level.copy()
        sa_level.index = pd.DatetimeIndex(sa_level.index).to_period("Q")
    if sa_level.index.freqstr.startswith("M"):
        sa_level = sa_level.groupby(sa_level.index.asfreq("Q")).sum()
    return sa_level.dropna().sort_index()


def dlog_path_to_yoy(
    dlog_q: pd.Series,
    panels: Panels,
    target_quarter: pd.Period,
) -> float:
    """Turn the model's quarterly growth path into y/y growth for *target_quarter*.

    Starts from the last official SA GDP level and applies the model's q/q growth for each
    quarter after it, up to the target: level_T = level_L * exp(sum of dlog_{L+1..T} / 100).
    Each quarter uses its own growth rate. If the target quarter is already published, its
    official SA level is used. y/y compares with the SA level four quarters earlier.
    """
    sa_q = gdp_sa_quarterly(panels)
    target_dlog = dlog_q.get(target_quarter, np.nan) if dlog_q is not None else np.nan
    if sa_q is None or sa_q.empty:
        return _fallback_yoy(target_dlog, panels, target_quarter)

    last_q = sa_q.index[-1]
    if target_quarter <= last_q:
        level = sa_q.get(target_quarter, np.nan)
    else:
        path = [dlog_q.get(last_q + i, np.nan) for i in range(1, (target_quarter - last_q).n + 1)]
        if not np.all(np.isfinite(path)):
            return _fallback_yoy(target_dlog, panels, target_quarter)
        level = sa_q.iloc[-1] * np.exp(np.sum(path) / 100.0)

    year_ago = target_quarter - 4
    base = sa_q.get(year_ago, np.nan)
    if not (np.isfinite(level) and np.isfinite(base)) or base <= 0 or level <= 0:
        return _fallback_yoy(target_dlog, panels, target_quarter)
    return float((level / base - 1) * 100.0)


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
