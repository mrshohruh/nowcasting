"""Shared test helpers. Every test here runs offline on real files saved from the sources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from nowcast.catalogue import load

FIX = Path(__file__).resolve().parent / "fixtures"
ROOT = FIX.parents[1]
NAG_SEED = ROOT / "data" / "seed" / "nag_uzbekistan_mcd_2026-10-14.xlsx"
WB_DIR = FIX / "worldbank"


def response(url: str, content: bytes, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = content
    r.url = url
    r.encoding = "utf-8"
    return r


class FakeSession:
    """Answers GETs from a routing function url -> bytes (or an exception to raise)."""

    def __init__(self, route):
        self.route = route
        self.calls: list[str] = []
        self.headers: dict = {}

    def get(self, url, params=None, timeout=None):
        full = url + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
        self.calls.append(full)
        out = self.route(full)
        if isinstance(out, BaseException):
            raise out
        if isinstance(out, requests.Response):
            return out
        return response(full, out)


def siat_route(table_files: dict[int, Path]):
    """Route SIAT pointer and data-file URLs to fixture files."""

    def route(url: str):
        for tid, path in table_files.items():
            if f"/sdmx/{tid}/table/download/" in url:
                ptr = {
                    "updated_at": "2026-09-29T13:52:03+05:00",
                    "file": f"https://api.siat.stat.uz/media/uploads/sdmx/sdmx_data_{tid}.json",
                }
                return json.dumps(ptr).encode()
            if url.endswith(f"sdmx_data_{tid}.json"):
                return path.read_bytes()
        return None

    return route


@pytest.fixture(scope="session")
def catalogue() -> dict[str, dict]:
    return {s["id"]: s for s in load()}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Adapters pause between calls to be polite to the sources; tests don't need to."""
    import nowcast.ingest.adapters as adapters
    import nowcast.ingest.run as run

    monkeypatch.setattr(adapters.time, "sleep", lambda s: None)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
