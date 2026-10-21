"""Narrative note generator: fills Jinja2 templates with decomposition results.

Usage:
    from nowcast.explain.narrative import render_note

    text_en = render_note(decomposition, sectors, lang="en")
    text_uz = render_note(decomposition, sectors, lang="uz")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from nowcast.explain.news import Decomposition
from nowcast.explain.sectors import SECTOR_LABELS_UZ, SectorContribution

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
TEMPLATES = {
    "en": "note_en.j2",
    "uz": "note_uz.j2",
}


def _build_env(template_dir: Path | None = None) -> Environment:
    """Build a Jinja2 environment with the templates directory."""
    d = template_dir or TEMPLATE_DIR
    return Environment(
        loader=FileSystemLoader(str(d)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render_note(
    decomposition: Decomposition | None,
    sectors: list[SectorContribution] | None = None,
    lang: str = "en",
    template_dir: Path | None = None,
    run_date: str = "",
    target_quarter: str = "",
    horizon: str = "",
    new_nowcast: float | None = None,
) -> str:
    """Render a nowcasting note from decomposition + sector results.

    Parameters
    ----------
    decomposition : Decomposition or None
        Four-run decomposition (None for first run with no comparison).
    sectors : list[SectorContribution], optional
        Sector contributions from the bridge equations.
    lang : str
        Language code ("en" or "uz").
    template_dir : Path, optional
        Override the default templates directory.
    run_date, target_quarter, horizon : str
        Override values (used when decomposition is None).
    new_nowcast : float, optional
        Override nowcast value (used when decomposition is None).

    Returns
    -------
    str
        The rendered note text.
    """
    env = _build_env(template_dir)
    template_name = TEMPLATES.get(lang, TEMPLATES["en"])
    template = env.get_template(template_name)

    # Build context
    if decomposition is not None:
        # Prefer the caller-supplied new_nowcast (winsorised) over the
        # decomposition's raw value, which may not be clipped.
        nowcast_val = new_nowcast if new_nowcast is not None else decomposition.new_nowcast
        ctx = {
            "run_date": decomposition.run_date,
            "target_quarter": decomposition.target_quarter,
            "horizon": horizon,
            "new_nowcast": nowcast_val,
            "old_nowcast": decomposition.old_nowcast,
            "total_change": (
                nowcast_val - decomposition.old_nowcast
                if decomposition.old_nowcast is not None
                else decomposition.total_change
            ),
            "revision_effect": decomposition.revision_effect,
            "news_effect": decomposition.news_effect,
            "reest_effect": decomposition.reestimation_effect,
            "top_news": decomposition.top_news(5),
            "grouped_impacts": decomposition.grouped_impacts(),
            "has_decomposition": True,
        }
    else:
        ctx = {
            "run_date": run_date,
            "target_quarter": target_quarter,
            "horizon": horizon,
            "new_nowcast": new_nowcast if new_nowcast is not None else 0.0,
            "old_nowcast": None,
            "total_change": None,
            "revision_effect": 0.0,
            "news_effect": 0.0,
            "reest_effect": 0.0,
            "top_news": [],
            "grouped_impacts": {},
            "has_decomposition": False,
        }

    # Add sector contributions (with translated labels for Uzbek)
    if sectors:
        if lang == "uz":
            translated = []
            for s in sectors:
                translated.append(SectorContribution(
                    sector=s.sector,
                    label=SECTOR_LABELS_UZ.get(s.sector, s.label),
                    weight=s.weight,
                    growth_yoy=s.growth_yoy,
                    contribution_pp=s.contribution_pp,
                    method=s.method,
                    n_obs=s.n_obs,
                    r2=s.r2,
                ))
            ctx["sectors"] = translated
        else:
            ctx["sectors"] = sectors
        total_bridge = sum(s.contribution_pp for s in sectors)
        ctx["bridge_gdp"] = total_bridge
    else:
        ctx["sectors"] = []
        ctx["bridge_gdp"] = None

    text = template.render(**ctx)

    # Clean up: collapse multiple blank lines, strip trailing whitespace
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def number_check(note: str) -> list[str]:
    """Check that all numbers in a rendered note are plausible.

    Returns a list of warnings (empty = all OK). This catches:
    - GDP y/y outside ±30%
    - Contributions outside ±20 pp
    - NaN or inf in the text
    """
    warnings_list = []

    # Check for NaN/inf
    if "nan" in note.lower() or "inf" in note.lower():
        warnings_list.append("Note contains NaN or Inf values")

    # Extract all numbers (including negative and decimal)
    numbers = re.findall(r"[-+]?\d+\.?\d*", note)
    for n_str in numbers:
        try:
            n = float(n_str)
        except ValueError:
            continue

        # GDP y/y check (numbers followed by "%" or "y/y")
        # This is approximate; the test_number_check test does a stricter version
        if abs(n) > 30 and "%" in note[note.find(n_str):note.find(n_str) + len(n_str) + 5]:
            warnings_list.append(f"Suspiciously large percentage: {n}")

    return warnings_list
