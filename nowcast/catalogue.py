"""Load and validate config/series_catalogue.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "config" / "series_catalogue.yaml"

REQUIRED = ["id", "name", "publisher", "source", "url", "key", "frequency", "unit", "form",
            "prices", "block", "first_period", "months_after", "status"]
ALLOWED = {
    "publisher": {"Uzstat", "Central Bank of Uzbekistan", "World Bank"},
    "source": {"siat", "cbu_sdmx", "cbu_xlsx", "cbu_rate_json", "worldbank_xlsx", "stat_nag_xlsx"},
    "frequency": {"M", "Q"},
    "form": {"cumulative_ytd", "discrete", "level", "index_mm", "growth_yoy_cum", "growth_yoy"},
    "prices": {"nominal", "real", "price", "none"},
    "block": {"real", "services", "external", "prices_finance", "firms", "quarterly", "target"},
    "status": {"core", "candidate", "reference"},
}


def load(path: Path = CATALOGUE) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["series"]


def validate(series: list[dict]) -> list[str]:
    """Return a list of problems; empty means the catalogue is valid."""
    problems = []
    seen = set()
    for i, s in enumerate(series):
        sid = s.get("id", f"#{i}")
        for f in REQUIRED:
            if s.get(f) in (None, ""):
                problems.append(f"{sid}: missing {f}")
        for f, allowed in ALLOWED.items():
            if f in s and s[f] not in allowed:
                problems.append(f"{sid}: {f}={s[f]!r} not in {sorted(allowed)}")
        if sid in seen:
            problems.append(f"{sid}: duplicate id")
        seen.add(sid)
        if not isinstance(s.get("months_after"), int) or not 1 <= s["months_after"] <= 6:
            problems.append(f"{sid}: months_after must be an integer 1-6")
        if not str(s.get("url", "")).startswith("https://"):
            problems.append(f"{sid}: url must be https")
        if s.get("source") in ("siat", "stat_nag_xlsx"):
            codes = s.get("codes")
            if not isinstance(codes, list) or not codes or not all(isinstance(c, str) and c for c in codes):
                problems.append(f"{sid}: codes must be a non-empty list of strings for source {s['source']}")
    return problems
