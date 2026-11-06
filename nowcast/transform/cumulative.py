"""Year-to-date GDP growth, comparable with what Uzstat publishes.

Uzstat publishes real GDP growth only cumulatively from January (Q1, Jan-Jun, Jan-Sep, Jan-Dec):

    G_k,t = R_cum_k,t / N_cum_k,t-1     (real output so far this year in last year's prices,
                                         over last year's nominal output for the same months)

The models nowcast one quarter at a time. For a target quarter q of year t whose earlier quarters
1..k are already published, the year-to-date figure is

    YTD_q,t = [ W_k (1 + G_k,t) + sum_{j=k+1..q} w_j (1 + g_j,t) ] / [ W_k + sum_j w_j ] - 1

* G_k,t  Uzstat's published cumulative growth for the quarters already out,
* g_j,t  the model's growth for each quarter not yet published (the target, and at month 1 also
         the quarter just ended, which Uzstat has not released yet: a backcast),
* W_k    last year's nominal value of the published months, as Uzstat used it: R_cum_k,t / G_k,t,
* w_j    last year's nominal value of each unpublished quarter (NAG current prices).

This is the exact inverse of the decumulation in ``target.py`` (``cumulative_from_discrete``), so
a quarter derived from Uzstat's figures goes back to Uzstat's cumulative rate. The result is
linear in each g_j: a change of 1 pp in the target quarter moves the year-to-date figure by
``target_share`` pp (w_q over the total), which scales the news decomposition and the band.

Weight guard. The weights come from last year's quarterly levels, and Uzstat's file can mix price
bases between quarters (the 2026-11-03 workbook: Jan-Sep 2025 on the revised basis, Jan-Dec 2025
not, which makes 2025Q4 look 22% smaller than 2024Q4 in current prices). Each quarter's size
relative to the months before it is very stable (Q4 is 42-50% of Jan-Sep in 2018-2024, and no
consistent year is more than 12% from the median of the three years before it), so a ratio more
than 20% away from that median is replaced by the median and the choice recorded in
``weight_basis``. The broken cases are far outside: Q4 2025 in the 2026-11-05 workbook (-50%),
Q3 2025 in the 2026-10-14 one (-26%, a fall in current prices). Published figures are never
changed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# A last-year quarter whose size relative to the months before it differs from the median of the
# three years before by more than this (relative) is treated as a broken level.
RATIO_TOL = 0.20
SPAN = {1: "Jan-Mar", 2: "Jan-Jun", 3: "Jan-Sep", 4: "Jan-Dec"}
SPAN_UZ = {1: "yanvar-mart", 2: "yanvar-iyun", 3: "yanvar-sentyabr", 4: "yanvar-dekabr"}
SPAN_LONG = {1: "January-March", 2: "January-June", 3: "January-September", 4: "January-December"}


@dataclass
class Published:
    """Uzstat's cumulative GDP figures as known on a date, indexed by 'YYYYQn'.

    N current prices, R previous-year prices (both cumulative, billion UZS), G cumulative growth
    in percent (8.3 = 8.3%, not Uzstat's 108.3)."""

    N: pd.Series
    R: pd.Series
    G: pd.Series

    def growth(self, quarter: str) -> float:
        return float(self.G.get(quarter, np.nan))


def published_from_long(d: pd.DataFrame) -> Published:
    """From store rows of series ``gdp_nag`` (columns item, ref_period, value)."""
    def item(name: str) -> pd.Series:
        x = d[d["item"] == name]
        return pd.Series(x["value"].to_numpy(dtype=float), index=x["ref_period"].astype(str)).sort_index()
    g = item("gdp.G")
    return Published(N=item("gdp.N"), R=item("gdp.R"), G=g - 100.0)


def published_as_of(store, as_of: dt.date | str) -> Published:
    """Uzstat's figures as they stood on *as_of*, under the same release rule as the panels
    (real vintages from the first vintage on, the release-lag cut before it)."""
    from nowcast.catalogue import load as load_catalogue
    from nowcast.transform.inputs import load_inputs

    cat = [s for s in load_catalogue() if s["id"] == "gdp_nag"]
    inputs = load_inputs(store, as_of, catalogue=cat)
    return published_from_long(inputs.data[inputs.data["series_id"] == "gdp_nag"])


def _q(year: int, q: int) -> str:
    return f"{year}Q{q}"


def _ratio(N: pd.Series, year: int, j: int) -> float:
    """Quarter j of *year* in current prices relative to quarters 1..j-1 of the same year."""
    a, b = N.get(_q(year, j), np.nan), N.get(_q(year, j - 1), np.nan)
    return float((a - b) / b) if np.isfinite(a) and np.isfinite(b) and b > 0 else np.nan


def last_year_chain(pub: Published, year: int) -> tuple[dict[int, float], dict[int, str]]:
    """Last year's cumulative nominal GDP C_1..C_4 for weighting *year*, with the guard applied.

    Returns ({j: C_j}, {j: basis}); basis is 'levels' or 'median share <years>'."""
    N, prev = pub.N, year - 1
    c1 = N.get(_q(prev, 1), np.nan)
    chain: dict[int, float] = {1: float(c1)} if np.isfinite(c1) else {}
    basis: dict[int, str] = {1: "levels"} if chain else {}
    for j in range(2, 5):
        if j - 1 not in chain:
            break
        rho = _ratio(N, prev, j)
        refs = [r for y in range(prev - 1, prev - 4, -1) if np.isfinite(r := _ratio(N, y, j))]
        ref = float(np.median(refs)) if len(refs) >= 2 else np.nan
        if np.isfinite(rho) and (not np.isfinite(ref) or abs(rho / ref - 1) <= RATIO_TOL):
            chain[j], basis[j] = chain[j - 1] * (1 + rho), "levels"
        elif np.isfinite(ref):
            years = f"{prev - len(refs)}-{prev - 1}"
            chain[j], basis[j] = chain[j - 1] * (1 + ref), f"median share {years}"
        else:
            break
    return chain, basis


@dataclass
class YTD:
    """A year-to-date figure built from a quarter nowcast."""

    quarter: str  # target quarter, e.g. '2026Q4'
    value: float  # cumulative growth %, comparable with Uzstat (Jan-Dec 2026)
    quarter_value: float  # the target quarter's own growth %
    span: str  # 'Jan-Dec'
    known_through: str | None = None  # last quarter of the year Uzstat has published
    known_value: float | None = None  # its cumulative growth %
    estimated: list[str] = field(default_factory=list)  # quarters taken from the model
    estimates: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)  # last year's nominal, per quarter
    known_weight: float = 0.0  # W_k
    weight_basis: dict[str, str] = field(default_factory=dict)
    target_share: float = 1.0  # d value / d quarter_value
    unknown_share: float = 1.0  # weight of all model-estimated quarters in the total

    def with_target(self, g: float) -> float:
        """The year-to-date figure if the target quarter's growth were *g* (all else fixed)."""
        if not np.isfinite(self.value):
            return np.nan
        return float(self.value + self.target_share * (g - self.quarter_value))

    def to_dict(self) -> dict:
        return {
            "quarter": self.quarter, "value": self.value, "quarter_value": self.quarter_value,
            "span": self.span, "known_through": self.known_through, "known_value": self.known_value,
            "estimated": self.estimated, "estimates": self.estimates, "weights": self.weights,
            "known_weight": self.known_weight, "weight_basis": self.weight_basis,
            "target_share": self.target_share, "unknown_share": self.unknown_share,
        }


