"""What the DFM is built on: its input variables and how tightly each is tied to the factors.

The DFM has no sector breakdown. It compresses its input variables into a few common factors and
reads GDP off them. This module lists those inputs, so a reader can see what the headline rests on.

For each variable we report:
- the latest observation and its y/y growth (from the y/y panel),
- ``r2_f1`` / ``r2_f2``: R-squared of the variable on each factor taken alone (statsmodels'
  ``coefficients_of_determination``). A high value means the variable moves with that factor, so it
  carries information about it. The two values are NOT additive and are not a joint R-squared.
"""

from __future__ import annotations

import logging

import pandas as pd

from nowcast.explain.news import VARIABLE_LABELS
from nowcast.transform.panels import Panels

logger = logging.getLogger(__name__)

BLOCK_LABELS: dict[str, str] = {
    "real": "Real activity",
    "services": "Services",
    "external": "External sector",
    "prices": "Prices",
    "prices_finance": "Prices & finance",
    "finance": "Finance",
    "quarterly": "Income, wages & investment",
    "firms": "Firms",
    "target": "GDP & output",
}

# Variables whose block in the panel metadata is not informative enough for a reader
_BLOCK_OVERRIDE: dict[str, str] = {"gdp": "target", "gva": "target"}


def dfm_inputs(
    result,
    panels: Panels,
    variable_ids: list[str] | None,
    n_factors: int = 2,
) -> list[dict]:
    """One row per DFM input variable, ordered by how strongly it is tied to the factors.

    ``result`` is the fitted statsmodels results object (``DFM._last_result``).
    """
    try:
        r2 = result.coefficients_of_determination
    except Exception as exc:  # pragma: no cover - defensive, library-version dependent
        logger.warning("Could not read factor R-squared: %s", exc)
        return []

    meta = panels.meta.set_index("variable") if not panels.meta.empty else pd.DataFrame()
    yoy = panels.yoy
    ids = list(variable_ids) if variable_ids else list(r2.index)

    rows: list[dict] = []
    for var in r2.index:
        if var not in ids:
            continue
        sub = yoy[yoy["variable"] == var].dropna(subset=["value"]).sort_values("period")
        last = sub.iloc[-1] if not sub.empty else None
        freq = str(meta.loc[var, "freq"]) if var in meta.index else ""
        block = _BLOCK_OVERRIDE.get(var) or (
            str(meta.loc[var, "block"]) if var in meta.index else ""
        )
        # Factor columns are named '0', '1', ... (strings): read them by position
        vals = [float(v) for v in r2.loc[var].iloc[:n_factors]]
        rows.append({
            "variable": var,
            "label": VARIABLE_LABELS.get(var, var),
            "block": BLOCK_LABELS.get(block, block.replace("_", " ").title() or "Other"),
            "freq": "Quarterly" if freq == "Q" else "Monthly",
            "last_period": str(last["period"]) if last is not None else "",
            "last_yoy": float(last["value"]) if last is not None else None,
            "r2_f1": vals[0] if len(vals) > 0 else 0.0,
            "r2_f2": vals[1] if len(vals) > 1 else 0.0,
        })

    rows.sort(key=lambda r: max(r["r2_f1"], r["r2_f2"]), reverse=True)
    return rows
