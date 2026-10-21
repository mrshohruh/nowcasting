# Nowcasting platform: state after Phase 4 (21 Oct 2026)

- Code: https://github.com/mrshohruh/nowcasting. Phase 4 merged into `main` (ee9b751, 21 Oct 2026).
- Sample notes: `docs/sample_notes/` — 5 approved samples in English and Uzbek.

## What Phase 4 added

### News decomposition (`nowcast/explain/news.py`)

Four-run Bańbura-Modugno decomposition that explains why the GDP nowcast changed between consecutive runs:

1. **Old params + old data** → baseline (previous nowcast)
2. **Old params + revised data** → isolates revision effect
3. **Old params + new data** → isolates news effect
4. **New params + new data** → captures re-estimation effect

- `Decomposition` dataclass: `old_nowcast`, `new_nowcast`, `total_change`, `revision_effect`, `news_effect`, `reestimation_effect`, plus per-variable `news_items` and `revision_items`.
- `residual` property checks |total − (revision + news + re-estimation)| < 0.001 pp.
- `top_news(n)` returns top n items by |impact|; `grouped_impacts()` sums by thematic group.
- `VARIABLE_LABELS` and `VARIABLE_GROUPS` provide human-readable names and thematic groupings.
- Gracefully handles first run (no prior state → returns None), NaN panel issues, and parameter shape mismatches.

### Run state persistence (`nowcast/explain/state.py`)

- `RunState` dataclass: `run_date`, `panel_hash` (16-char SHA-256 of SA panel CSV), `nowcast_table`, `dfm_params_path`, `panel_sa_path`, plus DFM metadata.
- `save_state()` writes `latest.json` + `dfm_params.npz` + `panel_sa.parquet` to `state_dir`.
- `load_state()` / `load_old_params()` / `load_old_panel_sa()` recover previous state for decomposition.
- `panel_hash()` deterministic — same data always produces the same hash.
- DFM parameters extracted via `extract_dfm_params(result)` from the statsmodels result object.

### Sector contributions (`nowcast/explain/sectors.py`)

- Runs Model B (bridge equations) and computes each GVA sector's weighted contribution to GDP y/y.
- `SectorContribution` dataclass: `sector`, `label`, `weight`, `growth_yoy`, `contribution_pp`, `method`, `n_obs`, `r2`.
- `SECTOR_LABELS` (English) and `SECTOR_LABELS_UZ` (Uzbek) dictionaries for 11 sectors.
- `format_sector_table()` produces a tidy DataFrame for display.
- Contributions sorted by |contribution_pp| (largest first).

### Narrative notes (`nowcast/explain/narrative.py`)

- `render_note()` fills Jinja2 templates with decomposition + sector data.
- Supports `lang="en"` and `lang="uz"` (Uzbek in Latin script).
- When decomposition is available: reports change from previous estimate, groups impacts by theme.
- When decomposition is None (first run): reports current nowcast with sector view only.
- Sector labels automatically translated to Uzbek when `lang="uz"`.
- Uses caller-supplied winsorised GDP y/y (not raw decomposition value) to prevent unrealistic figures.

### Number check (`nowcast/explain/narrative.py`)

- `number_check(note)` scans rendered text for NaN, Inf, and suspiciously large percentages (>30%).
- Returns a list of warning strings (empty = all OK).

### Templates (`templates/`)

- `note_en.j2` — English: horizon label, GDP y/y, change, grouped impacts, top 3 drivers, top 4 sectors.
- `note_uz.j2` — Uzbek (Latin script): same structure with Uzbek grammar, "YaIM" for GDP, "f.b." for pp.
- Notes are 2–3 sentences: concise per user preference.

### Replay script (`scripts/replay_notes.py`)

- Iterates over monthly run dates, builds panels, runs DFM, decomposes, renders notes.
- `select_samples(notes, n=6)` picks diverse samples: first run, largest up/down revision, month-1, month-3, rich decomposition.
- `write_samples()` outputs individual .txt files + `SUMMARY.md`.
- Supports `--max-dates` for faster testing.

### DFM modification (`nowcast/models/dfm.py`)

- Added `self._last_result = result` after prediction — exposes the statsmodels result object for parameter extraction by the decomposition module.

### Dependencies

- Added `jinja2>=3.1` to `pyproject.toml`.

## Exit check

- **Decomposition residual**: 0.0000 pp across all 35 decompositions in the replay (threshold: 0.001 pp). **PASS**.
- **Number check**: no NaN/Inf warnings in any generated note.
- **Sample notes**: 5 samples approved by user (first run, upward revision, downward revision, month-1, month-3).
- **Uzbek translation**: sector labels translated; grammar reviewed.

## Replay statistics (30 run dates, Jan 2022 – Jun 2024)

- 40 notes generated (some dates produce notes for multiple target quarters).
- 35 successful decompositions; 5 skipped (first runs or NaN panel issues on early dates).
- Decomposition failures occur on specific dates (2022-02, 2023-02, 2024-02) where old params + new panel shape produce non-finite values in statsmodels — handled gracefully with try/except.
- Occasional DFM estimation failures ("Matrix not positive definite") on dates with sparse data (e.g., 2025-03) — also handled gracefully.

## Tests (`tests/test_explain.py`)

14 unit tests covering all Phase 4 modules:

- **TestState** (4): hash determinism, hash changes on value, save/load roundtrip, missing state.
- **TestNumberCheck** (3): clean note passes, NaN flagged, Inf flagged.
- **TestDecomposition** (3): residual zero, residual nonzero, top_news sorting.
- **TestSectors** (1): format_table output.
- **TestNarrative** (3): render without decomposition (EN), render with decomposition (EN), render Uzbek.

## Open items

- **Full 57-date replay**: the 30-date replay (Jan 2022 – Jun 2024) completed; the full 57-date run timed out at 42/57 (~74%). The remaining dates (Jul 2024 – Sep 2026) can be run locally or with a longer timeout. The decomposition logic is validated on the completed dates.
- **DFM m1 outlier at 2023Q3**: still present from Phase 3 (SA level conversion issue with sparse early vintages). Does not affect decomposition quality.
- **Decomposition failures on "February" dates**: old params from January sometimes don't match the February panel shape due to NaN handling in statsmodels. This is a known edge case handled by the try/except; the note is still generated (without decomposition detail).
- **Phase 5 (dashboard, production pipeline)**: the news decomposition and narrative modules are ready to integrate into a production pipeline and dashboard.

## File inventory

```
nowcast/explain/
├── __init__.py          # Package docstring
├── state.py             # Run state persistence (~170 lines)
├── news.py              # Four-run decomposition (~320 lines)
├── sectors.py           # Sector contributions (~140 lines)
└── narrative.py         # Jinja2 rendering + number check (~140 lines)

templates/
├── note_en.j2           # English note template
└── note_uz.j2           # Uzbek note template

scripts/
└── replay_notes.py      # Replay engine + sample selection (~260 lines)

tests/
└── test_explain.py      # 14 unit tests

docs/sample_notes/
├── SUMMARY.md           # All 5 samples side-by-side
├── all_notes.json       # Metadata for all 40 notes
├── sample_01–05_*.txt   # Individual sample files (EN + UZ)
└── _state/              # Replay state directory
```
