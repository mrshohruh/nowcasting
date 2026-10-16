"""Save real files from the hosts the Claude workspace cannot reach, as offline test fixtures
(task 1.10). Runs in .github/workflows/fixtures.yml on a GitHub runner.

    python tools/capture_fixtures.py tests/fixtures

Writes worldbank/commodity-markets.html (the page with the monthly link) and
worldbank/CMO-Historical-Data-Monthly.xlsx, then parses them with the adapter and prints what
it found.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nowcast.catalogue import load  # noqa: E402
from nowcast.ingest import http  # noqa: E402
from nowcast.ingest.adapters import PINK_LINK, WorldBank  # noqa: E402
from nowcast.ingest.base import FetchContext  # noqa: E402


def main(out: str) -> int:
    d = Path(out) / "worldbank"
    d.mkdir(parents=True, exist_ok=True)
    s = http.session()
    series = next(x for x in load() if x["id"] == "gold_price")
    page = http.get(s, series["url"])
    (d / "commodity-markets.html").write_bytes(page.content)
    a = WorldBank(series, FetchContext(run_date=dt.date.today(), session=s))
    raw = a.fetch()
    (d / "CMO-Historical-Data-Monthly.xlsx").write_bytes(raw.content)
    p = a.parse(raw)
    print("link:", PINK_LINK.search(page.text).group(1))
    print("xlsx:", raw.url, len(raw.content), "bytes")
    print("unit:", p.meta["unit"], "| points:", len(p.data), p.data["ref_period"].min(), "to", p.data["ref_period"].max())
    print(p.data.tail(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures"))