def to_ytd(target: str | pd.Period, estimates: dict[str, float], pub: Published) -> YTD:
    """Year-to-date growth for *target* from the model's quarter estimates and Uzstat's figures.

    ``estimates`` maps 'YYYYQn' to quarter growth in % and must cover the target and every
    earlier quarter of the year that Uzstat has not published yet; quarters Uzstat has published
    are taken from Uzstat whatever ``estimates`` says. Missing inputs give value NaN.
    """
    p = pd.Period(str(target), freq="Q")
    t, q = p.year, p.quarter
    tq = str(p)
    g_t = float(estimates.get(tq, np.nan))
    out = YTD(quarter=tq, value=np.nan, quarter_value=g_t, span=SPAN[q])

    if np.isfinite(pub.growth(tq)):  # already published: Uzstat's figure is the answer
        out.value, out.known_through, out.known_value = pub.growth(tq), tq, pub.growth(tq)
        out.target_share = out.unknown_share = 0.0
        return out

    k = max((j for j in range(1, q) if np.isfinite(pub.growth(_q(t, j)))), default=0)
    unknown = [_q(t, j) for j in range(k + 1, q + 1)]
    out.estimated = unknown
    out.estimates = {u: float(estimates.get(u, np.nan)) for u in unknown}
    if q == 1:  # Q1 alone: cumulative and quarter growth are the same number
        out.value = g_t
        return out
    if not all(np.isfinite(v) for v in out.estimates.values()):
        return out

    chain, basis = last_year_chain(pub, t)
    if any(j not in chain for j in range(1, q + 1)):
        return out
    disc = {j: chain[j] - chain.get(j - 1, 0.0) for j in range(1, q + 1)}
    W, gk = 0.0, 0.0
    if k >= 1:
        kq = _q(t, k)
        gk = pub.growth(kq)
        r = pub.R.get(kq, np.nan)
        # Last year's nominal value of the published months as Uzstat divided by: R / G
        W = float(r / (1 + gk / 100.0)) if np.isfinite(r) else chain[k]
        scale = W / chain[k]
        disc = {j: v * scale for j, v in disc.items()}
        out.known_through, out.known_value = kq, gk
    w = {_q(t, j): disc[j] for j in range(k + 1, q + 1)}
    total = W + sum(w.values())
    num = W * (1 + gk / 100.0) + sum(w[u] * (1 + out.estimates[u] / 100.0) for u in unknown)
    out.value = float(100.0 * (num / total - 1.0))
    out.weights = w
    out.known_weight = W
    out.weight_basis = {_q(t - 1, j): basis[j] for j in range(1, q + 1)}
    out.target_share = float(w[tq] / total)
    out.unknown_share = float(sum(w.values()) / total)
    return out


