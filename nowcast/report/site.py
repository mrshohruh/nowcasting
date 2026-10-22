"""Static site generator for the Uzbekistan GDP Nowcasting Platform.

Usage
-----
    # With real context from the production pipeline:
    python -m nowcast.report.site --context context.json --out _site

    # Demo mode — realistic sample data so the dashboard looks complete:
    python -m nowcast.report.site --demo --out _site

Programmatically::

    from nowcast.report.site import build_site, SiteContext
    ctx = SiteContext(run_date="2026-10-01", ...)
    build_site(ctx, out_dir="_site")
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from markupsafe import Markup
from jinja2 import Environment, FileSystemLoader

from nowcast.report import charts

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------
TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # Site-wide
        "site_title": "Uzbekistan GDP Nowcasting Platform",
        "site_name": "IMRS Nowcasting",
        "site_description": "Real-time GDP growth estimates for Uzbekistan",
        "nav_label": "Main navigation",
        "nav_toggle": "Toggle menu",
        # Nav items
        "nav_headline": "Headline",
        "nav_path": "Nowcast Path",
        "nav_whatchanged": "What Changed",
        "nav_sectors": "Sectors",
        "nav_monitor": "Data Monitor",
        "nav_models": "Models",
        "nav_quarter": "Quarter",
        "nav_downloads": "Downloads",
        "nav_methodology": "Methodology",
        # Footer
        "footer_org": "Institute of Macroeconomic and Regional Studies",
        "footer_generated": "Generated",
        # Headline page
        "target_quarter_label": "Target quarter",
        "as_of_label": "As of",
        "headline_subtitle": "GDP growth, year-on-year",
        "headline_chart_title": "Nowcast Timeline",
        "headline_chart_note": (
            "Past nowcasts are model replays (backtest), not forecasts published at the time: "
            "each uses only the observations that would have been released by that date, "
            "but with today's revised values. Official GDP is the latest release. "
            "Months 1 and 2 can be switched on in the legend."
        ),
        "pp_label": "pp",
        "vs_previous": "vs previous",
        "band_label": "80% band",
        "key_messages": "Key Messages",
        "sector_summary": "Sector Summary",
        "col_sector": "Sector",
        "col_weight": "Weight",
        "col_growth": "Growth (y/y %)",
        "col_contribution": "Contribution (pp)",
        "col_method": "Method",
        # Path page
        "path_description": "Each line traces how a quarter's nowcast evolved from month to month.",
        "path_chart_title": "Nowcast Evolution by Quarter",
        "path_table_title": "Nowcast Table",
        "col_run_date": "Run Date",
        "col_target": "Target",
        "col_horizon": "Horizon",
        "col_nowcast": "Nowcast",
        "col_change": "Change",
        # What changed page
        "no_decomposition": "First run — no comparison available yet",
        "previous_estimate": "Previous estimate",
        "current_estimate": "Current estimate",
        "total_change_label": "Total change",
        "waterfall_title": "Revision Waterfall",
        "decomposition_title": "Decomposition",
        "col_effect": "Component",
        "col_impact_pp": "Impact (pp)",
        "col_description": "Description",
        "effect_revision": "Data revisions",
        "effect_revision_desc": "Revised data for previously released periods",
        "effect_news": "New data",
        "effect_news_desc": "Newly released observations",
        "effect_reestimation": "Re-estimation",
        "effect_reestimation_desc": "Updated model parameters",
        "total_label": "Total",
        "grouped_impacts_title": "Impact by Group",
        "top_drivers_title": "Top Drivers",
        "col_variable": "Variable",
        "col_period": "Period",
        "col_surprise": "Surprise",
        # Sectors page
        "sectors_description": "GVA sector contributions to headline GDP growth from bridge equations.",
        "sectors_chart_title": "Sector Contributions",
        "sectors_table_title": "Sector Detail",
        "bridge_sum": "bridge sum",
        "dfm_comparison": "DFM headline comparison",
        # Monitor page
        "monitor_description": "Availability of input data used in the nowcasting model.",
        "monitor_availability": "Data Availability Matrix",
        "col_source": "Source",
        "col_frequency": "Frequency",
        "col_last_available": "Last Available",
        "legend_available": "Available",
        "legend_missing": "Missing",
        "legend_pending": "Not yet due",
        "monitor_series_detail": "Series Metadata",
        "col_series_id": "Series ID",
        "col_first_period": "First Period",
        "col_expected_lag": "Expected Lag",
        "months_label": "months",
        # Models page
        "models_description": "Backtest performance of the nowcasting models.",
        "backtest_summary_title": "Backtest Summary",
        "backtest_chart_title": "RMSE by Horizon",
        "model_specs_title": "Model Specifications",
        "col_model": "Model",
        "col_rmse": "RMSE",
        "col_mae": "MAE",
        "col_ratio_ar1": "vs AR(1)",
        "col_n_runs": "Runs",
        "ratio_note": "Ratio < 1 means the model beats a simple AR(1) benchmark.",
        # Downloads page
        "downloads_description": "Download past nowcasts, impact decomposition tables, and data vintages.",
        "no_downloads": "No download files available for this run.",
        # Horizon labels
        "forecast": "forecast",
        "nowcast_m1": "early estimate (month 1)",
        "nowcast_m2": "estimate (month 2)",
        "nowcast_m3": "estimate (month 3)",
        "backcast": "backcast",
    },
    "uz": {
        # Site-wide
        "site_title": "O'zbekiston YaIM Nowkasting Platformasi",
        "site_name": "IMTI Nowkasting",
        "site_description": "O'zbekiston uchun real vaqtda YaIM o'sish baholari",
        "nav_label": "Asosiy navigatsiya",
        "nav_toggle": "Menyu",
        # Nav items
        "nav_headline": "Asosiy",
        "nav_path": "Nowkast yo'li",
        "nav_whatchanged": "Nima o'zgardi",
        "nav_sectors": "Tarmoqlar",
        "nav_monitor": "Ma'lumotlar monitori",
        "nav_models": "Modellar",
        "nav_quarter": "Chorak",
        "nav_downloads": "Yuklab olish",
        "nav_methodology": "Metodologiya",
        # Footer
        "footer_org": "Makroiqtisodiy va mintaqaviy tadqiqotlar instituti",
        "footer_generated": "Yaratilgan",
        # Headline page
        "target_quarter_label": "Maqsadli chorak",
        "as_of_label": "Sana",
        "headline_subtitle": "YaIM o'sishi, yillik",
        "headline_chart_title": "Nowkast vaqt chizig'i",
        "headline_chart_note": (
            "Oldingi nowkastlar o'sha paytda e'lon qilingan prognozlar emas, balki modelning "
            "qayta hisob-kitoblaridir (backtest): ular faqat o'sha sanagacha e'lon qilinishi "
            "mumkin bo'lgan kuzatuvlardan foydalanadi, ammo qiymatlar bugungi qayta ko'rilgan "
            "qiymatlardir. Rasmiy YaIM - oxirgi e'lon qilingan qiymat. 1- va 2-oy "
            "nowkastlarini afsonadan yoqish mumkin."
        ),
        "pp_label": "f.b.",
        "vs_previous": "avvalgisiga nisbatan",
        "band_label": "80% oraliq",
        "key_messages": "Asosiy xabarlar",
        "sector_summary": "Tarmoqlar xulosasi",
        "col_sector": "Tarmoq",
        "col_weight": "Ulush",
        "col_growth": "O'sish (y/y %)",
        "col_contribution": "Hissa (f.b.)",
        "col_method": "Usul",
        # Path page
        "path_description": "Har bir chiziq chorak nowkastining oyma-oy qanday o'zgarganini ko'rsatadi.",
        "path_chart_title": "Chorak bo'yicha nowkast evolyutsiyasi",
        "path_table_title": "Nowkast jadvali",
        "col_run_date": "Hisoblash sanasi",
        "col_target": "Maqsad",
        "col_horizon": "Gorizont",
        "col_nowcast": "Nowkast",
        "col_change": "O'zgarish",
        # What changed page
        "no_decomposition": "Birinchi hisoblash — taqqoslash mavjud emas",
        "previous_estimate": "Avvalgi baho",
        "current_estimate": "Joriy baho",
        "total_change_label": "Umumiy o'zgarish",
        "waterfall_title": "O'zgarish tarkibi",
        "decomposition_title": "Dekompozitsiya",
        "col_effect": "Komponent",
        "col_impact_pp": "Ta'sir (f.b.)",
        "col_description": "Tavsif",
        "effect_revision": "Ma'lumot tuzatishlari",
        "effect_revision_desc": "Avval e'lon qilingan davrlar uchun tuzatilgan ma'lumotlar",
        "effect_news": "Yangi ma'lumotlar",
        "effect_news_desc": "Yangi e'lon qilingan kuzatuvlar",
        "effect_reestimation": "Qayta baholash",
        "effect_reestimation_desc": "Yangilangan model parametrlari",
        "total_label": "Jami",
        "grouped_impacts_title": "Guruh bo'yicha ta'sir",
        "top_drivers_title": "Asosiy omillar",
        "col_variable": "O'zgaruvchi",
        "col_period": "Davr",
        "col_surprise": "Kutilmagan",
        # Sectors page
        "sectors_description": "YaIM o'sishiga QQQ tarmoqlarining bridge tenglamalar orqali hissasi.",
        "sectors_chart_title": "Tarmoqlar hissasi",
        "sectors_table_title": "Tarmoqlar tafsiloti",
        "bridge_sum": "bridge yig'indisi",
        "dfm_comparison": "DFM bosh baho taqqoslash",
        # Monitor page
        "monitor_description": "Nowkasting modelida ishlatiladigan kirish ma'lumotlarining mavjudligi.",
        "monitor_availability": "Ma'lumotlar mavjudligi matritsasi",
        "col_source": "Manba",
        "col_frequency": "Chastota",
        "col_last_available": "Oxirgi mavjud",
        "legend_available": "Mavjud",
        "legend_missing": "Yo'q",
        "legend_pending": "Hali kutilmoqda",
        "monitor_series_detail": "Seriya metama'lumotlari",
        "col_series_id": "Seriya ID",
        "col_first_period": "Birinchi davr",
        "col_expected_lag": "Kutilgan kechikish",
        "months_label": "oy",
        # Models page
        "models_description": "Nowkasting modellarining backtest natijalari.",
        "backtest_summary_title": "Backtest xulosasi",
        "backtest_chart_title": "Gorizont bo'yicha RMSE",
        "model_specs_title": "Model spetsifikatsiyalari",
        "col_model": "Model",
        "col_rmse": "RMSE",
        "col_mae": "MAE",
        "col_ratio_ar1": "AR(1) ga nisbatan",
        "col_n_runs": "Ishga tushirishlar",
        "ratio_note": "Nisbat < 1 model oddiy AR(1) benchmarkni yengganini bildiradi.",
        # Downloads page
        "downloads_description": "O'tgan nowkastlar, ta'sir jadvallari va ma'lumotlar arxivini yuklab oling.",
        "no_downloads": "Ushbu ishga tushirish uchun yuklab olish fayllari mavjud emas.",
        # Horizon labels
        "forecast": "prognoz",
        "nowcast_m1": "dastlabki baho (1-oy)",
        "nowcast_m2": "baho (2-oy)",
        "nowcast_m3": "baho (3-oy)",
        "backcast": "retrospektiv baho",
    },
}

# ---------------------------------------------------------------------------
# Site context
# ---------------------------------------------------------------------------


@dataclass
class SiteContext:
    """Every piece of data the dashboard needs, in one serialisable object."""

    run_date: str
    target_quarter: str
    horizon: str

    # Headline
    new_nowcast: float
    old_nowcast: float | None = None
    total_change: float | None = None
    band_lo: float | None = None
    band_hi: float | None = None

    # Decomposition (serialised dict — see nowcast.explain.news.Decomposition)
    decomposition: dict | None = None

    # Sectors  [{sector, label, weight, growth_yoy, contribution_pp, method}, ...]
    sectors: list[dict] = field(default_factory=list)

    # Nowcast history  [{run_date, target_quarter, gdp_yoy, band_lo, band_hi}, ...]
    nowcast_history: list[dict] = field(default_factory=list)

    # Nowcast paths  {quarter: [{run_date, gdp_yoy, official?}, ...]}
    nowcast_paths: dict[str, list[dict]] = field(default_factory=dict)

    # Data monitor  [{variable, label, source, frequency, periods: {period: status}}, ...]
    monitor_data: list[dict] = field(default_factory=list)

    # Model performance  [{model, horizon, rmse, ratio_vs_ar1}, ...]
    backtest_results: list[dict] = field(default_factory=list)

    # Past nowcasts next to GDP actuals (backtest history rows), for the headline chart
    nowcast_vs_actual: list[dict] = field(default_factory=list)

    # Quarters published alongside this one, for the quarter selector  ["2026Q3", "2026Q4"]
    available_quarters: list[str] = field(default_factory=list)

    # Downloads  [{group, label, filename, format, description}, ...]
    download_files: list[dict] = field(default_factory=list)

    # Methodology (pre-rendered HTML body)
    methodology_html: str = ""

    # Narrative text (plain text from the Jinja2 note templates)
    narrative_en: str = ""
    narrative_uz: str = ""

    def to_json(self, path: str | Path) -> None:
        """Serialise to a JSON file."""
        Path(path).write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "SiteContext":
        """Deserialise from a JSON file."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**raw)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
