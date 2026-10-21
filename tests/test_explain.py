"""Tests for Phase 4: news decomposition and narrative generation."""

import datetime as dt

import pandas as pd
import pytest

# ── State persistence tests ───────────────────────────────────────────────

class TestState:
    """Tests for nowcast.explain.state."""

    def test_panel_hash_deterministic(self):
        from nowcast.explain.state import panel_hash

        sa = pd.DataFrame({
            "variable": ["gdp", "cpi", "gdp"],
            "period": ["2026Q1", "2026-01", "2026Q2"],
            "value": [3.5, 12.1, 4.0],
        })
        h1 = panel_hash(sa)
        h2 = panel_hash(sa)
        assert h1 == h2
        assert len(h1) == 16

    def test_panel_hash_changes_on_value(self):
        from nowcast.explain.state import panel_hash

        sa1 = pd.DataFrame({
            "variable": ["gdp"], "period": ["2026Q1"], "value": [3.5],
        })
        sa2 = pd.DataFrame({
            "variable": ["gdp"], "period": ["2026Q1"], "value": [3.6],
        })
        assert panel_hash(sa1) != panel_hash(sa2)

    def test_save_load_roundtrip(self, tmp_path):
        from unittest.mock import MagicMock

        from nowcast.explain.state import load_state, save_state

        # Build a minimal mock Panels
        panels = MagicMock()
        panels.sa = pd.DataFrame({
            "variable": ["gdp", "cpi"],
            "period": ["2026Q1", "2026-01"],
            "value": [3.5, 12.1],
        })

        nowcast_table = {"2026Q1": 6.2, "2026Q2": 5.8}
        state = save_state(
            tmp_path, dt.date(2026, 10, 1), panels, nowcast_table,
            dfm_result=None, dfm_config={"n_factors": 2},
        )

        assert state.run_date == "2026-10-01"
        assert state.nowcast_table == nowcast_table

        # Load it back
        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded.run_date == "2026-10-01"
        assert loaded.nowcast_table == nowcast_table

    def test_load_state_missing(self, tmp_path):
        from nowcast.explain.state import load_state

        assert load_state(tmp_path) is None


# ── Number check tests ───────────────────────────────────────────────────

class TestNumberCheck:
    """Tests for the number-check function (task 4.8)."""

    def test_clean_note_passes(self):
        from nowcast.explain.narrative import number_check

        note = "GDP growth is 6.2% y/y. Manufacturing contributed +1.2 pp."
        assert number_check(note) == []

    def test_nan_flagged(self):
        from nowcast.explain.narrative import number_check

        note = "GDP growth is nan% y/y."
        warnings = number_check(note)
        assert any("NaN" in w for w in warnings)

    def test_inf_flagged(self):
        from nowcast.explain.narrative import number_check

        note = "GDP growth is inf% y/y."
        warnings = number_check(note)
        assert any("Inf" in w or "inf" in w.lower() for w in warnings)


# ── Decomposition dataclass tests ────────────────────────────────────────

class TestDecomposition:
    """Tests for the Decomposition dataclass."""

    def test_residual_zero(self):
        from nowcast.explain.news import Decomposition

        d = Decomposition(
            target_quarter="2026Q3",
            old_nowcast=6.0,
            new_nowcast=6.5,
            total_change=0.5,
            revision_effect=0.1,
            news_effect=0.3,
            reestimation_effect=0.1,
        )
        assert d.residual == pytest.approx(0.0, abs=0.001)

    def test_residual_nonzero(self):
        from nowcast.explain.news import Decomposition

        d = Decomposition(
            target_quarter="2026Q3",
            old_nowcast=6.0,
            new_nowcast=6.5,
            total_change=0.5,
            revision_effect=0.1,
            news_effect=0.2,
            reestimation_effect=0.1,
        )
        # 0.1 + 0.2 + 0.1 = 0.4, total is 0.5, residual = 0.1
        assert d.residual == pytest.approx(0.1, abs=0.001)

    def test_top_news(self):
        from nowcast.explain.news import Decomposition, NewsItem

        items = [
            NewsItem("cpi", "2026-07", 12.0, 11.5, 0.5, 0.1, 0.05),
            NewsItem("ind_manuf", "2026-07", 8.0, 5.0, 3.0, 0.2, 0.6),
            NewsItem("retail", "2026-07", 10.0, 9.8, 0.2, 0.05, 0.01),
        ]
        d = Decomposition(
            target_quarter="2026Q3",
            old_nowcast=6.0, new_nowcast=6.5, total_change=0.5,
            revision_effect=0.0, news_effect=0.5, reestimation_effect=0.0,
            news_items=items,
        )
        top2 = d.top_news(2)
        assert len(top2) == 2
        assert top2[0].variable == "ind_manuf"  # largest |impact|


# ── Sector contribution tests ────────────────────────────────────────────

class TestSectors:
    """Tests for sector contribution formatting."""

    def test_format_table(self):
        from nowcast.explain.sectors import SectorContribution, format_sector_table

        contribs = [
            SectorContribution("gva_mining", "Mining", 0.08, 5.0, 0.4, "ridge", 20, 0.85),
            SectorContribution("gva_agriculture", "Agriculture", 0.22, 3.0, 0.66, "AR", 16, 0.0),
        ]
        df = format_sector_table(contribs)
        assert len(df) == 3  # 2 sectors + total row
        assert "GDP (bridge sum)" in df["Sector"].values


# ── Narrative rendering tests ────────────────────────────────────────────

class TestNarrative:
    """Tests for Jinja2 template rendering."""

    def test_render_without_decomposition(self):
        from nowcast.explain.narrative import render_note

        note = render_note(
            decomposition=None,
            lang="en",
            run_date="2026-10-01",
            target_quarter="2026Q3",
            horizon="nowcast_m3",
            new_nowcast=6.2,
        )
        assert "6.2%" in note
        assert "2026Q3" in note
        assert "nan" not in note.lower()

    def test_render_with_decomposition(self):
        from nowcast.explain.narrative import render_note
        from nowcast.explain.news import Decomposition, NewsItem

        d = Decomposition(
            target_quarter="2026Q3",
            old_nowcast=6.0,
            new_nowcast=6.5,
            total_change=0.5,
            revision_effect=0.1,
            news_effect=0.3,
            reestimation_effect=0.1,
            news_items=[
                NewsItem("ind_manuf", "2026-07", 8.0, 5.0, 3.0, 0.2, 0.3, "Industry"),
                NewsItem("retail", "2026-07", 10.0, 9.8, 0.2, 0.1, 0.05, "Trade"),
            ],
            run_date="2026-10-01",
        )
        note = render_note(d, lang="en", horizon="nowcast_m3")
        assert "6.5%" in note
        assert "0.5 pp" in note
        assert "nan" not in note.lower()

    def test_render_uzbek(self):
        from nowcast.explain.narrative import render_note

        note = render_note(
            decomposition=None,
            lang="uz",
            run_date="2026-10-01",
            target_quarter="2026Q3",
            horizon="nowcast_m3",
            new_nowcast=6.2,
        )
        assert "6.2%" in note
        assert "YaIM" in note  # Uzbek for GDP
        assert "nan" not in note.lower()