def ytd_for_results(results: pd.DataFrame, store, published=None) -> pd.DataFrame:
    """Add year-to-date columns to replay results (one row per run date, quarter, model).

    Each row's year-to-date figure uses Uzstat's figures as they stood on its run date and the
    same model's estimates from the same run date for any earlier quarter not yet published
    (its backcast rows). Adds gdp_ytd, target_share, unknown_share, known_through.
    ``published`` (run_date -> Published) is a cache: pass the same dict to later calls to read
    the store once per run date.
    """
    cache: dict = published if published is not None else {}
    out = results.copy()
    cols = {"gdp_ytd": [], "target_share": [], "unknown_share": [], "known_through": []}
    est = {k: dict(zip(g["target_quarter"], g["gdp_yoy"], strict=True))
           for k, g in out.groupby(["run_date", "model"])}
    for rd, tq, m in zip(out["run_date"], out["target_quarter"], out["model"], strict=True):
        if rd not in cache:
            cache[rd] = published_as_of(store, rd)
        r = to_ytd(tq, est[(rd, m)], cache[rd])
        cols["gdp_ytd"].append(r.value)
        cols["target_share"].append(r.target_share)
        cols["unknown_share"].append(r.unknown_share)
        cols["known_through"].append(r.known_through)
    for k, v in cols.items():
        out[k] = v
    return out
