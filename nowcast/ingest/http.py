"""One HTTP session for every source: timeouts, retries with backoff, a clear user-agent (task 1.1).

SIAT answers HTTP 429 after roughly 15-20 quick calls; on 429 we wait for Retry-After (or 20 s,
40 s, ...). Other failures (connection errors, timeouts, 5xx) are retried 3 times with 2, 4, 8 s
pauses. 4xx other than 429 are not retried: they mean the URL is wrong, and retrying hides that.
"""

from __future__ import annotations

import time

import requests

USER_AGENT = "nowcast-uz/0.2 (+https://github.com/mrshohruh/nowcasting)"
TIMEOUT = (15, 120)  # connect, read (seconds)
RETRIES = 3


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def get(
    s: requests.Session, url: str, params: dict | None = None, retries: int = RETRIES, sleep=None
) -> requests.Response:
    """GET with retries. Raises the last error when every attempt fails."""
    sleep = sleep or time.sleep
    backoff = 2.0
    wait_429 = 20.0
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, params=params, timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        else:
            if r.status_code == 429:
                last = requests.HTTPError(f"429 Too Many Requests for {url}", response=r)
                if attempt < retries:
                    ra = r.headers.get("Retry-After", "")
                    sleep(float(ra) if ra.isdigit() else wait_429)
                    wait_429 *= 2
                continue
            if 500 <= r.status_code < 600:
                last = requests.HTTPError(f"{r.status_code} server error for {url}", response=r)
            else:
                r.raise_for_status()
                return r
        if attempt < retries:
            sleep(backoff)
            backoff *= 2
    assert last is not None
    raise last