PAGES = [
    ("headline", "nav_headline"),
    ("path", "nav_path"),
    ("whatchanged", "nav_whatchanged"),
    ("sectors", "nav_sectors"),
    ("monitor", "nav_monitor"),
    ("models", "nav_models"),
    ("downloads", "nav_downloads"),
    ("methodology", "nav_methodology"),
]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_site(ctx: SiteContext, out_dir: str | Path = "_site") -> Path:
    """Build the complete static dashboard site.

    Returns the output directory path.
    """
    from datetime import datetime

    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1. Copy static assets
    static_out = out / "static"
    shutil.copytree(str(STATIC_DIR), str(static_out))
    logger.info("Copied static assets to %s", static_out)

    # 2. Copy downloads if any
    dl_out = out / "downloads"
    dl_out.mkdir(exist_ok=True)

    # 3. Generate chart JSON specs
    chart_specs = _generate_charts(ctx)

    # 4. Set up Jinja2
    env = _build_jinja_env()

    # 5. Prepare template variables from ctx
    # -- Decomposition (flatten for templates) --
    decomp = ctx.decomposition or {}
    has_decomposition = bool(decomp and ctx.total_change is not None)
    revision_effect = decomp.get("revision_effect", 0.0)
    news_effect = decomp.get("news_effect", 0.0)
    reestimation_effect = decomp.get("reestimation_effect", 0.0)
    top_news = decomp.get("top_news", [])
    # Add surprise field to top_news items if missing (alias for impact)
    for item in top_news:
        if "surprise" not in item and "impact" in item:
            item["surprise"] = item["impact"]
    grouped_impacts_raw = decomp.get("grouped_impacts", {})
    # Convert dict → sorted list of {group, impact} for template iteration
    if isinstance(grouped_impacts_raw, dict):
        grouped_impacts = sorted(
            [{"group": k, "impact": v} for k, v in grouped_impacts_raw.items()],
            key=lambda x: abs(x["impact"]),
            reverse=True,
        )
    else:
        grouped_impacts = grouped_impacts_raw or []

    # -- Path table (flatten nowcast_paths dict → list of rows) --
    path_table: list[dict] = []
    for quarter, points in sorted(ctx.nowcast_paths.items()):
        prev_val = None
        for pt in points:
            val = pt.get("gdp_yoy", pt.get("nowcast"))
            change = (val - prev_val) if prev_val is not None else None
            path_table.append(dict(
                run_date=pt.get("run_date", ""),
                target_quarter=quarter,
                horizon="",
                nowcast=val,
                change=change,
            ))
            prev_val = val

    # -- Monitor: reshape for template --
    # Template expects monitor_table (list of row objects) + monitor_columns
    monitor_periods = set()
    for row in ctx.monitor_data:
        monitor_periods.update(row.get("periods", {}).keys())
    monitor_columns = sorted(monitor_periods)

    monitor_table: list[dict] = []
    for row in ctx.monitor_data:
        periods = row.get("periods", {})
        availability: dict[str, str] = {}
        for col in monitor_columns:
            raw = periods.get(col, "not_due")
            availability[col] = raw
        last_avail = ""
        for col in reversed(monitor_columns):
            if periods.get(col) == "available":
                last_avail = col
                break
        monitor_table.append(dict(
            label=row.get("label", row.get("variable", "")),
            source=row.get("source", ""),
            frequency=row.get("frequency", ""),
            availability=availability,
            last_available=last_avail,
        ))

    # -- Series metadata (for monitor detail panel) --
    series_metadata = [
        dict(
            series_id=row.get("variable", ""),
            label=row.get("label", ""),
            source=row.get("source", ""),
            frequency=row.get("frequency", ""),
            first_period="",
            expected_lag=2,
        )
        for row in ctx.monitor_data
    ]

    # -- Backtest summary: reshape for template --
    backtest_summary: list[dict] = []
    for b in ctx.backtest_results:
        is_headline = b.get("model", "") == "DFM"
        backtest_summary.append(dict(
            model=b.get("model", ""),
            horizon=b.get("horizon", ""),
            rmse=b.get("rmse", 0),
            mae=b.get("mae", b.get("rmse", 0) * 0.8),  # approximate if missing
            ratio=b.get("ratio_vs_ar1", b.get("ratio", 1.0)),
            n_runs=b.get("n_runs", 20),
            is_headline=is_headline,
        ))

    model_details = [
        dict(name="DFM (Dynamic Factor Model)", params={
            "Factors": "2", "VAR order": "2",
            "Idiosyncratic": "AR(1)", "EM iterations": "250",
        }),
        dict(name="Bridge equations", params={
            "Method": "Ridge regression", "Sectors": "10 GVA",
            "Fallback": "AR(1)", "Frequency": "Quarterly",
        }),
    ]

    # -- Downloads: group by category --
    download_groups_map: dict[str, dict] = {}
    group_titles = {
        "nowcast_history": {"en": "Nowcast History", "uz": "Nowkast tarixi"},
        "impact_tables": {"en": "Impact Decomposition", "uz": "Ta'sir jadvallari"},
        "vintages": {"en": "Data Vintages", "uz": "Ma'lumotlar arxivi"},
    }
    for dl in ctx.download_files:
        grp = dl.get("group", "other")
        if grp not in download_groups_map:
            download_groups_map[grp] = dict(
                title=grp,
                description=dl.get("description", ""),
                files=[],
            )
        download_groups_map[grp]["files"].append(dict(
            filename=dl.get("filename", ""),
            label=dl.get("label", dl.get("filename", "")),
            format=dl.get("format", ""),
            size="",
            date=ctx.run_date,
        ))

    # -- Sectors: compute total weight and bridge sum --
    total_weight = sum(s.get("weight", 0) for s in ctx.sectors)
    bridge_gdp = sum(s.get("contribution_pp", 0) for s in ctx.sectors)

    # -- Footer --
    year = ctx.run_date[:4] if ctx.run_date else str(datetime.now().year)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # -- Methodology content (mark safe so autoescape doesn't escape HTML) --
    methodology_content = Markup(ctx.methodology_html or _default_methodology_html())

    # 6. Render each language
    for lang in ("en", "uz"):
        lang_dir = out / lang
        lang_dir.mkdir(exist_ok=True)

        t = TRANSLATIONS[lang]
        horizon_label = t.get(ctx.horizon, ctx.horizon)
        narrative_raw = ctx.narrative_en if lang == "en" else ctx.narrative_uz
        # Convert plain-text paragraphs to HTML so they render with line breaks
        narrative_text = Markup("<br>".join(
            f"<p>{Markup.escape(p.strip())}</p>" if p.strip() else ""
            for p in narrative_raw.split("\n\n")
        )) if narrative_raw else ""

        # Set download group titles per language
        download_groups = []
        for grp_key, grp_data in download_groups_map.items():
            title_map = group_titles.get(grp_key, {})
            download_groups.append(dict(
                title=title_map.get(lang, grp_data["title"]),
                description=grp_data["description"],
                files=grp_data["files"],
            ))

        # Common template variables — matching what templates expect
        common = dict(
            lang=lang,
            root="../",
            t=t,
            year=year,
            generated_at=generated_at,
            # Headline data
            run_date=ctx.run_date,
            target_quarter=ctx.target_quarter,
            horizon_label=horizon_label,
            new_nowcast=ctx.new_nowcast,
            old_nowcast=ctx.old_nowcast,
            total_change=ctx.total_change,
            band_lo=ctx.band_lo,
            band_hi=ctx.band_hi,
            has_decomposition=has_decomposition,
            narrative_text=narrative_text,
            sectors=ctx.sectors,
            # Charts — individual vars as templates expect
            chart_headline=chart_specs.get("headline"),
            chart_path=chart_specs.get("path"),
            chart_waterfall=chart_specs.get("waterfall"),
            chart_sectors=chart_specs.get("sectors"),
            chart_backtest=chart_specs.get("models"),
            # What changed
            revision_effect=revision_effect,
            news_effect=news_effect,
            reestimation_effect=reestimation_effect,
            top_news=top_news,
            grouped_impacts=grouped_impacts,
            # Path
            path_table=path_table,
            # Monitor
            monitor_table=monitor_table,
            monitor_columns=monitor_columns,
            series_metadata=series_metadata,
            # Models
            backtest_summary=backtest_summary,
            model_details=model_details,
            # Downloads
            download_groups=download_groups,
            # Methodology
            methodology_content=methodology_content,
            # Sectors extra
            total_weight=total_weight,
            bridge_gdp=bridge_gdp,
            dfm_nowcast=ctx.new_nowcast,
        )

        for page_id, _nav_key in PAGES:
            template_name = f"{page_id}.html"
            try:
                tmpl = env.get_template(template_name)
            except Exception:
                logger.warning("Template %s not found — skipping", template_name)
                continue

            # Quarter selector: the same page in each published quarter's folder
            quarter_options = [
                dict(quarter=q,
                     href=f"../../{q}/{lang}/{page_id}.html",
                     selected=(q == ctx.target_quarter))
                for q in sorted(ctx.available_quarters or [ctx.target_quarter], reverse=True)
            ]
            html = tmpl.render(page=page_id, quarter_options=quarter_options, **common)
            (lang_dir / f"{page_id}.html").write_text(html, encoding="utf-8")
            logger.info("Rendered %s/%s.html", lang, page_id)

    # 7. Root index.html — redirect to en/headline.html
    (out / "index.html").write_text(
        '<!DOCTYPE html>\n<html><head>\n'
        '<meta http-equiv="refresh" content="0;url=en/headline.html">\n'
        '</head><body></body></html>\n',
        encoding="utf-8",
    )

    logger.info("Site built → %s", out)
    return out


