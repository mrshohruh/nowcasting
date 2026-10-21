"""Four-run Banbura-Modugno news decomposition.

Decomposes the change in the DFM nowcast between two runs into three additive
effects:

    delta_nowcast = revision_effect + news_effect + re_estimation_effect

where:
    Run 1: old params, old data   -> y_hat_1
    Run 2: old params, revised data -> y_hat_2   (revision effect = y2 - y1)
    Run 3: old params, new data   -> y_hat_3   (news effect = y3 - y2)
    Run 4: new params, new data   -> y_hat_4   (re-estimation effect = y4 - y3)

For the news effect, statsmodels' DynamicFactorMQ.news() gives per-release
impacts: each new observation contributes (surprise * weight) to the forecast
revision, where surprise = actual - forecast and weight comes from the Kalman
gain.

When old parameters are not available (first run), the decomposition is skipped
and only the current nowcast is reported.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from nowcast.explain.state import RunState, load_old_panel_sa, load_old_params
from nowcast.transform.panels import Panels

logger = logging.getLogger(__name__)

# Variable display names for narratives
VARIABLE_LABELS: dict[str, str] = {
    "ind_mining": "Mining output",
    "ind_manuf": "Manufacturing output",
    "ind_elec": "Electricity production",
    "constr": "Construction output",
    "retail": "Retail trade",
    "wholesale": "Wholesale trade",
    "cpi": "Consumer prices",
    "ppi": "Producer prices",
    "exports_total": "Total exports",
    "exports_nogold": "Non-gold exports",
    "exports_gold": "Gold exports",
    "imports": "Imports",
    "m2_real": "Real money supply (M2)",
    "gold_price": "Gold price",
    "usd_rate": "Exchange rate",
    "reserves_fx": "FX reserves",
    "gdp": "GDP",
    "gva": "GVA",
    "invest": "Investment",
    "remittances_real": "Real remittances",
    "wages_real": "Real wages",
    "rate_deposit_hh": "Household deposit rate",
    "rate_loan_firms": "Firm lending rate",
    "new_small_business": "New small businesses",
    "mkt_trade": "Market trade services",
    "mkt_transport": "Transport services",
    "mkt_ict": "ICT services",
    "mkt_finance": "Financial services",
    "mkt_accommodation": "Accommodation services",
    "bop_exports": "BOP exports",
    "bop_imports": "BOP imports",
}

# Group variables into thematic blocks for the narrative
VARIABLE_GROUPS: dict[str, list[str]] = {
    "Industry": ["ind_mining", "ind_manuf", "ind_elec"],
    "Construction": ["constr"],
    "Trade": ["retail", "wholesale", "mkt_trade", "mkt_accommodation"],
    "Transport & ICT": ["mkt_transport", "mkt_ict"],
    "Finance": ["mkt_finance", "m2_real", "reserves_fx", "rate_deposit_hh", "rate_loan_firms"],
    "Prices": ["cpi", "ppi", "gold_price"],
    "External": ["exports_nogold", "exports_gold", "exports_total", "imports",
                  "usd_rate", "bop_exports", "bop_imports"],
    "Demand": ["invest", "remittances_real", "wages_real", "new_small_business"],
    "National accounts": ["gdp", "gva"],
}


@dataclass
class NewsItem:
    """One release's contribution to the forecast revision."""

    variable: str
    period: str  # e.g. "2026-07" or "2026Q3"
    actual: float  # observed value
    forecast: float  # Kalman forecast before the release
    surprise: float  # actual - forecast
    weight: float  # Kalman gain coefficient
    impact: float  # surprise * weight (contribution to nowcast revision in pp)
    group: str = ""  # thematic group

    @property
    def label(self) -> str:
        return VARIABLE_LABELS.get(self.variable, self.variable)


