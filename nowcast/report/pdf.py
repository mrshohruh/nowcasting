"""One-page PDF nowcast note via WeasyPrint.

Usage:
    from nowcast.report.pdf import generate_pdf_note, NoteContext

    ctx = NoteContext(run_date="2026-10-01", target_quarter="2026Q3",
                      horizon="nowcast_m3", new_nowcast=6.2)
    generate_pdf_note(ctx, lang="en", out_path="note_en.pdf")
    generate_pdf_note(ctx, lang="uz", out_path="note_uz.pdf")

If WeasyPrint is not installed the function writes the rendered HTML instead
and logs a warning — the caller can still use the HTML as a preview.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
LOGO_PATH = Path(__file__).parent / "static" / "logo.svg"


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class NoteContext:
    """Everything the one-page note needs."""

    run_date: str
    target_quarter: str
    horizon: str
    new_nowcast: float
    old_nowcast: float | None = None
    total_change: float | None = None
    revision_effect: float = 0.0
    news_effect: float = 0.0
    reestimation_effect: float = 0.0
    top_drivers: list[dict] = field(default_factory=list)   # [{label, impact}, …]
    sectors: list[dict] = field(default_factory=list)        # [{label, growth_yoy, contribution_pp}, …]
    narrative: str = ""
    has_decomposition: bool = False


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

PDF_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "title": "GDP Nowcast Note",
        "subtitle": "Institute of Macroeconomic and Regional Studies",
        "as_of": "As of",
        "quarter": "Quarter",
        "horizon": "Horizon",
        "headline": "GDP Growth (y/y)",
        "change_from_prev": "Change from previous",
        "drivers": "Key Drivers",
        "sectors": "Sector Contributions",
        "sector_col": "Sector",
        "growth_col": "Growth (y/y %)",
        "contrib_col": "Contribution (pp)",
        "revision": "Data revisions",
        "news": "New data",
        "reestimation": "Model re-estimation",
        "pp": "pp",
        "higher": "higher",
        "lower": "lower",
        "disclaimer": (
            "This note is produced automatically by the IMRS nowcasting model. "
            "Figures are preliminary estimates subject to revision."
        ),
        "forecast": "forecast",
        "nowcast_m1": "early estimate (month 1)",
        "nowcast_m2": "estimate (month 2)",
        "nowcast_m3": "estimate (month 3)",
        "backcast": "backcast",
    },
    "uz": {
        "title": "YaIM Nowkast Xulosa",
        "subtitle": "Makroiqtisodiy va mintaqaviy tadqiqotlar instituti",
        "as_of": "Sana",
        "quarter": "Chorak",
        "horizon": "Gorizont",
        "headline": "YaIM o‘sishi (y/y)",
        "change_from_prev": "Avvalgisidan o‘zgarish",
        "drivers": "Asosiy omillar",
        "sectors": "Tarmoqlar bo‘yicha hissa",
        "sector_col": "Tarmoq",
        "growth_col": "O‘sish (y/y %)",
        "contrib_col": "Hissa (f.b.)",
        "revision": "Ma‘lumot tuzatishlari",
        "news": "Yangi ma‘lumotlar",
        "reestimation": "Model qayta baholash",
        "pp": "f.b.",
        "higher": "yuqori",
        "lower": "past",
        "disclaimer": (
            "Ushbu xulosa IMTI nowkasting modeli tomonidan avtomatik ravishda "
            "tayyorlangan. Raqamlar dastlabki baho bo‘lib, o‘zgarishi mumkin."
        ),
        "forecast": "prognoz",
        "nowcast_m1": "dastlabki baho (1-oy)",
        "nowcast_m2": "baho (2-oy)",
        "nowcast_m3": "baho (3-oy)",
        "backcast": "retrospektiv baho",
    },
}


# ---------------------------------------------------------------------------
# Template (embedded so the module is self-contained)
# ---------------------------------------------------------------------------

_PDF_TEMPLATE = r"""<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
<meta charset="utf-8">
<style>
@page { size: A4; margin: 18mm 16mm 14mm 16mm; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Inter, "Segoe UI", system-ui, sans-serif; color: #1a2744; font-size: 10pt; line-height: 1.45; }

/* header */
.header { display: flex; align-items: center; gap: 14px; border-bottom: 3px solid #e31e24; padding-bottom: 10px; margin-bottom: 14px; }
.header img { height: 44px; }
.header-text h1 { font-size: 16pt; font-weight: 700; color: #e31e24; }
.header-text p  { font-size: 8.5pt; color: #6b7280; }

/* KPI strip */
.kpi-strip { display: flex; gap: 12px; margin-bottom: 14px; }
.kpi { flex: 1; background: #f5f6f8; border-radius: 6px; padding: 10px 12px; text-align: center; }
.kpi .label { font-size: 7.5pt; color: #6b7280; text-transform: uppercase; letter-spacing: .04em; }
.kpi .value { font-size: 18pt; font-weight: 700; color: #1a2744; }
.kpi .value.red { color: #e31e24; }
.kpi .sub   { font-size: 8pt; color: #6b7280; }

/* decomposition bar */
.decomp { margin-bottom: 14px; }
.decomp h2 { font-size: 10pt; font-weight: 600; margin-bottom: 6px; }
.decomp-row { display: flex; gap: 8px; margin-bottom: 3px; font-size: 8.5pt; }
.decomp-row .bar-label { width: 110px; text-align: right; color: #6b7280; flex-shrink: 0; }
.decomp-row .bar-track { flex: 1; background: #eee; border-radius: 3px; height: 14px; position: relative; overflow: hidden; }
.decomp-row .bar-fill  { height: 100%; border-radius: 3px; }
.decomp-row .bar-val   { width: 50px; text-align: right; font-weight: 600; flex-shrink: 0; }
.fill-pos { background: #059669; }
.fill-neg { background: #e31e24; }

/* drivers */
.drivers { margin-bottom: 14px; }
.drivers h2 { font-size: 10pt; font-weight: 600; margin-bottom: 6px; }
.driver-list { font-size: 8.5pt; }
.driver-list li { margin-bottom: 2px; }
.driver-list .impact { font-weight: 600; }
.impact-pos { color: #059669; }
.impact-neg { color: #e31e24; }

/* sector table */
.sectors { margin-bottom: 14px; }
.sectors h2 { font-size: 10pt; font-weight: 600; margin-bottom: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; }
th { background: #1a2744; color: #fff; padding: 5px 8px; text-align: left; font-weight: 600; }
td { padding: 4px 8px; border-bottom: 1px solid #e5e7eb; }
tr:nth-child(even) td { background: #f9fafb; }
.num { text-align: right; }

/* narrative */
.narrative { font-size: 8.5pt; color: #374151; margin-bottom: 14px; line-height: 1.5; }

/* footer */
.footer { position: fixed; bottom: 0; left: 0; right: 0; text-align: center; font-size: 7pt; color: #9ca3af; padding: 8px 16mm; border-top: 1px solid #e5e7eb; }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  {% if logo_data %}<img src="{{ logo_data }}" alt="IMRS">{% endif %}
  <div class="header-text">
    <h1>{{ t.title }}</h1>
    <p>{{ t.subtitle }}</p>
  </div>
</div>

<!-- KPI strip -->
<div class="kpi-strip">
  <div class="kpi">
    <div class="label">{{ t.quarter }}</div>
    <div class="value">{{ target_quarter }}</div>
    <div class="sub">{{ horizon_label }}</div>
  </div>
  <div class="kpi">
    <div class="label">{{ t.headline }}</div>
    <div class="value red">{{ "%.1f" | format(new_nowcast) }}%</div>
    <div class="sub">{{ t.as_of }} {{ run_date }}</div>
  </div>
  {% if has_decomposition and old_nowcast is not none %}
  <div class="kpi">
    <div class="label">{{ t.change_from_prev }}</div>
    <div class="value">{{ "%+.1f" | format(total_change) }} {{ t.pp }}</div>
    <div class="sub">{{ "%.1f" | format(old_nowcast) }}% → {{ "%.1f" | format(new_nowcast) }}%</div>
  </div>
  {% endif %}
</div>

{% if has_decomposition and total_change is not none %}
<!-- Decomposition bars -->
<div class="decomp">
  <h2>{{ t.change_from_prev }}</h2>
  {% for item in decomp_items %}
  <div class="decomp-row">
    <span class="bar-label">{{ item.label }}</span>
    <span class="bar-track">
      <span class="bar-fill {{ 'fill-pos' if item.value >= 0 else 'fill-neg' }}"
            style="width: {{ item.pct }}%;"></span>
    </span>
    <span class="bar-val">{{ "%+.2f" | format(item.value) }}</span>
  </div>
  {% endfor %}
</div>
{% endif %}

{% if top_drivers %}
<!-- Drivers -->
<div class="drivers">
  <h2>{{ t.drivers }}</h2>
  <ol class="driver-list">
    {% for d in top_drivers %}
    <li>{{ d.label }}:
      <span class="impact {{ 'impact-pos' if d.impact >= 0 else 'impact-neg' }}">{{ "%+.2f" | format(d.impact) }} {{ t.pp }}</span>
    </li>
    {% endfor %}
  </ol>
</div>
{% endif %}

{% if sectors %}
<!-- Sector table -->
<div class="sectors">
  <h2>{{ t.sectors }}</h2>
  <table>
    <thead>
      <tr>
        <th>{{ t.sector_col }}</th>
        <th class="num">{{ t.growth_col }}</th>
        <th class="num">{{ t.contrib_col }}</th>
      </tr>
    </thead>
    <tbody>
      {% for s in sectors %}
      <tr>
        <td>{{ s.label }}</td>
        <td class="num">{{ "%.1f" | format(s.growth_yoy) }}</td>
        <td class="num">{{ "%+.2f" | format(s.contribution_pp) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

{% if narrative %}
<!-- Narrative -->
<div class="narrative">
  {{ narrative }}
</div>
{% endif %}

<div class="footer">{{ t.disclaimer }}</div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_pdf_note(
    ctx: NoteContext,
    lang: str = "en",
    out_path: str | Path = "note.pdf",
    logo_path: str | Path | None = None,
) -> Path:
    """Render the one-page PDF note (or HTML fallback).

    Parameters
    ----------
    ctx : NoteContext
        All data for the note.
    lang : str
        ``"en"`` or ``"uz"``.
    out_path : str | Path
        Destination file.  ``.pdf`` triggers WeasyPrint; anything else is raw
        HTML.
    logo_path : str | Path | None
        Override for the logo SVG path.

    Returns
    -------
    Path to the written file.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t = PDF_TRANSLATIONS.get(lang, PDF_TRANSLATIONS["en"])

    # Embed logo as data URI
    logo = Path(logo_path) if logo_path else LOGO_PATH
    logo_data = ""
    if logo.exists():
        svg_bytes = logo.read_bytes()
        b64 = base64.b64encode(svg_bytes).decode()
        logo_data = f"data:image/svg+xml;base64,{b64}"

    # Horizon label
    horizon_label = t.get(ctx.horizon, ctx.horizon)

    # Decomposition bar data
    decomp_items = []
    if ctx.has_decomposition and ctx.total_change is not None:
        max_abs = max(
            abs(ctx.revision_effect), abs(ctx.news_effect),
            abs(ctx.reestimation_effect), 0.01,
        )
        for label_key, value in [
            ("revision", ctx.revision_effect),
            ("news", ctx.news_effect),
            ("reestimation", ctx.reestimation_effect),
        ]:
            decomp_items.append({
                "label": t[label_key],
                "value": value,
                "pct": min(abs(value) / max_abs * 100, 100),
            })

    # Build Jinja2 context
    env = Environment(autoescape=False)
    template = env.from_string(_PDF_TEMPLATE)
    html = template.render(
        lang=lang,
        t=t,
        logo_data=logo_data,
        run_date=ctx.run_date,
        target_quarter=ctx.target_quarter,
        horizon_label=horizon_label,
        new_nowcast=ctx.new_nowcast,
        old_nowcast=ctx.old_nowcast,
        total_change=ctx.total_change,
        has_decomposition=ctx.has_decomposition,
        decomp_items=decomp_items,
        top_drivers=ctx.top_drivers or [],
        sectors=ctx.sectors or [],
        narrative=ctx.narrative,
    )

    # Try WeasyPrint; fall back to raw HTML
    if out_path.suffix.lower() == ".pdf":
        try:
            from weasyprint import HTML as WeasyprintHTML  # type: ignore[import-untyped]

            WeasyprintHTML(string=html).write_pdf(str(out_path))
            logger.info("PDF note written → %s", out_path)
            return out_path

        except ImportError:
            logger.warning(
                "weasyprint not installed — falling back to HTML. "
                "pip install weasyprint to enable PDF output."
            )
            html_path = out_path.with_suffix(".html")
            html_path.write_text(html, encoding="utf-8")
            logger.info("HTML note (fallback) written → %s", html_path)
            return html_path

        except Exception as exc:
            logger.warning("WeasyPrint failed (%s) — writing HTML fallback", exc)
            html_path = out_path.with_suffix(".html")
            html_path.write_text(html, encoding="utf-8")
            return html_path

    # Caller explicitly asked for HTML
    out_path.write_text(html, encoding="utf-8")
    logger.info("HTML note written → %s", out_path)
    return out_path
