"""DFM inputs table: factor R-squared is read by position and the rows are sorted by strength."""

import pandas as pd

from nowcast.explain.dfm_inputs import dfm_inputs
from nowcast.transform.panels import Panels


class _FakeResult:
    # statsmodels names the factor columns '0', '1' (strings), not 0, 1
    coefficients_of_determination = pd.DataFrame(
        {"0": [0.05, 0.40], "1": [0.10, 0.02]}, index=["ind_mining", "retail"]
    )


def _panels() -> Panels:
    yoy = pd.DataFrame({
        "variable": ["ind_mining", "retail"], "freq": ["M", "M"],
        "period": ["2026-08", "2026-08"], "value": [-18.2, 12.0],
    })
    meta = pd.DataFrame({
        "variable": ["ind_mining", "retail"], "freq": ["M", "M"], "block": ["real", "real"],
    })
    return Panels(as_of=None, yoy=yoy, sa=pd.DataFrame(), meta=meta,
                  flags=pd.DataFrame(), levels={}, modes={})


def test_r2_is_read_and_rows_sorted_by_strength():
    rows = dfm_inputs(_FakeResult(), _panels(), ["ind_mining", "retail"])
    assert [r["variable"] for r in rows] == ["retail", "ind_mining"]
    assert rows[0]["r2_f1"] == 0.40 and rows[0]["r2_f2"] == 0.02
    assert rows[1]["last_yoy"] == -18.2 and rows[0]["block"] == "Real activity"
