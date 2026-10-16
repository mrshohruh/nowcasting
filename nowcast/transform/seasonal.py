"""Task 2.5: seasonal adjustment with robust STL on log levels.

STL (statsmodels) splits log(level) into trend, seasonal and remainder; the adjusted level is
exp(log level - seasonal). ``robust=True`` downweights outliers (2020, the 2019 price
liberalisation) so they do not distort the seasonal pattern. The seasonal smoother spans 13 years
of the same month (7 for quarters), i.e. a slowly moving pattern: with 5-8 years of data this is
close to a fixed pattern, which is what such short series can support.

STL needs a complete series: inside gaps are filled by log-linear interpolation for the fit only
and are missing again in the output. Series shorter than three full years are not adjusted.

Moving holidays (Navruz is fixed on 21 March, but Eid al-Fitr and Eid al-Adha move by about 11
days a year) are not modelled; the transformation report checks the adjusted series for leftover
seasonality, and X-13 with holiday regressors is tried later only if it shows up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from nowcast.transform.decumulate import periods_per_year, regular

MIN_YEARS = 3


def stl_adjust(level: pd.Series) -> tuple[pd.Series | None, str]:
    """Return (seasonally adjusted level, note). ``None`` if the series cannot be adjusted."""
    level = regular(level)
    s = periods_per_year(level.index) if len(level) else 12
    if level.notna().sum() < MIN_YEARS * s:
        return None, f"not adjusted: fewer than {MIN_YEARS} years"
    if (level.dropna() <= 0).any():
        return None, "not adjusted: non-positive values"
    lg = np.log(level)
    pos = np.arange(len(lg), dtype=float)
    ok = lg.notna().to_numpy()
    filled = pd.Series(np.interp(pos, pos[ok], lg.to_numpy()[ok]), index=lg.index)
    res = STL(filled.to_numpy(), period=s, seasonal=13 if s == 12 else 7, robust=True).fit()
    sa = np.exp(filled.to_numpy() - res.seasonal)
    out = pd.Series(sa, index=level.index).where(level.notna())
    note = "STL robust" + (f", {int((~ok).sum())} gaps filled for the fit only" if (~ok).any() else "")
    return out, note


def seasonal_strength(level: pd.Series) -> float | None:
    """Share of the variance of detrended log level explained by the seasonal (0-1; Wang et al. 2006)."""
    level = regular(level).dropna()
    s = periods_per_year(level.index) if len(level) else 12
    if len(level) < MIN_YEARS * s or (level <= 0).any():
        return None
    res = STL(np.log(level.to_numpy()), period=s, seasonal=13 if s == 12 else 7, robust=True).fit()
    r, sr = res.resid, res.resid + res.seasonal
    return float(max(0.0, 1.0 - np.var(r) / np.var(sr)))


def residual_seasonality(growth: pd.Series) -> float | None:
    """p-value of an F-test that month (quarter) means of a growth series are all equal.

    Applied to seasonally adjusted m/m growth: a small p-value means seasonality is left over.
    """
    from scipy import stats

    g = growth.dropna()
    if len(g) < 2 * periods_per_year(g.index):
        return None
    pos = g.index.month if periods_per_year(g.index) == 12 else g.index.quarter
    groups = [g[pos == k].to_numpy() for k in sorted(set(pos))]
    groups = [x for x in groups if len(x) >= 2]
    if len(groups) < 2:
        return None
    return float(stats.f_oneway(*groups).pvalue)
