"""Run state persistence for the news decomposition.

The four-run Banbura-Modugno decomposition needs:
    1. Previous-run DFM parameters (EM output)
    2. Previous-run panel data (which observations were available)
    3. Previous-run nowcast table (point estimates per quarter)

State is stored as a JSON sidecar + a parquet panel snapshot on the ``data``
branch under ``state/``:

    state/latest.json          metadata: run_date, panel_hash, nowcast_table, dfm_params_path
    state/dfm_params.npz       DFM parameters (numpy arrays from result.params + extras)
    state/panel_sa.parquet     SA panel snapshot (needed for old-data runs)

``save_state`` writes after each production run; ``load_state`` reads before
the next run so the decomposition can do old-params/old-data vs new-params/new-data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RunState:
    """Snapshot of one production run's output."""

    run_date: str  # ISO date
    panel_hash: str  # SHA-256 of the SA panel parquet bytes
    nowcast_table: dict[str, float]  # {quarter_str: gdp_yoy}
    dfm_params_path: str  # relative path to .npz
    panel_sa_path: str  # relative path to panel_sa.parquet

    # DFM estimation metadata
    n_factors: int = 2
    factor_orders: int = 2
    n_monthly_vars: int = 0
    n_quarterly_vars: int = 0
    em_iterations: int = 250


def panel_hash(sa_df: pd.DataFrame) -> str:
    """Deterministic hash of the SA panel (variable, period, value triples)."""
    # Sort for determinism, then hash the CSV representation
    cols = ["variable", "period", "value"]
    subset = sa_df[cols].sort_values(cols).reset_index(drop=True)
    csv_bytes = subset.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()[:16]


def extract_dfm_params(result) -> dict[str, np.ndarray]:
    """Extract serialisable parameter arrays from a DynamicFactorMQ result.

    Parameters
    ----------
    result : statsmodels DynamicFactorMQResults
        The fitted model result object.

    Returns
    -------
    dict of str -> ndarray
        Keys: 'params' (the full parameter vector), plus any smoother
        state we need for re-initialisation.
    """
    out = {"params": np.asarray(result.params, dtype=np.float64)}

    # Save the smoothed state and covariance for Kalman re-init
    if hasattr(result, "smoothed_state"):
        out["smoothed_state"] = np.asarray(result.smoothed_state, dtype=np.float64)
    if hasattr(result, "smoothed_state_cov"):
        out["smoothed_state_cov"] = np.asarray(result.smoothed_state_cov, dtype=np.float64)

    # Save filter output for news computation
    if hasattr(result, "filtered_state"):
        out["filtered_state"] = np.asarray(result.filtered_state, dtype=np.float64)
    if hasattr(result, "filtered_state_cov"):
        out["filtered_state_cov"] = np.asarray(result.filtered_state_cov, dtype=np.float64)

    return out


def save_state(
    state_dir: Path,
    run_date: dt.date,
    panels: Panels,  # noqa: F821
    nowcast_table: dict[str, float],
    dfm_result=None,
    dfm_config: dict | None = None,
) -> RunState:
    """Persist run state to disk.

    Parameters
    ----------
    state_dir : Path
        Directory to write into (e.g. ``store/state/``).
    run_date : date
        The as-of date of the run.
    panels : Panels
        The model-ready panels (we save the SA panel).
    nowcast_table : dict
        {quarter_str: gdp_yoy} point estimates.
    dfm_result : DynamicFactorMQResults, optional
        Fitted DFM result for parameter extraction.
    dfm_config : dict, optional
        DFM hyperparameters (n_factors, etc.).
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save SA panel snapshot
    panel_sa_path = "panel_sa.parquet"
    panels.sa.to_parquet(state_dir / panel_sa_path, index=False)

    # 2. Save DFM parameters
    dfm_params_path = "dfm_params.npz"
    if dfm_result is not None:
        params = extract_dfm_params(dfm_result)
        np.savez(state_dir / dfm_params_path, **params)
    else:
        # No DFM result — write an empty marker
        np.savez(state_dir / dfm_params_path, params=np.array([]))

    # 3. Compute panel hash
    ph = panel_hash(panels.sa)

    # 4. Build metadata
    cfg = dfm_config or {}
    state = RunState(
        run_date=run_date.isoformat(),
        panel_hash=ph,
        nowcast_table=nowcast_table,
        dfm_params_path=dfm_params_path,
        panel_sa_path=panel_sa_path,
        n_factors=cfg.get("n_factors", 2),
        factor_orders=cfg.get("factor_orders", 2),
        n_monthly_vars=cfg.get("n_monthly_vars", 0),
        n_quarterly_vars=cfg.get("n_quarterly_vars", 0),
        em_iterations=cfg.get("em_max_iter", 250),
    )

    # 5. Write metadata JSON
    meta_path = state_dir / "latest.json"
    meta_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

    logger.info(
        "Saved run state: date=%s panel_hash=%s quarters=%s",
        run_date,
        ph,
        list(nowcast_table.keys()),
    )
    return state


def load_state(state_dir: Path) -> RunState | None:
    """Load the most recent run state from disk.

    Returns None if no state has been saved yet (first run).
    """
    state_dir = Path(state_dir)
    meta_path = state_dir / "latest.json"

    if not meta_path.exists():
        logger.info("No previous run state found at %s", meta_path)
        return None

    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    return RunState(**raw)


def load_old_params(state_dir: Path, state: RunState) -> dict[str, np.ndarray] | None:
    """Load the saved DFM parameter arrays.

    Returns None if the params file is missing or empty.
    """
    params_path = Path(state_dir) / state.dfm_params_path
    if not params_path.exists():
        return None

    data = dict(np.load(params_path, allow_pickle=False))
    if "params" in data and data["params"].size == 0:
        return None
    return data


def load_old_panel_sa(state_dir: Path, state: RunState) -> pd.DataFrame | None:
    """Load the saved SA panel snapshot."""
    panel_path = Path(state_dir) / state.panel_sa_path
    if not panel_path.exists():
        return None
    return pd.read_parquet(panel_path)
