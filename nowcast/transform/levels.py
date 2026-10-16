"""Tasks 2.2 and 2.4: price indices and real levels.

Price indices (2.2). The m/m CPI and PPI are chained into level indices with 2021 = 100 (the
average of the twelve months of 2021). The COICOP-2018 CPI starts in January 2021; the old-basket
CPI (SIAT 1285) runs to December 2020, so the two m/m series are joined end to start (no overlap,
no rescaling needed: both are % of the previous month). The plan named the NSDP CPI file for
2019-2020; the old SIAT table is the same official series and is already in the store.

Real levels from y/y growth (2.4). Seasonal adjustment and q/q growth need levels, but Uzstat
publishes real growth against the same period of last year. Levels are rebuilt by chaining each
month (quarter) to the same month a year earlier:

    L(t, k) = L(t-1, k) * g(t, k) / 100,    L(base year, k) = nominal value of the base year

so the level is in base-year prices (2018 for GDP) and turning it back into y/y growth gives g
exactly. A missing g would break the chain of that calendar month for every later year, so the
link across a missing value uses a log-linear interpolation of the neighbouring periods: the
missing period itself stays missing (it never enters a panel) and later years carry on. Such
links are reported as ``bridged``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nowcast.transform.decumulate import periods_per_year, position, regular

BASE_YEAR_PRICES = 2021


def chain_mm(*mm: pd.Series, base_year: int = BASE_YEAR_PRICES) -> pd.Series:
    """Join m/m indices (100 = no change) end to start and chain them into a level, base_year = 100."""
    parts = [regular(s) for s in mm if s is not None and not s.dropna().empty]
    joined = pd.concat(parts).sort_index()
    joined = joined[~joined.index.duplicated(keep="last")]
    joined = regular(joined)
    if joined.isna().any():
        gap = joined.index[joined.isna()][0]
        raise ValueError(f"m/m index has a gap at {gap}; a chained level cannot cross it")
    level = (joined / 100.0).cumprod()
    base = level[level.index.year == base_year]
    if len(base) < 12:  # base year not complete (e.g. an as-of date in 2021): rebase on what exists
        base = level.iloc[:12]
    return (100.0 * level / base.mean()).rename(None)


def to_quarterly_mean(monthly: pd.Series) -> pd.Series:
    """Average of the three months; a quarter with a missing month is missing."""
    m = regular(monthly)
    q = m.index.asfreq("Q")
    grp = m.groupby(q)
    out = grp.mean().where(grp.count() == 3)
    out.index = pd.PeriodIndex(out.index, freq="Q")
    return out


def deflate(nominal: pd.Series, price_level: pd.Series) -> pd.Series:
    """nominal / (price / 100). Quarterly nominal values are deflated by the quarterly mean price."""
    nominal = regular(nominal)
    p = price_level
    if periods_per_year(nominal.index) == 4 and periods_per_year(p.index) == 12:
        p = to_quarterly_mean(p)
    return nominal / (p.reindex(nominal.index) / 100.0)


def _interpolate_log(level: pd.Series) -> pd.Series:
    """Fill inside gaps of a positive level by log-linear interpolation (for links only)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        lg = np.log(level.where(level > 0))
    pos = pd.Series(np.arange(len(lg), dtype=float), index=lg.index)
    if lg.notna().sum() >= 2:
        filled = pd.Series(np.interp(pos, pos[lg.notna()], lg.dropna()), index=lg.index)
    else:
        filled = lg
    first, last = lg.first_valid_index(), lg.last_valid_index()
    inside = (lg.index >= first) & (lg.index <= last) if first is not None else np.zeros(len(lg), bool)
    return np.exp(filled.where(inside))


def levels_from_yoy(g: pd.Series, base: pd.Series) -> tuple[pd.Series, list[str]]:
    """Chain discrete y/y growth (100 = no change) into a level.

    ``base``: positive values of one full year (the base year) on the same frequency, e.g. that
    year's discrete nominal values. Years before the base year are chained backwards. Returns the
    level and the periods whose missing value was bridged by interpolation.
    """
    g = regular(g)
    base = base.dropna()
    s = periods_per_year(g.index)
    base_year = int(base.index.year[0])
    if len(base) != s or (base <= 0).any() or set(base.index.year) != {base_year}:
        raise ValueError(f"base must be {s} positive values of one year, got {len(base)}")
    years = range(min(int(g.index.year.min()), base_year), max(int(g.index.year.max()), base_year) + 1)
    full = pd.period_range(pd.Period(year=years[0], month=1, freq="M") if s == 12 else
                           pd.Period(year=years[0], quarter=1, freq="Q"),
                           periods=s * len(years), freq=g.index.freq)
    gf = g.reindex(full)
    L = pd.Series(np.nan, index=full)
    L[base.index] = base.to_numpy()
    bridged: list[str] = []
    link = L.copy()

    def year_slice(y: int) -> pd.PeriodIndex:
        return full[full.year == y]

    for y in [y for y in years if y > base_year]:
        cur, prev = year_slice(y), year_slice(y - 1)
        L[cur] = link[prev].to_numpy() * gf[cur].to_numpy() / 100.0
        link = _interpolate_log(L)
        bridged += [str(p) for p in prev if pd.isna(L[p]) and pd.notna(link[p]) and pd.notna(gf[p + s])]
    for y in [y for y in reversed(years) if y < base_year]:
        cur, nxt = year_slice(y), year_slice(y + 1)
        L[cur] = link[nxt].to_numpy() / (gf[nxt].to_numpy() / 100.0)
        link = _interpolate_log(L)
        bridged += [str(p) for p in nxt if pd.isna(L[p]) and pd.notna(link[p])]
    return regular(L), sorted(set(bridged))


def yoy_index(level: pd.Series) -> pd.Series:
    """Level -> growth against the same period last year, 100 = no change."""
    level = regular(level)
    return 100.0 * level / level.shift(periods_per_year(level.index))


def first_full_year(s: pd.Series) -> int | None:
    """First calendar year in which ``s`` has every period, all positive."""
    s = regular(s)
    if s.empty:
        return None
    n = periods_per_year(s.index)
    for y in sorted(set(s.index.year)):
        v = s[s.index.year == y]
        complete = len(v) == n and (position(v.index) == np.arange(1, n + 1)).all()
        if complete and v.notna().all() and (v > 0).all():
            return int(y)
    return None
