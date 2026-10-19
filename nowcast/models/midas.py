"""Model C: Regularised U-MIDAS with elastic net.

Unrestricted MIDAS (U-MIDAS) with Almon polynomial lag weighting and elastic net
regularisation. Maps monthly y/y indicators directly to quarterly GDP y/y growth.

Steps:
1. Align monthly indicators to quarterly GDP on the y/y panel.
2. For each monthly variable, create Almon-weighted lags (degree-P polynomial
   over K monthly lags) → one or a few regressors per variable.
3. Estimate elastic net on the resulting feature matrix.
4. Predict the target quarter GDP using available monthly data (handles ragged edge).

This avoids the "parameter proliferation" problem of putting all monthly lags
into a single regression.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import ElasticNetCV
from sklearn.preprocessing import StandardScaler

from nowcast.transform.panels import Panels

logger = logging.getLogger(__name__)


def _almon_weights(n_lags: int, degree: int) -> np.ndarray:
    """Almon polynomial lag weight matrix.

    Returns (n_lags, degree+1) matrix where column j = (0..n_lags-1)^j.
    The product X_almon = X_lags @ W reduces n_lags to degree+1 regressors.
    """
    lags = np.arange(n_lags, dtype=float)
    # Normalise to [0, 1] for numerical stability
    lags_norm = lags / max(n_lags - 1, 1)
    W = np.column_stack([lags_norm ** j for j in range(degree + 1)])
    return W


def _build_midas_features(
    yoy: pd.DataFrame,
    monthly_vars: list[str],
    n_lags: int,
    poly_degree: int,
    target_quarters: pd.PeriodIndex,
) -> pd.DataFrame:
    """Build Almon-weighted MIDAS feature matrix.

    For each target quarter q, collect the n_lags most recent monthly
    observations ending at the last month of q (or last available).
    Apply Almon polynomial weighting to reduce to poly_degree+1 features per variable.

    Returns DataFrame indexed by target_quarters, columns = var_almon_0, var_almon_1, ...
    """
    W = _almon_weights(n_lags, poly_degree)  # (n_lags, degree+1)

    features = {}
    for var in monthly_vars:
        d = yoy[(yoy["variable"] == var) & (yoy["freq"] == "M")].copy()
        if d.empty:
            continue
        d["pi"] = pd.PeriodIndex(d["period"], freq="M")
        d = d.sort_values("pi").drop_duplicates("pi", keep="last").set_index("pi")
        monthly_vals = d["value"]

        for q in target_quarters:
            # Last month of the quarter
            last_month = q.asfreq("M", how="end")
            # Get the n_lags months ending at last_month (or last available before)
            available = monthly_vals[monthly_vals.index <= last_month]
            if len(available) < 2:
                # Not enough data
                for j in range(poly_degree + 1):
                    key = f"{var}_a{j}"
                    features.setdefault(key, {})[q] = np.nan
                continue

            # Take last n_lags observations
            recent = available.iloc[-min(n_lags, len(available)):]
            x_lags = recent.values

            # Pad if fewer than n_lags
            if len(x_lags) < n_lags:
                pad = np.full(n_lags - len(x_lags), np.nan)
                x_lags = np.concatenate([pad, x_lags])

            # Apply Almon weights: (n_lags,) @ (n_lags, degree+1) = (degree+1,)
            # Handle NaN: replace with mean of non-NaN values
            mask = np.isnan(x_lags)
            if mask.all():
                almon = np.full(poly_degree + 1, np.nan)
            else:
                x_filled = x_lags.copy()
                x_filled[mask] = np.nanmean(x_lags)
                almon = x_filled @ W

            for j in range(poly_degree + 1):
                key = f"{var}_a{j}"
                features.setdefault(key, {})[q] = almon[j]

    if not features:
        return pd.DataFrame(index=target_quarters)

    df = pd.DataFrame(features, index=target_quarters)
    return df


class UMIDAS:
    """Regularised U-MIDAS nowcasting model."""

    name = "midas"

    def __init__(
        self,
        n_lags: int = 6,
        poly_degree: int = 3,
        alphas: list[float] | None = None,
        cv_folds: int = 5,
        min_train_quarters: int = 12,
        monthly_vars: list[str] | None = None,
    ):
        self.n_lags = n_lags
        self.poly_degree = poly_degree
        self.alphas = alphas or [0.1, 0.5, 0.9, 0.95, 0.99]
        self.cv_folds = cv_folds
        self.min_train = min_train_quarters
        self.monthly_vars = monthly_vars

    def predict(self, panels: Panels, target_quarter: pd.Period) -> dict:
        """Estimate U-MIDAS and produce GDP y/y nowcast."""
        yoy = panels.yoy

        # Get GDP quarterly y/y
        gdp = yoy[(yoy["variable"] == "gdp") & (yoy["freq"] == "Q")].copy()
        if gdp.empty or len(gdp) < self.min_train:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "insufficient GDP data"}}

        gdp["pi"] = pd.PeriodIndex(gdp["period"], freq="Q")
        gdp = gdp.sort_values("pi").drop_duplicates("pi", keep="last").set_index("pi")
        gdp_series = gdp["value"]

        # Determine monthly variables to use
        if self.monthly_vars is not None:
            m_vars = self.monthly_vars
        else:
            meta = panels.meta
            m_vars = meta[(meta["freq"] == "M")]["variable"].unique().tolist()

        # Build MIDAS feature matrix for all known quarters + target
        all_quarters = gdp_series.index.union(pd.PeriodIndex([target_quarter], freq="Q"))
        all_quarters = all_quarters.sort_values()

        X_all = _build_midas_features(
            yoy, m_vars, self.n_lags, self.poly_degree, all_quarters
        )

        if X_all.empty or X_all.shape[1] == 0:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "no MIDAS features built"}}

        # Split into training (where GDP is known) and prediction
        train_quarters = gdp_series.index.intersection(X_all.index)
        if len(train_quarters) < self.min_train:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"only {len(train_quarters)} training obs"}}

        y_train = gdp_series.loc[train_quarters].values
        X_train = X_all.loc[train_quarters].values

        # Drop columns that are all NaN in training
        good_cols = ~np.isnan(X_train).all(axis=0)
        if not good_cols.any():
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": "all feature columns are NaN"}}

        X_train = X_train[:, good_cols]
        col_names = X_all.columns[good_cols].tolist()

        # Fill remaining NaN with column means
        col_means = np.nanmean(X_train, axis=0)
        for j in range(X_train.shape[1]):
            mask = np.isnan(X_train[:, j])
            if mask.any():
                X_train[mask, j] = col_means[j]

        # Scale features
        scaler = StandardScaler()
        X_train_sc = scaler.fit_transform(X_train)

        # Elastic net with time-series CV
        n_folds = min(self.cv_folds, max(2, len(y_train) // 4))

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ElasticNetCV(
                    l1_ratio=self.alphas,
                    cv=n_folds,
                    max_iter=5000,
                    random_state=42,
                )
                model.fit(X_train_sc, y_train)
        except Exception as e:
            logger.warning("MIDAS elastic net failed: %s", e)
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"elastic net failed: {e}"}}

        # Predict target quarter
        if target_quarter in X_all.index:
            x_target = X_all.loc[target_quarter].values[good_cols]
        else:
            return {"gdp_yoy": np.nan, "model": self.name,
                    "info": {"error": f"no features for {target_quarter}"}}

        # Fill NaN in target
        for j in range(len(x_target)):
            if np.isnan(x_target[j]):
                x_target[j] = col_means[j]

        x_target_sc = scaler.transform(x_target.reshape(1, -1))
        prediction = float(model.predict(x_target_sc)[0])

        # Winsorise extreme predictions: clip to ±2.5σ of training GDP
        y_mean, y_std = float(np.mean(y_train)), float(np.std(y_train))
        lo = y_mean - 2.5 * max(y_std, 1.0)
        hi = y_mean + 2.5 * max(y_std, 1.0)
        prediction = float(np.clip(prediction, lo, hi))

        # Feature importance
        coef = model.coef_
        nonzero = np.abs(coef) > 1e-8
        important = [(col_names[i], float(coef[i]))
                     for i in np.argsort(-np.abs(coef))[:10]
                     if nonzero[i]]

        resid = y_train - model.predict(X_train_sc)
        rmse_in = float(np.sqrt(np.mean(resid ** 2)))

        return {
            "gdp_yoy": prediction,
            "model": self.name,
            "info": {
                "target": str(target_quarter),
                "n_obs": len(y_train),
                "n_features": int(X_train.shape[1]),
                "n_nonzero": int(nonzero.sum()),
                "l1_ratio": float(model.l1_ratio_),
                "alpha": float(model.alpha_),
                "rmse_in": rmse_in,
                "top_features": important,
            },
        }