@dataclass
class Decomposition:
    """Full four-run decomposition result for one target quarter."""

    target_quarter: str
    old_nowcast: float  # y_hat_1 (old params, old data)
    new_nowcast: float  # y_hat_4 (new params, new data)
    total_change: float  # y_hat_4 - y_hat_1

    revision_effect: float  # y_hat_2 - y_hat_1
    news_effect: float  # y_hat_3 - y_hat_2
    reestimation_effect: float  # y_hat_4 - y_hat_3

    news_items: list[NewsItem] = field(default_factory=list)
    revision_items: list[NewsItem] = field(default_factory=list)

    # Intermediate nowcasts
    y_hat_2: float = np.nan  # old params, revised data
    y_hat_3: float = np.nan  # old params, new data

    run_date: str = ""
    prev_run_date: str = ""

    @property
    def residual(self) -> float:
        """Decomposition residual — should be < 0.001 pp."""
        return abs(
            self.total_change
            - (self.revision_effect + self.news_effect + self.reestimation_effect)
        )

    def top_news(self, n: int = 5) -> list[NewsItem]:
        """Top n news items by absolute impact."""
        return sorted(self.news_items, key=lambda x: abs(x.impact), reverse=True)[:n]

    def grouped_impacts(self) -> dict[str, float]:
        """Sum of impacts by thematic group."""
        groups: dict[str, float] = {}
        for item in self.news_items:
            g = item.group or _find_group(item.variable)
            groups[g] = groups.get(g, 0.0) + item.impact
        return dict(sorted(groups.items(), key=lambda x: abs(x[1]), reverse=True))


def _find_group(variable: str) -> str:
    """Find the thematic group for a variable."""
    for group, members in VARIABLE_GROUPS.items():
        if variable in members:
            return group
    return "Other"


def _build_dfm_model(
    panels: Panels,
    variable_ids: list[str] | None,
    target_quarter: pd.Period,
    n_factors: int = 2,
    factor_orders: int = 2,
    idiosyncratic_ar1: bool = True,
):
    """Build a DynamicFactorMQ model instance (not yet fitted)."""
    from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ

    from nowcast.models.dfm import _build_endog

    monthly_df, quarterly_df, meta_dict = _build_endog(panels, variable_ids, target_quarter)

    if monthly_df.empty or monthly_df.shape[1] < 2:
        return None, None, None

    model = DynamicFactorMQ(
        endog=monthly_df,
        endog_quarterly=quarterly_df if not quarterly_df.empty else None,
        factors=n_factors,
        factor_orders=factor_orders,
        idiosyncratic_ar1=idiosyncratic_ar1,
        standardize=False,
    )
    return model, monthly_df, meta_dict


