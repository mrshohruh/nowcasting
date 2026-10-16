"""Task 2.10: pieces of the transformation report (notebooks/transform_report.ipynb).

One figure per panel variable with four small charts, each on its own scale:
    1. the input as published (cumulative growth, cumulative or discrete value, m/m index, rate)
    2. the real level and its seasonally adjusted version
    3. y/y growth, as it enters the y/y panel
    4. SA m/m (q/q) growth, as it enters the SA panel, outliers marked
and the tables of choices (weights, base, deflation, adjustment) and flags.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from nowcast.transform.inputs import Inputs
from nowcast.transform.panels import Panels, load_spec
from nowcast.transform.seasonal import residual_seasonality, seasonal_strength

# reference palette (dataviz skill): slot 1 blue, slot 2 orange; red for flagged points; ink greys
BLUE, ORANGE, RED = "#2a78d6", "#eb6834", "#e34948"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e0"
SPEC = {v["id"]: v for v in load_spec()}


def _style(ax, title: str) -> None:
    ax.set_title(title, fontsize=9, color=INK, loc="left")
    ax.tick_params(labelsize=7, colors=INK2, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


def _ts(s: pd.Series | None) -> pd.Series:
    if s is None:
        return pd.Series(dtype=float)
    s = s.dropna()
    return pd.Series(s.to_numpy(dtype=float), index=s.index.to_timestamp())


def raw_input(inputs: Inputs, var: str) -> tuple[pd.Series, str]:
    v = SPEC[var]
    if v["recipe"] == "nag":
        return inputs.raw("gdp_nag", f"{v['item']}.G"), "published: cumulative growth, %"
    if v["recipe"] == "price_index":
        parts = [inputs.raw(s, i) for s, i in v["inputs"]]
        return pd.concat(parts).sort_index(), "published: m/m index"
    sid, item = v["input"][:2]
    form = inputs.form(sid)
    label = {"growth_yoy_cum": "cumulative growth, %", "cumulative_ytd": "cumulative value",
             "average_ytd": "average from January", "discrete": "value", "level": "level"}[form]
    return inputs.raw(sid, item), f"published: {label}"


def figure(panels: Panels, inputs: Inputs, var: str):
    yoy = panels.yoy[panels.yoy["variable"] == var].set_index("period")["value"]
    sa = panels.sa[panels.sa["variable"] == var].set_index("period")
    freq = SPEC[var]["freq"]

    def to_pi(ix):
        return pd.PeriodIndex(ix, freq=freq).to_timestamp()

    fig, axes = plt.subplots(1, 4, figsize=(13, 2.3), constrained_layout=True)
    raw, raw_title = raw_input(inputs, var)
    r = _ts(raw)
    axes[0].plot(r.index, r.values, color=INK2, linewidth=1.2)
    _style(axes[0], "1  " + raw_title)
    lv = panels.levels[var]
    level, sal = _ts(lv["level"]), _ts(lv["sa_level"])
    if len(level):
        axes[1].plot(level.index, level.values, color=BLUE, linewidth=1.2, label="level")
        if len(sal) and SPEC[var].get("sa", True) and SPEC[var]["recipe"] != "rate":
            axes[1].plot(sal.index, sal.values, color=ORANGE, linewidth=1.6, label="seasonally adjusted")
            axes[1].legend(fontsize=7, frameon=False, loc="upper left")
        _style(axes[1], "2  real level" if SPEC[var]["recipe"] not in ("rate",) else "2  level, % per year")
    else:
        axes[1].text(0.5, 0.5, "no level (y/y only)", ha="center", va="center", color=INK2, fontsize=8,
                     transform=axes[1].transAxes)
        _style(axes[1], "2  real level")
    if len(yoy):
        axes[2].plot(to_pi(yoy.index), yoy.values, color=BLUE, linewidth=1.2)
        axes[2].axhline(0, color=INK2, linewidth=0.6)
    unit = "pp" if SPEC[var]["recipe"] == "rate" else "%"
    _style(axes[2], f"3  y/y, {unit}")
    if len(sa):
        t = to_pi(sa.index)
        axes[3].plot(t, sa["value"].to_numpy(), color=BLUE, linewidth=1.2)
        out = sa["outlier"].to_numpy()
        if out.any():
            axes[3].scatter(t[out], sa["value"].to_numpy()[out], s=18, color=RED, zorder=3,
                            label="outlier (missing in model)")
            axes[3].legend(fontsize=7, frameon=False, loc="upper left")
        axes[3].axhline(0, color=INK2, linewidth=0.6)
        _style(axes[3], f"4  SA {'m/m' if freq == 'M' else 'q/q'}, {'pp' if unit == 'pp' else '100 x dlog'}")
    else:
        axes[3].text(0.5, 0.5, "not in the SA panel", ha="center", va="center", color=INK2, fontsize=8,
                     transform=axes[3].transAxes)
        _style(axes[3], "4  SA growth")
    for ax in axes:
        ax.xaxis.set_major_locator(plt.matplotlib.dates.YearLocator(base=2 if freq == "M" else 2))
        ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%Y"))
    return fig


def choices_table(panels: Panels) -> pd.DataFrame:
    rows = []
    for _, m in panels.meta.iterrows():
        var = m["variable"]
        lv = panels.levels[var]
        sa = panels.sa[panels.sa["variable"] == var]
        res_p = None
        if len(sa) and SPEC[var].get("sa", True) and SPEC[var]["recipe"] not in ("rate",):
            s = pd.Series(sa["value_clean"].to_numpy(), index=pd.PeriodIndex(sa["period"], freq=m["freq"]))
            res_p = residual_seasonality(s)
        strength = seasonal_strength(lv["level"]) if lv["level"] is not None else None
        how = m["weights"] if isinstance(m["weights"], str) and m["weights"] else m["steps"]
        rows.append({
            "variable": var, "freq": m["freq"], "block": m["block"], "status": m["status"],
            "recipe": m["recipe"], "real terms / weights": how if isinstance(how, str) else "",
            "level base": m["base"] if isinstance(m["base"], str) else "",
            "seasonal adjustment": m["sa"] if isinstance(m["sa"], str) else "",
            "seasonal strength": None if strength is None else round(strength, 2),
            "leftover seasonality p": None if res_p is None else round(res_p, 3),
            "y/y from": m.get("yoy_first"), "y/y to": m.get("yoy_last"),
            "SA outliers": None if pd.isna(m.get("sa_outliers")) else int(m["sa_outliers"]),
            "flags": int(m["n_flags"]),
        })
    return pd.DataFrame(rows)


def flags_table(panels: Panels) -> pd.DataFrame:
    f = panels.flags.copy()
    if f.empty:
        return f
    f["flag"] = f["flag"].str.replace(r"^weights: .*", lambda m: m.group(0), regex=True)
    return (f.groupby(["variable", "flag"])["period"]
            .agg(n="size", periods=lambda p: ", ".join(sorted(p)[:8]) + (" ..." if len(p) > 8 else ""))
            .reset_index())


def last_values(panels: Panels, n: int = 6) -> pd.DataFrame:
    """Last n y/y values of every monthly variable: the spot-check table."""
    y = panels.yoy[(panels.yoy["block"] != "dummy")]
    out = {}
    for var, d in y.groupby("variable", sort=False):
        d = d.sort_values("period").tail(n)
        out[var] = dict(zip(d["period"], np.round(d["value"], 2), strict=True))
    return pd.DataFrame(out).T
