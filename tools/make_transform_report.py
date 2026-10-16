"""Write and run notebooks/transform_report.ipynb (task 2.10).

    python tools/make_transform_report.py --store <data branch checkout> [--as-of YYYY-MM-DD]

The notebook is generated (one section per panel variable), executed, and saved with its outputs
so it can be read on GitHub without running anything.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import nbformat
from nbconvert.preprocessors import ExecutePreprocessor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "notebooks" / "transform_report.ipynb"

INTRO = """# Transformation report (Phase 2, task 2.10)

Built from the data store as of **{as_of}**. For every panel variable: (1) the input as
published, (2) the real level and its seasonally adjusted version, (3) y/y growth as it enters
the y/y panel, (4) SA m/m or q/q growth as it enters the factor model's panel, with the outliers
that are set to missing marked in red.

The method is in `docs/transformations.md`; the choices for each series are in the table below.
**Exit check:** spot-check 10 series against your own numbers (the last-values table helps) and
note anything that looks wrong.

How to read the choices table:
* *real terms / weights*: `prev_year` = last year's nominal values of the series (exact);
  `same_year` = the first year of the nominal series, where this year's shares stand in;
  `fallback:` = a related nominal series; `equal` = no nominal values, every period weighs the same.
* *seasonal strength* (0-1): share of the variance of the detrended log level that is seasonal.
* *leftover seasonality p*: F-test that the SA growth rates have the same mean in every month
  (quarter); below 0.05 means seasonality is left over (X-13 with holiday effects then needs a look).
"""


def build(store: str, as_of: str | None) -> nbformat.NotebookNode:
    nb = nbformat.v4.new_notebook()
    cells = [nbformat.v4.new_markdown_cell(INTRO.format(as_of=as_of or "the run date"))]
    cells.append(nbformat.v4.new_code_cell(f"""\
import os
import warnings

import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import display

from nowcast.store.vintages import Store
from nowcast.transform import report
from nowcast.transform.inputs import load_inputs
from nowcast.transform.panels import build_panels

warnings.filterwarnings("ignore")
pd.set_option("display.max_rows", 300)
pd.set_option("display.max_colwidth", 70)
plt.rcParams["figure.dpi"] = 72
STORE = os.environ.get("NOWCAST_STORE", {store!r})
AS_OF = {as_of!r} or str(pd.Timestamp.now(tz="Asia/Tashkent").date())
inputs = load_inputs(Store(STORE), AS_OF)
panels = build_panels(inputs=inputs)
print(f"as of {{AS_OF}}: {{panels.meta['yoy_n'].notna().sum()}} variables in the y/y panel, "
      f"{{panels.meta['sa_n'].notna().sum()}} in the SA panel")
print("input modes:", pd.Series(panels.modes).value_counts().to_dict())"""))
    cells.append(nbformat.v4.new_markdown_cell("## Choices for each variable"))
    cells.append(nbformat.v4.new_code_cell("display(report.choices_table(panels))"))
    cells.append(nbformat.v4.new_markdown_cell("## Flags\n\nEvery period that was treated in a special way."))
    cells.append(nbformat.v4.new_code_cell("display(report.flags_table(panels))"))
    cells.append(nbformat.v4.new_markdown_cell("## Last six y/y values (for the spot check)"))
    cells.append(nbformat.v4.new_code_cell("display(report.last_values(panels))"))
    import yaml

    spec = yaml.safe_load((ROOT / "config" / "transforms.yaml").read_text(encoding="utf-8"))
    for section, key in (("Monthly variables", "monthly"), ("Quarterly variables", "quarterly")):
        cells.append(nbformat.v4.new_markdown_cell(f"## {section}"))
        for v in spec[key]:
            cells.append(nbformat.v4.new_markdown_cell(f"### `{v['id']}`"))
            cells.append(nbformat.v4.new_code_cell(
                f"fig = report.figure(panels, inputs, {v['id']!r})\nplt.show()"))
    nb.cells = cells
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return nb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    nb = build("../store", a.as_of)  # path recorded in the notebook; overridden by NOWCAST_STORE
    os.environ["NOWCAST_STORE"] = str(Path(a.store).resolve())
    ExecutePreprocessor(timeout=900, kernel_name="python3").preprocess(nb, {"metadata": {"path": str(ROOT)}})
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, out)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
