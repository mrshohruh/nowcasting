"""Sector-level GVA contributions from the bridge equations (Model B).

The DFM gives the headline nowcast; the bridges give a sector breakdown.
This module runs the bridge equations and computes each sector's weighted
contribution to the GDP nowcast, expressed in percentage points of GDP y/y.

    contribution_i = weight_i * sector_growth_i

These contributions sum to the bridge-equation GDP nowcast (which may differ
slightly from the DFM headline). The narrative reports the sector view as
context alongside the DFM-driven news decomposition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from nowcast.models.bridges import DEFAULT_GVA_WEIGHTS, BridgeEquations
from nowcast.transform.panels import Panels

logger = logging.getLogger(__name__)

# Human-readable sector names
SECTOR_LABELS: dict[str, str] = {
    "gva_agriculture": "Agriculture",
    "gva_mining": "Mining",
    "gva_manufacturing": "Manufacturing",
    "gva_electricity": "Electricity",
    "gva_water": "Water supply",
    "gva_construction": "Construction",
    "gva_trade_accommodation": "Trade & accommodation",
    "gva_transport": "Transport",
    "gva_ict": "ICT",
    "gva_other_services": "Other services",
    "net_taxes": "Net taxes on products",
}

SECTOR_LABELS_UZ: dict[str, str] = {
    "gva_agriculture": "Qishloq xo'jaligi",
    "gva_mining": "Tog'-kon sanoati",
    "gva_manufacturing": "Qayta ishlash sanoati",
    "gva_electricity": "Elektr energiyasi",
    "gva_water": "Suv ta'minoti",
    "gva_construction": "Qurilish",
    "gva_trade_accommodation": "Savdo va turar joy",
    "gva_transport": "Transport",
    "gva_ict": "AKT",
    "gva_other_services": "Boshqa xizmatlar",
    "net_taxes": "Mahsulotlarga sof soliqlar",
}


@dataclass
class SectorContribution:
    """One GVA sector's contribution to the GDP nowcast."""

    sector: str
    label: str
    weight: float  # share of GDP (0–1)
    growth_yoy: float  # sector y/y growth (%)
    contribution_pp: float  # weight * growth (pp of GDP y/y)
    method: str  # "ridge", "AR", "AR_fallback"
    n_obs: int = 0
    r2: float = 0.0


def sector_contributions(
    panels: Panels,
    target_quarter: pd.Period,
    bridge_map: dict | None = None,
    gva_weights: dict[str, float] | None = None,
) -> list[SectorContribution]:
    """Compute sector-level contributions to GDP y/y.

    Parameters
    ----------
    panels : Panels
        Current model-ready panels.
    target_quarter : pd.Period
        Quarter to nowcast.
    bridge_map : dict, optional
        Sector-to-indicator mapping (from config).
    gva_weights : dict, optional
        Sector GDP shares.

    Returns
    -------
    List of SectorContribution, sorted by absolute contribution (largest first).
    """
    model = BridgeEquations(bridge_map=bridge_map, gva_weights=gva_weights)
    result = model.predict(panels, target_quarter)

    weights = gva_weights or DEFAULT_GVA_WEIGHTS
    sectors_info = result.get("info", {}).get("sectors", {})

    contributions = []
    for sector, info in sectors_info.items():
        growth = info.get("prediction", np.nan)
        if np.isnan(growth):
            continue

        w = weights.get(sector, 0.0)
        contribution = w * growth

        contributions.append(SectorContribution(
            sector=sector,
            label=SECTOR_LABELS.get(sector, sector),
            weight=w,
            growth_yoy=growth,
            contribution_pp=contribution,
            method=info.get("method", "unknown"),
            n_obs=info.get("n_obs", 0),
            r2=info.get("r2", 0.0),
        ))

    # Sort by absolute contribution, largest first
    contributions.sort(key=lambda x: abs(x.contribution_pp), reverse=True)
    return contributions


def format_sector_table(contributions: list[SectorContribution]) -> pd.DataFrame:
    """Format sector contributions as a tidy DataFrame for display."""
    rows = []
    total_growth = 0.0
    total_weight = 0.0

    for c in contributions:
        rows.append({
            "Sector": c.label,
            "Weight": f"{c.weight:.0%}",
            "Growth (y/y %)": f"{c.growth_yoy:.1f}",
            "Contribution (pp)": f"{c.contribution_pp:.2f}",
            "Method": c.method,
        })
        total_growth += c.contribution_pp
        total_weight += c.weight

    rows.append({
        "Sector": "GDP (bridge sum)",
        "Weight": f"{total_weight:.0%}",
        "Growth (y/y %)": "",
        "Contribution (pp)": f"{total_growth:.2f}",
        "Method": "",
    })

    return pd.DataFrame(rows)