def _build_jinja_env() -> Environment:
    """Create the Jinja2 environment for site templates."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=True,
    )
    # Custom filters — tojson_safe must return Markup so autoescape doesn't re-escape it
    _tojson = lambda v: Markup(json.dumps(v, default=str))
    env.filters["tojson_safe"] = _tojson
    env.filters["tojson"] = _tojson
    env.filters["format_pct"] = lambda v: f"{v:.1f}" if v is not None else "—"
    env.filters["format_pp"] = lambda v: f"{v:+.2f}" if v is not None else "—"
    env.filters["abs"] = abs
    return env


def _generate_charts(ctx: SiteContext) -> dict[str, dict | None]:
    """Build all chart figure dicts from the site context."""
    c: dict[str, dict | None] = {}

    # Headline
    if ctx.nowcast_vs_actual:
        c["headline"] = charts.nowcast_vs_actual_chart(
            ctx.nowcast_vs_actual,
            current=dict(quarter=ctx.target_quarter, value=ctx.new_nowcast,
                         band_lo=ctx.band_lo, band_hi=ctx.band_hi),
        )
    elif ctx.nowcast_history:
        c["headline"] = charts.headline_chart(ctx.nowcast_history)
    else:
        # Single-point fallback
        c["headline"] = charts.headline_chart([dict(
            run_date=ctx.run_date,
            target_quarter=ctx.target_quarter,
            gdp_yoy=ctx.new_nowcast,
            band_lo=ctx.band_lo,
            band_hi=ctx.band_hi,
        )])

    # Nowcast path
    if ctx.nowcast_paths:
        c["path"] = charts.nowcast_path_chart(ctx.nowcast_paths)
    else:
        c["path"] = None

    # Waterfall
    if ctx.decomposition:
        c["waterfall"] = charts.waterfall_chart(ctx.decomposition)
    else:
        c["waterfall"] = None

    # Sectors
    if ctx.sectors:
        c["sectors"] = charts.sector_chart(ctx.sectors)
    else:
        c["sectors"] = None

    # Data monitor — rendered as HTML table, not chart
    c["monitor"] = None

    # Model comparison
    if ctx.backtest_results:
        c["models"] = charts.model_comparison_chart(ctx.backtest_results)
    else:
        c["models"] = None

    return c


# ---------------------------------------------------------------------------
# Demo context
# ---------------------------------------------------------------------------

def _demo_context() -> SiteContext:
    """Build a realistic sample context for demo / boss-preview mode."""

    # Nowcast history — six monthly runs
    history = [
        dict(run_date="2026-05-01", target_quarter="2026Q2", gdp_yoy=5.4,
             band_lo=4.2, band_hi=6.6),
        dict(run_date="2026-06-01", target_quarter="2026Q2", gdp_yoy=5.7,
             band_lo=4.5, band_hi=6.9),
        dict(run_date="2026-07-01", target_quarter="2026Q2", gdp_yoy=5.9,
             band_lo=4.8, band_hi=7.0, official=True),
        dict(run_date="2026-08-01", target_quarter="2026Q3", gdp_yoy=5.6,
             band_lo=4.3, band_hi=6.9),
        dict(run_date="2026-09-01", target_quarter="2026Q3", gdp_yoy=5.8,
             band_lo=4.6, band_hi=7.0),
        dict(run_date="2026-10-01", target_quarter="2026Q3", gdp_yoy=6.2,
             band_lo=5.0, band_hi=7.4),
    ]

    # Nowcast paths — two quarters
    paths = {
        "2026Q2": [
            dict(run_date="2026-04-01", gdp_yoy=5.1),
            dict(run_date="2026-05-01", gdp_yoy=5.4),
            dict(run_date="2026-06-01", gdp_yoy=5.7),
            dict(run_date="2026-07-01", gdp_yoy=5.9, official=True),
        ],
        "2026Q3": [
            dict(run_date="2026-07-01", gdp_yoy=5.3),
            dict(run_date="2026-08-01", gdp_yoy=5.6),
            dict(run_date="2026-09-01", gdp_yoy=5.8),
            dict(run_date="2026-10-01", gdp_yoy=6.2),
        ],
    }

    # Decomposition
    decomp = dict(
        target_quarter="2026Q3",
        old_nowcast=5.8,
        new_nowcast=6.2,
        total_change=0.4,
        revision_effect=0.1,
        news_effect=0.2,
        reestimation_effect=0.1,
        run_date="2026-10-01",
        prev_run_date="2026-09-01",
        top_news=[
            dict(variable="ind_manuf", label="Manufacturing output",
                 period="2026-09", impact=0.12, group="Industry"),
            dict(variable="retail", label="Retail trade",
                 period="2026-09", impact=0.08, group="Trade"),
            dict(variable="exports_nogold", label="Non-gold exports",
                 period="2026-09", impact=-0.05, group="External"),
            dict(variable="constr", label="Construction output",
                 period="2026-09", impact=0.04, group="Construction"),
            dict(variable="cpi", label="Consumer prices",
                 period="2026-09", impact=0.03, group="Prices"),
        ],
        grouped_impacts={
            "Industry": 0.15,
            "Trade": 0.08,
            "Construction": 0.04,
            "Prices": 0.03,
            "External": -0.05,
            "Finance": -0.02,
        },
    )

    # Sectors
    sectors = [
        dict(sector="gva_manufacturing", label="Manufacturing", weight=0.22,
             growth_yoy=7.8, contribution_pp=1.72, method="ridge"),
        dict(sector="gva_trade_accommodation", label="Trade & accommodation", weight=0.14,
             growth_yoy=8.2, contribution_pp=1.15, method="ridge"),
        dict(sector="gva_construction", label="Construction", weight=0.07,
             growth_yoy=9.5, contribution_pp=0.67, method="ridge"),
        dict(sector="gva_mining", label="Mining", weight=0.10,
             growth_yoy=5.1, contribution_pp=0.51, method="ridge"),
        dict(sector="gva_transport", label="Transport", weight=0.08,
             growth_yoy=6.0, contribution_pp=0.48, method="ridge"),
        dict(sector="gva_ict", label="ICT", weight=0.04,
             growth_yoy=12.3, contribution_pp=0.49, method="ridge"),
        dict(sector="gva_agriculture", label="Agriculture", weight=0.25,
             growth_yoy=3.2, contribution_pp=0.80, method="AR"),
        dict(sector="gva_electricity", label="Electricity", weight=0.02,
             growth_yoy=4.1, contribution_pp=0.08, method="ridge"),
        dict(sector="gva_other_services", label="Other services", weight=0.05,
             growth_yoy=5.5, contribution_pp=0.28, method="AR"),
        dict(sector="net_taxes", label="Net taxes", weight=0.03,
             growth_yoy=6.0, contribution_pp=0.18, method="AR"),
    ]

    # Data monitor — use real series from catalogue
    monitor = _demo_monitor_data()

    # Backtest
    backtest = [
        dict(model="DFM", horizon="m1", rmse=1.42, ratio_vs_ar1=0.93),
        dict(model="DFM", horizon="m2", rmse=1.15, ratio_vs_ar1=0.90),
        dict(model="DFM", horizon="m3", rmse=0.957, ratio_vs_ar1=0.891),
        dict(model="Bridge", horizon="m1", rmse=1.55, ratio_vs_ar1=1.02),
        dict(model="Bridge", horizon="m2", rmse=1.25, ratio_vs_ar1=0.98),
        dict(model="Bridge", horizon="m3", rmse=1.10, ratio_vs_ar1=1.02),
        dict(model="AR(1)", horizon="m1", rmse=1.53, ratio_vs_ar1=1.00),
        dict(model="AR(1)", horizon="m2", rmse=1.28, ratio_vs_ar1=1.00),
        dict(model="AR(1)", horizon="m3", rmse=1.074, ratio_vs_ar1=1.00),
    ]

    # Downloads (sample)
    downloads = [
        dict(group="nowcast_history", label="Nowcast history (CSV)",
             filename="nowcast_history.csv", format="csv",
             description="All past nowcasts with dates and targets"),
        dict(group="nowcast_history", label="Nowcast history (Excel)",
             filename="nowcast_history.xlsx", format="xlsx",
             description="All past nowcasts with dates and targets"),
        dict(group="impact_tables", label="Impact decomposition (CSV)",
             filename="impacts_2026Q3.csv", format="csv",
             description="News and revision impacts for 2026Q3"),
        dict(group="vintages", label="Data vintage 2026-10-01 (Parquet)",
             filename="vintage_2026-10-01.parquet", format="parquet",
             description="Complete data snapshot as of 2026-10-01"),
    ]

    # Narrative
    narrative_en = (
        "As of 2026-10-01, the estimate (month 3) for 2026Q3 GDP growth is 6.2% y/y. "
        "This is 0.4 pp higher than the previous estimate of 5.8%.\n\n"
        "The change reflects upward pressure from Industry, Trade, Construction, "
        "partly offset by External. The largest single drivers are "
        "Manufacturing output, Retail trade, Non-gold exports."
    )
    narrative_uz = (
        "2026-10-01 holati bo'yicha 2026Q3 uchun YaIM o'sishi bahosi (3-oy) "
        "6.2% (y/y) ni tashkil etadi. Bu avvalgi 5.8% bahoga nisbatan 0.4 f.b. yuqori.\n\n"
        "O'zgarish Sanoat, Savdo, Qurilish tomonidan yuqoriga bosim, "
        "qisman Tashqi savdo tomonidan pastga bosimni aks ettiradi."
    )

    return SiteContext(
        run_date="2026-10-01",
        target_quarter="2026Q3",
        horizon="nowcast_m3",
        new_nowcast=6.2,
        old_nowcast=5.8,
        total_change=0.4,
        band_lo=5.0,
        band_hi=7.4,
        decomposition=decomp,
        sectors=sectors,
        nowcast_history=history,
        nowcast_paths=paths,
        monitor_data=monitor,
        backtest_results=backtest,
        download_files=downloads,
        methodology_html="",  # filled by the methodology template
        narrative_en=narrative_en,
        narrative_uz=narrative_uz,
    )


def _default_methodology_html() -> str:
    """Return default methodology content when none is provided."""
    return """
