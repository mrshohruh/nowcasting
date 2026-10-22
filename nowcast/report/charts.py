"""Plotly chart specifications for the nowcasting dashboard.

Each function returns a JSON-serialisable dict (a Plotly figure) that the
Jinja2 templates embed as ``Plotly.newPlot(div, fig.data, fig.layout, fig.config)``.

Brand palette
    Red        #e31e24
    Dark blue  #1a2744
    White      #ffffff
    Light grey #f5f6f8
    Med grey   #6b7280
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
RED = "#e31e24"
BLUE = "#1a2744"
LIGHT_BLUE = "#3b82f6"
TEAL = "#0d9488"
AMBER = "#d97706"
PURPLE = "#7c3aed"
EMERALD = "#059669"
SLATE = "#64748b"
GREY = "#6b7280"
WHITE = "#ffffff"
LIGHT_GREY = "#f5f6f8"

PALETTE = [BLUE, RED, TEAL, AMBER, PURPLE, EMERALD, SLATE, LIGHT_BLUE]

_FONT = dict(family="Inter, system-ui, sans-serif", color=BLUE)
_TRANSPARENT = "rgba(0,0,0,0)"

_BASE_LAYOUT = dict(
    paper_bgcolor=_TRANSPARENT,
    plot_bgcolor=_TRANSPARENT,
    font=_FONT,
    margin=dict(l=50, r=24, t=40, b=50),
    hovermode="x unified",
    xaxis=dict(showgrid=False, zeroline=False),
    yaxis=dict(
        showgrid=True,
        gridcolor="rgba(226,232,240,0.5)",
        zeroline=True,
        zerolinecolor="rgba(226,232,240,0.8)",
        zerolinewidth=1,
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="left",
        x=0,
        font=dict(size=11),
    ),
)

_CONFIG = dict(responsive=True, displayModeBar=False)


def _merge_layout(overrides: dict) -> dict:
    """Deep-ish merge of *overrides* onto _BASE_LAYOUT."""
    out = {}
    for k, v in _BASE_LAYOUT.items():
        if isinstance(v, dict) and k in overrides and isinstance(overrides[k], dict):
            out[k] = {**v, **overrides[k]}
        else:
            out[k] = v
    for k, v in overrides.items():
        if k not in out:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# 1.  Headline chart — GDP y/y nowcast with 80 % band
# ---------------------------------------------------------------------------

def headline_chart(nowcast_history: list[dict], bands: dict | None = None) -> dict:
    """GDP y/y nowcast timeline with optional 80 % uncertainty band.

    Parameters
    ----------
    nowcast_history : list of dict
        ``[{run_date, target_quarter, gdp_yoy, band_lo, band_hi, official}, ...]``
        sorted chronologically.  ``official`` (optional, bool) flags GDP actuals.
    bands : dict, optional
        ``{band_lo, band_hi}`` for the *latest* nowcast if not in ``nowcast_history``.
    """
    dates = [h["run_date"] for h in nowcast_history]
    vals = [h["gdp_yoy"] for h in nowcast_history]

    data: list[dict] = []

    # Band fill (lo → hi)
    lo = [h.get("band_lo") for h in nowcast_history]
    hi = [h.get("band_hi") for h in nowcast_history]
    has_band = any(v is not None for v in lo)
    if has_band:
        # Replace None with the central value so the band doesn't break
        lo_clean = [l if l is not None else v for l, v in zip(lo, vals)]
        hi_clean = [h if h is not None else v for h, v in zip(hi, vals)]
        data.append(dict(
            x=dates + dates[::-1],
            y=hi_clean + lo_clean[::-1],
            fill="toself",
            fillcolor="rgba(227,30,36,0.10)",
            line=dict(color="rgba(0,0,0,0)"),
            hoverinfo="skip",
            showlegend=True,
            name="80 % band",
            type="scatter",
            mode="lines",
        ))

    # Central nowcast line
    data.append(dict(
        x=dates,
        y=vals,
        mode="lines+markers",
        name="Nowcast",
        line=dict(color=RED, width=2.5),
        marker=dict(size=5, color=RED),
        type="scatter",
        hovertemplate="%{y:.1f}%<extra>Nowcast</extra>",
    ))

    # Official GDP dots (if any)
    off_dates = [h["run_date"] for h in nowcast_history if h.get("official")]
    off_vals = [h["gdp_yoy"] for h in nowcast_history if h.get("official")]
    if off_dates:
        data.append(dict(
            x=off_dates,
            y=off_vals,
            mode="markers",
            name="Official GDP",
            marker=dict(size=9, color=BLUE, symbol="diamond"),
            type="scatter",
            hovertemplate="%{y:.1f}%<extra>Official</extra>",
        ))

    # Latest marker highlight
    if vals:
        data.append(dict(
            x=[dates[-1]],
            y=[vals[-1]],
            mode="markers+text",
            marker=dict(size=12, color=RED, line=dict(color=WHITE, width=2)),
            text=[f"{vals[-1]:.1f}%"],
            textposition="top center",
            textfont=dict(size=13, color=RED, family="Inter, system-ui, sans-serif"),
            showlegend=False,
            type="scatter",
            hoverinfo="skip",
        ))

    layout = _merge_layout(dict(
        title=dict(text="GDP Growth Nowcast (y/y %)", font=dict(size=15)),
        yaxis=dict(title="y/y %", ticksuffix="%"),
    ))
    return dict(data=data, layout=layout, config=_CONFIG)


def nowcast_vs_actual_chart(
    history: list[dict],
    current: dict | None = None,
    model: str = "dfm",
) -> dict:
    """Past nowcasts against official GDP, one point per target quarter.

    Parameters
    ----------
    history : list of dict
        Backtest rows ``[{model, horizon, target_quarter, forecast, actual}, ...]``.
        ``actual`` is None while the quarter is still open.
    current : dict, optional
        The live nowcast ``{quarter, value, band_lo, band_hi}``, drawn with its band.
    model : str
        Which model's past nowcasts to draw (default: the headline DFM).
    """
    horizons = {"nowcast_m1": "Month 1", "nowcast_m2": "Month 2", "nowcast_m3": "Month 3"}
    by_q: dict[str, dict] = {}
    for r in history:
        if r.get("model") != model or r.get("horizon") not in horizons:
            continue
        slot = by_q.setdefault(r["target_quarter"], {})
        slot[r["horizon"]] = r["forecast"]
        if r.get("actual") is not None:
            slot["actual"] = r["actual"]

    quarters = set(by_q)
    if current:
        quarters.add(current["quarter"])
    quarters = sorted(quarters)

    def series(key: str) -> list:
        return [by_q.get(q, {}).get(key) for q in quarters]

    data: list[dict] = [
        dict(
            x=quarters, y=series("actual"), mode="lines+markers", name="Official GDP",
            line=dict(color=BLUE, width=2.5),
            marker=dict(size=8, color=BLUE, symbol="diamond"),
            type="scatter", hovertemplate="%{y:.1f}%<extra>Official GDP</extra>",
        ),
        dict(
            x=quarters, y=series("nowcast_m3"), mode="lines+markers",
            name="Nowcast (month 3, end of quarter)",
            line=dict(color=RED, width=2.5), marker=dict(size=6, color=RED),
            type="scatter", hovertemplate="%{y:.1f}%<extra>Nowcast, month 3</extra>",
        ),
    ]
    # Earlier-in-the-quarter nowcasts: available from the legend
    for key, label, dash in (("nowcast_m2", "Nowcast (month 2)", "dot"),
                             ("nowcast_m1", "Nowcast (month 1)", "dot")):
        data.append(dict(
            x=quarters, y=series(key), mode="lines+markers", name=label, visible="legendonly",
            line=dict(color=RED, width=1.5, dash=dash), marker=dict(size=4, color=RED),
            opacity=0.6, type="scatter",
            hovertemplate="%{y:.1f}%<extra>" + label + "</extra>",
        ))

    if current:
        v = current["value"]
        lo, hi = current.get("band_lo"), current.get("band_hi")
        trace = dict(
            x=[current["quarter"]], y=[v], mode="markers+text",
            name=f"Current nowcast {current['quarter']}",
            marker=dict(size=13, color=RED, line=dict(color=WHITE, width=2)),
            text=[f"  {v:.1f}%"], textposition="middle right",
            textfont=dict(size=13, color=RED, family="Inter, system-ui, sans-serif"),
            type="scatter", hovertemplate="%{y:.1f}%<extra>Current nowcast</extra>",
        )
        if lo is not None and hi is not None:
            trace["error_y"] = dict(
                type="data", symmetric=False, array=[hi - v], arrayminus=[v - lo],
                color=RED, thickness=1.5, width=6,
            )
        data.append(trace)

    # No in-chart title: the card heading already names the chart, and a title collides with the legend
    layout = _merge_layout(dict(
        xaxis=dict(type="category", title="Target quarter"),
        yaxis=dict(title="y/y %", ticksuffix="%"),
    ))
    return dict(data=data, layout=layout, config=_CONFIG)


# ---------------------------------------------------------------------------
# 2.  Nowcast path — one line per target quarter
# ---------------------------------------------------------------------------

def nowcast_path_chart(paths: dict[str, list[dict]]) -> dict:
    """One coloured line per target quarter showing the nowcast evolution.

    Parameters
    ----------
    paths : dict
        ``{target_quarter: [{run_date, gdp_yoy, official?}, ...], ...}``
    """
    data: list[dict] = []
    for i, (qtr, pts) in enumerate(sorted(paths.items())):
        colour = PALETTE[i % len(PALETTE)]
        dates = [p["run_date"] for p in pts]
        vals = [p["gdp_yoy"] for p in pts]
        data.append(dict(
            x=dates,
            y=vals,
            mode="lines+markers",
            name=qtr,
            line=dict(color=colour, width=2),
            marker=dict(size=4, color=colour),
            type="scatter",
            hovertemplate=f"{qtr}: " + "%{y:.1f}%<extra></extra>",
        ))
        # Official end-dot
        off = [p for p in pts if p.get("official")]
        if off:
            data.append(dict(
                x=[off[-1]["run_date"]],
                y=[off[-1]["gdp_yoy"]],
                mode="markers",
                marker=dict(size=10, color=colour, symbol="diamond",
                            line=dict(color=WHITE, width=2)),
                showlegend=False,
                type="scatter",
                hovertemplate=f"{qtr} official: " + "%{y:.1f}%<extra></extra>",
            ))

    layout = _merge_layout(dict(
        title=dict(text="Nowcast Evolution by Quarter", font=dict(size=15)),
        yaxis=dict(title="GDP y/y %", ticksuffix="%"),
        xaxis=dict(title="Run date"),
    ))
    return dict(data=data, layout=layout, config=_CONFIG)


# ---------------------------------------------------------------------------
# 3.  Waterfall — revision decomposition
# ---------------------------------------------------------------------------

def waterfall_chart(decomposition: dict) -> dict:
    """Classic waterfall: old → +revision → +news → +reestimation → new.

    Parameters
    ----------
    decomposition : dict
        Keys: ``old_nowcast``, ``revision_effect``, ``news_effect``,
        ``reestimation_effect``, ``new_nowcast``.
    """
    old = decomposition.get("old_nowcast", 0)
    rev = decomposition.get("revision_effect", 0)
    news = decomposition.get("news_effect", 0)
    reest = decomposition.get("reestimation_effect", 0)
    new = decomposition.get("new_nowcast", old + rev + news + reest)

    labels = ["Previous", "Data revisions", "New data", "Re-estimation", "Current"]
    measures = ["absolute", "relative", "relative", "relative", "total"]
    values = [old, rev, news, reest, new]

    colours = []
    for m, v in zip(measures, values):
        if m == "absolute" or m == "total":
            colours.append(BLUE)
        elif v >= 0:
            colours.append("#22763d")  # positive green
        else:
            colours.append(RED)

    data = [dict(
        type="waterfall",
        orientation="v",
        measure=measures,
        x=labels,
        y=values,
        textposition="outside",
        text=[f"{v:+.2f}" if m == "relative" else f"{v:.1f}" for m, v in zip(measures, values)],
        textfont=dict(size=12),
        connector=dict(line=dict(color="rgba(107,114,128,0.4)", width=1, dash="dot")),
        decreasing=dict(marker=dict(color=RED)),
        increasing=dict(marker=dict(color="#22763d")),
        totals=dict(marker=dict(color=BLUE)),
    )]

    layout = _merge_layout(dict(
        title=dict(text="Nowcast Revision Decomposition", font=dict(size=15)),
        yaxis=dict(title="GDP y/y %", ticksuffix="%"),
        showlegend=False,
    ))
    return dict(data=data, layout=layout, config=_CONFIG)


# ---------------------------------------------------------------------------
# 4.  Sector contributions — horizontal bars
# ---------------------------------------------------------------------------

def sector_chart(sectors: list[dict]) -> dict:
    """Horizontal bar chart of sector contributions to GDP y/y.

    Parameters
    ----------
    sectors : list of dict
        ``[{sector, label, weight, growth_yoy, contribution_pp}, ...]``
        sorted by absolute contribution (largest first).
    """
    # Reverse so largest appears at top of horizontal chart
    ordered = list(reversed(sectors))
    labels = [s["label"] for s in ordered]
    contribs = [s["contribution_pp"] for s in ordered]
    colours = ["#22763d" if c >= 0 else RED for c in contribs]

    data = [dict(
        type="bar",
        orientation="h",
        x=contribs,
        y=labels,
        marker=dict(color=colours),
        text=[f"{c:+.2f} pp" for c in contribs],
        textposition="outside",
        textfont=dict(size=11),
        hovertemplate="%{y}: %{x:+.2f} pp<extra></extra>",
    )]

    layout = _merge_layout(dict(
        title=dict(text="Sector Contributions to GDP (pp)", font=dict(size=15)),
        xaxis=dict(
            title="Contribution (pp)",
            zeroline=True,
            zerolinecolor=GREY,
            zerolinewidth=1,
            showgrid=True,
            gridcolor="rgba(226,232,240,0.5)",
        ),
        yaxis=dict(showgrid=False, zeroline=False, automargin=True),
        showlegend=False,
        margin=dict(l=160, r=60, t=40, b=50),
        height=max(300, 35 * len(sectors) + 100),
    ))
    return dict(data=data, layout=layout, config=_CONFIG)


# ---------------------------------------------------------------------------
# 5.  Data monitor — HTML table, no chart (returns None)
# ---------------------------------------------------------------------------

def data_monitor_chart(monitor_data: list[dict]) -> dict | None:
    """The data monitor is rendered as an HTML table, not a Plotly chart.

    Returns *None*; the template handles the table directly from ``monitor_data``.
    """
    return None


# ---------------------------------------------------------------------------
# 6.  Model comparison — grouped bars by horizon
# ---------------------------------------------------------------------------

def model_comparison_chart(backtest: list[dict]) -> dict:
    """Grouped bar chart comparing model RMSE across horizons.

    Parameters
    ----------
    backtest : list of dict
        ``[{model, horizon, rmse, ratio_vs_ar1}, ...]``
    """
    # Group by model
    models: dict[str, dict[str, float]] = {}
    horizons_seen: list[str] = []
    for b in backtest:
        m = b["model"]
        h = b["horizon"]
        if h not in horizons_seen:
            horizons_seen.append(h)
        models.setdefault(m, {})[h] = b["rmse"]

    model_colours = {
        "DFM": RED,
        "Bridge": BLUE,
        "MIDAS": TEAL,
        "AR(1)": GREY,
    }

    data: list[dict] = []
    for m_name, h_vals in models.items():
        data.append(dict(
            type="bar",
            name=m_name,
            x=horizons_seen,
            y=[h_vals.get(h, 0) for h in horizons_seen],
            marker=dict(color=model_colours.get(m_name, SLATE)),
            text=[f"{h_vals.get(h, 0):.3f}" for h in horizons_seen],
            textposition="outside",
            textfont=dict(size=10),
            hovertemplate=f"{m_name}" + " — %{x}: RMSE %{y:.3f}<extra></extra>",
        ))

    layout = _merge_layout(dict(
        title=dict(text="Model Backtest — RMSE by Horizon", font=dict(size=15)),
        barmode="group",
        xaxis=dict(title="Horizon"),
        yaxis=dict(title="RMSE (pp)", rangemode="tozero"),
    ))
    return dict(data=data, layout=layout, config=_CONFIG)
