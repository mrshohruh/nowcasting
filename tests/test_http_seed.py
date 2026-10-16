"""HTTP retries (task 1.1) and seed history (task 1.4)."""

import datetime as dt

import pytest
import requests
from conftest import FakeSession, response

from nowcast.ingest import http
from nowcast.store import seed
from nowcast.store.vintages import Store


def flaky(*answers):
    it = iter(answers)

    def route(url):
        a = next(it)
        return a if isinstance(a, (BaseException, requests.Response)) else response(url, a)

    return route


def test_retries_then_succeeds():
    s = FakeSession(flaky(requests.ConnectionError("x"), response("u", b"", 503), b"ok"))
    waits = []
    assert http.get(s, "https://e.org/f", sleep=waits.append).content == b"ok"
    assert waits == [2.0, 4.0]


def test_429_waits_for_retry_after():
    r429 = response("u", b"", 429)
    r429.headers["Retry-After"] = "7"
    waits = []
    assert http.get(FakeSession(flaky(r429, b"ok")), "https://e.org/f", sleep=waits.append).content == b"ok"
    assert waits == [7.0]


def test_gives_up_after_three_retries_and_does_not_retry_404():
    s = FakeSession(flaky(*[requests.Timeout("t")] * 4))
    with pytest.raises(requests.Timeout):
        http.get(s, "https://e.org/f", sleep=lambda x: None)
    assert len(s.calls) == 4
    s404 = FakeSession(flaky(response("u", b"", 404)))
    with pytest.raises(requests.HTTPError):
        http.get(s404, "https://e.org/f", sleep=lambda x: None)
    assert len(s404.calls) == 1


def test_seed_workbook_covers_the_catalogue_series(catalogue):
    ws = seed.workbook_series()
    assert set(ws) <= set(catalogue)
    assert ws["mkt_services_growth"]["item"].nunique() == 14
    assert ws["m2"].set_index("ref_period").loc["2019-01", "value"] > 0


def test_load_seed_once(tmp_path, catalogue):
    st = Store(tmp_path)
    written = seed.load_seed(st, list(catalogue.values()))
    assert written[0] == "seed-2026-10-14"
    assert seed.load_seed(st, list(catalogue.values())) == []  # never twice
    on_14th = st.as_of(dt.date(2026, 10, 14))
    assert {"gdp_nag", "gva_services_q", "m2", "gold_price", "remittances_q"} <= set(on_14th["series_id"])
    assert st.as_of(dt.date(2026, 10, 13)).empty
    if seed.USD_DAILY.exists():
        assert written[1] == "seed-2026-10-16"
        usd = st.latest(["usd_rate"]).set_index("ref_period")["value"]
        assert usd.index.min() == "2018-01" and usd.index.max() == "2026-09"
