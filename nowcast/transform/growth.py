"""Tasks 2.6 and 2.7: growth rates, standardisation, outliers.

y/y panel (bridges, MIDAS): percent change on the same month (quarter) a year earlier,
    100 * (L_t / L_(t-12) - 1); interest rates as the change in percentage points.
SA panel (factor model): 100 * log change of the seasonally adjusted level on the previous month
    (quarter); interest rates as the change in percentage points.

Standardisation uses the mean and standard deviation of the values in the panel being built.
Because ``build_panels(as_of)`` only ever sees data available on ``as_of``, the estimation sample
is the as-of sample and no future value enters the scaling.

Outliers (factor model only): a value more than 4 robust standard deviations
(1.4826 x median absolute deviation) from the variable's median is set to missing in
``value_clean``; ``value`` keeps the computed number. The bridges instead get dummies for 2020Q2
and 2020Q3 (``covid_dummies``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nowcast.transform.decumulate import periods_per_year, regular

OUTLIER_K = 4.0
DUMMY_QUARTERS = ("2020Q2", "2020Q3")


def yoy_pct(level: pd.Series) -> pd.Series:
    level = regular(level)
    return 100.0 * (level / level.shift(periods_per_year(level.index)) - 1.0)


def yoy_diff(rate: pd.Series) -> pd.Series:
    rate = regular(rate)
    return rate - rate.shift(periods_per_year(rate.index))


def log_growth(level: pd.Series) -> pd.Series:
    level = regular(level)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100.0 * np.log(level / level.shift(1))


def diff(rate: pd.Series) -> pd.Series:
    rate = regular(rate)
    return rate - rate.shift(1)


def robust_outliers(x: pd.Series, k: float = OUTLIER_K) -> pd.Series:
    """True where |x - median| > k * 1.4826 * MAD."""
    v = x.dropna()
    if len(v) < 8:
        return pd.Series(False, index=x.index)
    med = v.median()
    scale = 1.4826 * (v - med).abs().median()
    if scale == 0 or not np.isfinite(scale):
        return pd.Series(False, index=x.index)
    return ((x - med).abs() > k * scale).fillna(False)


def standardise(x: pd.Series) -> tuple[pd.Series, float, float]:
    v = x.dropna()
    if len(v) < 2:
        return x * np.nan, np.nan, np.nan
    mu, sd = float(v.mean()), float(v.std(ddof=1))
    if sd == 0:
        return x * np.nan, mu, sd
    return (x - mu) / sd, mu, sd


def covid_dummies(index: pd.PeriodIndex) -> pd.DataFrame:
    """One 0/1 column per quarter in DUMMY_QUARTERS (quarterly index)."""
    return pd.DataFrame({f"d_{q}": (index == pd.Period(q, freq="Q")).astype(float) for q in DUMMY_QUARTERS},
                        index=index)