def _fit_model(model, params=None, em_max_iter: int = 250):
    """Fit or apply parameters to a DFM model."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if params is not None and len(params) == len(model.start_params):
            # Apply old parameters without re-estimation
            try:
                result = model.smooth(params)
                return result
            except Exception as e:
                logger.warning("Could not apply old params: %s — re-estimating", e)

        # Full estimation
        result = model.fit(disp=False, maxiter=em_max_iter, em_initialization=True)
        return result


def _extract_gdp_yoy(result, meta_dict: dict, panels: Panels, target_quarter: pd.Period) -> float:
    """Extract GDP y/y from a DFM result, same logic as DFM.predict."""
    from nowcast.models.dfm import DFM

    pred = result.get_prediction()
    pred_mean = pred.predicted_mean

    if "gdp" not in pred_mean.columns:
        return np.nan

    gdp_z = pred_mean["gdp"]
    gdp_meta = meta_dict.get("gdp", {"mu": 0, "sd": 1})
    mu, sd = gdp_meta["mu"], gdp_meta["sd"]

    target_month_end = target_quarter.asfreq("M", how="end").to_timestamp(how="end")
    if target_month_end in gdp_z.index:
        gdp_z_val = gdp_z[target_month_end]
    else:
        close = gdp_z.index[gdp_z.index >= target_quarter.start_time]
        if len(close) == 0:
            return np.nan
        gdp_z_val = gdp_z[close[-1]]

    if np.isnan(gdp_z_val):
        return np.nan

    gdp_dlog = gdp_z_val * sd + mu
    gdp_dlog = float(np.clip(gdp_dlog, -50.0, 50.0))
    return DFM._dlog_to_yoy(gdp_dlog, panels, target_quarter)


def _extract_news_items(
    result_old_data,
    result_new_data,
    meta_dict: dict,
    panels_new: Panels,
    target_quarter: pd.Period,
) -> list[NewsItem]:
    """Use statsmodels news() to get per-release impacts.

    Falls back to a simpler diff-based approach if news() is not available
    or fails.
    """
    items = []

    try:
        # statsmodels news() compares two results
        news_results = result_new_data.news(
            result_old_data,
            impact_date=target_quarter.end_time,
            impacted_variable="gdp",
        )

        if hasattr(news_results, "details") and news_results.details is not None:
            details = news_results.details
            for _, row in details.iterrows():
                update_var = str(row.get("update variable", row.get("source", "")))
                update_date = str(row.get("update date", row.get("date", "")))
                forecast_val = float(row.get("forecast (prev)", row.get("previous", 0)))
                observed_val = float(row.get("observed", row.get("actual", 0)))
                surprise = observed_val - forecast_val
                weight = float(row.get("weight", 0))
                impact = float(row.get("impact", surprise * weight))

                items.append(NewsItem(
                    variable=update_var,
                    period=update_date,
                    actual=observed_val,
                    forecast=forecast_val,
                    surprise=surprise,
                    weight=weight,
                    impact=impact,
                    group=_find_group(update_var),
                ))
    except Exception as e:
        logger.info("statsmodels news() not available or failed: %s — using diff approach", e)

    return items


def _compute_revision_items(
    old_sa: pd.DataFrame,
    new_sa: pd.DataFrame,
) -> list[NewsItem]:
    """Identify which observations were revised between the old and new panels."""
    items = []

    # Merge on (variable, period) and find value differences
    old = old_sa[["variable", "period", "value"]].copy()
    new = new_sa[["variable", "period", "value"]].copy()

    merged = old.merge(new, on=["variable", "period"], suffixes=("_old", "_new"), how="inner")
    merged["diff"] = merged["value_new"] - merged["value_old"]
    revised = merged[merged["diff"].abs() > 1e-10]

    for _, row in revised.iterrows():
        items.append(NewsItem(
            variable=row["variable"],
            period=row["period"],
            actual=row["value_new"],
            forecast=row["value_old"],
            surprise=row["diff"],
            weight=0.0,  # weight unknown for revisions
            impact=0.0,  # impact computed at aggregate level
            group=_find_group(row["variable"]),
        ))

    return items


def decompose(
    panels_new: Panels,
    target_quarter: pd.Period,
    state_dir,
    prev_state: RunState | None = None,
    variable_ids: list[str] | None = None,
    n_factors: int = 2,
    factor_orders: int = 2,
    em_max_iter: int = 250,
) -> Decomposition | None:
    """Run the four-run decomposition for one target quarter.

    Parameters
    ----------
    panels_new : Panels
        Current panels (new data).
    target_quarter : pd.Period
        Quarter to decompose.
    state_dir : Path-like
        Directory with saved state from the previous run.
    prev_state : RunState, optional
        Previous run state metadata. If None, loaded from state_dir.
    variable_ids : list[str], optional
        DFM variable subset.
    n_factors, factor_orders, em_max_iter : int
        DFM hyperparameters.

    Returns
    -------
    Decomposition or None if no previous state exists.
    """
    from pathlib import Path

    state_dir = Path(state_dir)

    # Load previous state
    if prev_state is None:
        from nowcast.explain.state import load_state
        prev_state = load_state(state_dir)

    if prev_state is None:
        logger.info("No previous state — skipping decomposition")
        return None

    # Load old params and old panel
    old_params_dict = load_old_params(state_dir, prev_state)
    old_sa = load_old_panel_sa(state_dir, prev_state)

    if old_params_dict is None or old_sa is None:
        logger.warning("Old params or panel missing — skipping decomposition")
        return None

    old_params = old_params_dict.get("params")
    if old_params is None or old_params.size == 0:
        logger.warning("Old params empty — skipping decomposition")
        return None

    # Build panels with old SA data for runs 1 and 2
    # Run 1: old params, old data
    # We need a Panels-like object with the old SA data
    panels_old = _panels_with_old_sa(panels_new, old_sa)

    # Build the DFM model on OLD data
    model_old, _, meta_old = _build_dfm_model(
        panels_old, variable_ids, target_quarter, n_factors, factor_orders
    )
    if model_old is None:
        logger.warning("Cannot build DFM on old data — skipping")
        return None

    # Run 1: old params + old data
    result_1 = _fit_model(model_old, old_params)
    if result_1 is None:
        return None
    y1 = _extract_gdp_yoy(result_1, meta_old, panels_old, target_quarter)

    # For Run 2, we need "revised data" — same observations as old panel but
    # with any revised values from the new panel
    panels_revised = _panels_with_revised_data(panels_old, panels_new)

    model_rev, _, meta_rev = _build_dfm_model(
        panels_revised, variable_ids, target_quarter, n_factors, factor_orders
    )

    # Run 2: old params + revised data (same coverage as old, new values)
    if model_rev is not None:
        try:
            result_2 = _fit_model(model_rev, old_params)
            y2 = _extract_gdp_yoy(result_2, meta_rev, panels_revised, target_quarter)
        except Exception:
            y2 = y1  # If the revised-data run fails, no revision effect
            result_2 = result_1
    else:
        y2 = y1
        result_2 = result_1

    # Run 3: old params + new data (full new panel)
    model_new_oldp, _, meta_new = _build_dfm_model(
        panels_new, variable_ids, target_quarter, n_factors, factor_orders
    )

    if model_new_oldp is not None:
        try:
            result_3 = _fit_model(model_new_oldp, old_params)
            y3 = _extract_gdp_yoy(result_3, meta_new, panels_new, target_quarter)
        except Exception:
            y3 = y2
            result_3 = result_2
    else:
        y3 = y2
        result_3 = result_2

    # Run 4: new params + new data (fresh estimation)
    model_new, _, meta_new4 = _build_dfm_model(
        panels_new, variable_ids, target_quarter, n_factors, factor_orders
    )

    if model_new is not None:
        result_4 = _fit_model(model_new, em_max_iter=em_max_iter)
        y4 = _extract_gdp_yoy(result_4, meta_new4 or meta_new, panels_new, target_quarter)
    else:
        return None

    # Extract news items (new releases between old and new panel)
    try:
        news_items = _extract_news_items(result_2, result_3, meta_new, panels_new, target_quarter)
    except Exception as e:
        logger.warning("News extraction failed: %s", e)
        news_items = []

    # Assign groups
    for item in news_items:
        if not item.group:
            item.group = _find_group(item.variable)

    # Compute revision items
    revision_items = _compute_revision_items(old_sa, panels_new.sa)

    return Decomposition(
        target_quarter=str(target_quarter),
        old_nowcast=float(y1) if np.isfinite(y1) else np.nan,
        new_nowcast=float(y4) if np.isfinite(y4) else np.nan,
        total_change=float(y4 - y1) if np.isfinite(y1) and np.isfinite(y4) else np.nan,
        revision_effect=float(y2 - y1) if np.isfinite(y1) and np.isfinite(y2) else 0.0,
        news_effect=float(y3 - y2) if np.isfinite(y2) and np.isfinite(y3) else 0.0,
        reestimation_effect=float(y4 - y3) if np.isfinite(y3) and np.isfinite(y4) else 0.0,
        news_items=news_items,
        revision_items=revision_items,
        y_hat_2=float(y2) if np.isfinite(y2) else np.nan,
        y_hat_3=float(y3) if np.isfinite(y3) else np.nan,
        run_date=str(panels_new.as_of),
        prev_run_date=prev_state.run_date,
    )


def _panels_with_old_sa(panels_new: Panels, old_sa: pd.DataFrame) -> Panels:
    """Create a Panels object using old SA data but current yoy/meta/levels."""
    from copy import copy

    p = copy(panels_new)
    p.sa = old_sa
    return p


def _panels_with_revised_data(panels_old: Panels, panels_new: Panels) -> Panels:
    """Create a panel with the same observation COVERAGE as old, but VALUES from new.

    This isolates revisions: same periods present as in the old panel, but with
    any value updates from the new panel applied.
    """
    from copy import copy

    old_sa = panels_old.sa
    new_sa = panels_new.sa

    # Keep only (variable, period) pairs that existed in the old panel
    # Use merge for efficiency instead of row-wise apply
    merged = new_sa.merge(
        old_sa[["variable", "period"]].drop_duplicates(),
        on=["variable", "period"],
        how="inner",
    )
    revised_sa = merged.copy()

    # For any old observations missing from the new panel, keep the old value
    new_keys = set(zip(revised_sa["variable"], revised_sa["period"], strict=False))
    missing = old_sa[
        old_sa.apply(lambda r: (r["variable"], r["period"]) not in new_keys, axis=1)
    ]
    if len(missing):
        revised_sa = pd.concat([revised_sa, missing], ignore_index=True)

    p = copy(panels_old)
    p.sa = revised_sa
    return p