<h2>Data Sources</h2>
<p>The platform uses monthly and quarterly macroeconomic indicators from:</p>
<ul>
<li><strong>Uzstat</strong> — industrial production, construction, retail and wholesale trade, services, CPI, PPI, wages, investment, GDP (national accounts)</li>
<li><strong>Central Bank of Uzbekistan (CBU)</strong> — monetary aggregates (M2), FX reserves, policy rate, exchange rates</li>
<li><strong>World Bank / commodity markets</strong> — gold price</li>
</ul>

<h2>Transformations</h2>
<p>All series are seasonally adjusted using X-13ARIMA-SEATS and transformed to year-on-year growth rates
where applicable. Missing values are handled by the EM algorithm inside the Dynamic Factor Model.</p>

<h2>Models</h2>
<h3>Dynamic Factor Model (DFM)</h3>
<p>The headline model is a <em>Dynamic Factor Model with Mixed Frequencies</em>
(<code>statsmodels.DynamicFactorMQ</code>) that extracts 2 common factors from
the panel of monthly indicators and uses them to nowcast quarterly GDP growth.
Key parameters: VAR(2) factor dynamics, AR(1) idiosyncratic components, 250 EM iterations.</p>

<h3>Bridge Equations</h3>
<p>10 GVA sector bridge equations map monthly indicators to quarterly sectoral growth using
ridge regression. Sectors with insufficient monthly data fall back to an AR(1) process.
Weighted sector contributions are summed to produce an alternative GDP growth estimate.</p>

