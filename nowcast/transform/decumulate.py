"""Task 2.1: from cumulative-from-January values to discrete months or quarters.

Three kinds of cumulative series reach the platform (forms in config/series_catalogue.yaml):

``cumulative_ytd``  sums from January (or Q1): output, trade, exports, new firms, income.
    The first period of a year is kept as published; every later period is this period's
    cumulative value minus the previous one. A missing period leaves the next one missing too;
    nothing is guessed. A cumulative value that falls within the year (Uzstat revises the
    current month without carrying the revision back) gives a negative discrete value, which is
    kept here and flagged; whatever uses it as a weight treats it as unusable.

``average_ytd``  averages from January (average wages): discrete_k = k A_k - (k-1) A_(k-1).

``growth_yoy_cum``  real growth of the cumulative period against the same period last year,
    Uzstat's form for GDP, industry, construction, trade, services and investment. The discrete
    growth of period k of year t uses last year's nominal values as weights, exactly as for GDP
    (nowcast/transform/target.py):

        R_k = G_k * C_k / 100          C_k = last year's cumulative nominal value to period k
        g_k = 100 * (R_k - R_(k-1)) / (C_k - C_(k-1)),      g_1 = G_1

    Re-cumulating g with the same weights gives back G exactly (``cumulative_growth``).

Where the weights come from is recorded for every period (``basis``):

* ``prev_year``          last year's nominal values of the series itself (the exact formula);
* ``same_year``          the series' own nominal values of the same year, for the first year of
                         the nominal series only (the formula needs only the shares of each
                         month in the year, so this year's shares stand in for last year's);
* ``fallback:<id>``      the same two steps with a related nominal series (e.g. total industry
                         for a sub-sector before its own nominal values are in the store);
* ``equal``              no nominal series at all: every period weighs the same.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def periods_per_year(idx: pd.PeriodIndex) -> int:
    return 12 if idx.freqstr.startswith("M") else 4


def position(idx: pd.PeriodIndex) -> np.ndarray:
    """Month (1-12) or quarter (1-4) of each period."""
    return np.asarray(idx.month if periods_per_year(idx) == 12 else idx.quarter)


def regular(s: pd.Series) -> pd.Series:
    """Reindex to every period between the first and last non-missing one."""
    s = s.dropna().sort_index()
    if s.empty:
        return s.astype(float)
    full = pd.period_range(s.index[0], s.index[-1], freq=s.index.freq)
    return s.reindex(full).astype(float)


def decumulate(cum: pd.Series) -> pd.Series:
    """cumulative_ytd -> discrete. January (Q1) kept as published; a gap leaves the next period missing."""
    cum = regular(cum)
    if cum.empty:
        return cum
    first = position(cum.index) == 1
    out = cum - cum.shift(1)
    out[first] = cum[first]
    return out.rename(cum.name)


def decumulate_average(avg: pd.Series) -> pd.Series:
    """average_ytd -> discrete: k A_k - (k-1) A_(k-1)."""
    avg = regular(avg)
    if avg.empty:
        return avg
    k = position(avg.index).astype(float)
    out = k * avg - (k - 1) * avg.shift(1)
    out[k == 1] = avg[k == 1]
    return out.rename(avg.name)


def cumulate(disc: pd.Series) -> pd.Series:
    """discrete -> cumulative_ytd (inverse of ``decumulate``)."""
    disc = regular(disc)
    years = disc.index.year
    return disc.groupby(years).cumsum(skipna=False).rename(disc.name)


def _weights_for_year(year: int, k_max: int, sources: list[tuple[str, pd.Series]],
                      per_year: int) -> tuple[np.ndarray, str]:
    """Cumulative weights C_1..C_kmax for one year and where they came from."""
    for name, cum in sources:
        if cum is None or cum.empty:
            continue
        for lag, basis in ((1, "prev_year"), (0, "same_year")):
            y = year - lag
            vals = []
            for k in range(1, k_max + 1):
                p = pd.Period(year=y, month=k, freq="M") if per_year == 12 else pd.Period(
                    year=y, quarter=k, freq="Q")
                vals.append(cum.get(p, np.nan))
            vals = np.array(vals, dtype=float)
            if np.isfinite(vals).all():
                if lag == 0 and cum.index.min().year < year:
                    continue  # same-year shares only for the first year of the nominal series
                return vals, basis if name == "own" else f"fallback:{name}"
    return np.arange(1, k_max + 1, dtype=float), "equal"


def discrete_growth(growth_cum: pd.Series, weights: list[tuple[str, pd.Series | None]]) -> pd.DataFrame:
    """growth_yoy_cum (100 = no change) -> discrete y/y growth of each period (same scale).

    ``weights``: cumulative nominal series in order of preference, as (name, series); the first
    is the series' own nominal values and is called ``own``. Returns a frame with columns
    ``g`` (discrete growth), ``basis`` and ``weight`` (last year's discrete nominal weight, or the
    stand-in) indexed like ``growth_cum``. A period whose weight is not positive gets no growth.
    """
    G = regular(growth_cum)
    per_year = periods_per_year(G.index)
    pos = position(G.index)
    out = pd.DataFrame({"g": np.nan, "basis": "", "weight": np.nan}, index=G.index)
    for year in sorted(set(G.index.year)):
        mask = G.index.year == year
        idx = G.index[mask]
        k_max = int(pos[mask].max())
        C, basis = _weights_for_year(year, k_max, weights, per_year)
        Gy = np.full(k_max, np.nan)
        Gy[pos[mask] - 1] = G[mask].to_numpy()
        R = Gy * C / 100.0
        w = np.diff(np.concatenate([[0.0], C]))
        g = np.full(k_max, np.nan)
        g[0] = Gy[0]
        with np.errstate(invalid="ignore", divide="ignore"):
            g[1:] = 100.0 * (R[1:] - R[:-1]) / w[1:]
        g[w <= 0] = np.nan
        for p in idx:
            k = (p.month if per_year == 12 else p.quarter) - 1
            out.loc[p, ["g", "basis", "weight"]] = [g[k], basis, w[k]]
    out["g"] = out["g"].astype(float)
    out["weight"] = out["weight"].astype(float)
    return out


def cumulative_growth(g: pd.Series, weight: pd.Series) -> pd.Series:
    """Inverse of ``discrete_growth``: weighted mean of discrete growth from January."""
    df = pd.DataFrame({"gw": g * weight, "w": weight.where(g.notna())})
    years = df.index.year
    num = df["gw"].groupby(years).cumsum(skipna=False)
    den = df["w"].groupby(years).cumsum(skipna=False)
    return num / den