<h2>Uncertainty Bands</h2>
<p>The 80% uncertainty band is computed from the empirical distribution of
historical backtest errors at each horizon (month 1, month 2, month 3).
As more data arrives closer to the quarter end, the band narrows.</p>

<h2>News Decomposition</h2>
<p>Following <em>Bańbura &amp; Modugno (2014)</em>, changes between consecutive nowcasts
are decomposed into three components: data revisions, new data releases (news),
and model re-estimation effects. This uses a 4-run decomposition strategy.</p>

<h2>Revision Policy</h2>
<p>Nowcasts are produced on the 1st of each month at 06:00 Tashkent time (01:00 UTC).
Past nowcast values are never revised — each run is a snapshot preserved in the vintage store.</p>

<h2>Release Calendar</h2>
<p>Monthly, on the 1st. The first public release is planned for April 1, 2027.</p>

<h2>Disclaimer</h2>
<p>These estimates are produced automatically by the IMRS nowcasting model and are
preliminary. They are subject to revision as new data become available and should not
be interpreted as official GDP statistics.</p>
"""


def _demo_monitor_data() -> list[dict]:
    """Build demo data monitor using real series names from the catalogue."""
    # Last 6 months relative to the demo date 2026-10-01
    periods = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]

    def _row(var: str, label: str, source: str, freq: str, avail_through: str) -> dict:
        statuses = {}
        for p in periods:
            if freq == "Q" and not p.endswith(("-03", "-06", "-09", "-12")):
                statuses[p] = "not_applicable"
            elif p <= avail_through:
                statuses[p] = "available"
            elif p == periods[-1]:
                statuses[p] = "not_due"
            else:
                statuses[p] = "missing"
        return dict(variable=var, label=label, source=source,
                    frequency=freq, periods=statuses)

    return [
        _row("ind_total_real", "Industrial production", "Uzstat", "M", "2026-08"),
        _row("ind_mining_real", "Mining output", "Uzstat", "M", "2026-08"),
        _row("ind_manuf_real", "Manufacturing output", "Uzstat", "M", "2026-08"),
        _row("ind_elec_real", "Electricity production", "Uzstat", "M", "2026-08"),
        _row("constr_real", "Construction", "Uzstat", "M", "2026-08"),
        _row("retail_real", "Retail trade", "Uzstat", "M", "2026-08"),
        _row("wholesale_real", "Wholesale trade", "Uzstat", "M", "2026-08"),
        _row("mkt_services_growth", "Market services (14 types)", "Uzstat", "M", "2026-08"),
        _row("exports_usd", "Exports (goods & services)", "Uzstat", "M", "2026-08"),
        _row("imports_usd", "Imports (goods & services)", "Uzstat", "M", "2026-08"),
        _row("cpi_mm", "Consumer price index", "Uzstat", "M", "2026-09"),
        _row("ppi_mm", "Producer price index", "Uzstat", "M", "2026-09"),
        _row("usd_rate", "USD/UZS exchange rate", "CBU", "M", "2026-09"),
        _row("m2", "Broad money (M2)", "CBU", "M", "2026-08"),
        _row("reserves_fx", "FX reserves", "CBU", "M", "2026-08"),
        _row("gold_price", "Gold price", "World Bank", "M", "2026-09"),
        _row("policy_rate", "Policy rate", "CBU", "M", "2026-09"),
        _row("gdp_nag", "GDP (national accounts)", "Uzstat", "Q", "2026-06"),
        _row("wages_q", "Average wages", "Uzstat", "Q", "2026-06"),
        _row("invest_growth_q", "Investment growth", "Uzstat", "Q", "2026-06"),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build the nowcasting dashboard site")
    parser.add_argument("--context", type=str, help="Path to context.json")
    parser.add_argument("--demo", action="store_true", help="Build with demo data")
    parser.add_argument("--out", type=str, default="_site", help="Output directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.demo:
        ctx = _demo_context()
    elif args.context:
        ctx = SiteContext.from_json(args.context)
    else:
        parser.error("Provide --context or --demo")

    build_site(ctx, out_dir=args.out)


if __name__ == "__main__":
    main()
