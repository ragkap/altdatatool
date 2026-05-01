import hashlib
import json
import random
import sqlite3
import time
from datetime import datetime, timezone

import httpx

from app.config import CACHE_DB_PATH

EXPLORE_URL = "https://trends.google.com/trends/api/explore"
SINGLE_URL = "https://trends.google.com/trends/api/widgetdata/multiline"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TZ = -480  # SGT, matches the URL the user provided
HL = "en-US"
CACHE_TTL = 60 * 60 * 24 * 7  # 7 days


class TrendsRateLimited(Exception):
    """Trends rate-limited us after exhausting retries."""


def _get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict,
    *,
    max_attempts: int = 4,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        r = client.get(url, params=params, headers=HEADERS)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            # exponential backoff with jitter: 1.5s, 3s, 6s, 12s
            delay = (1.5 * (2 ** attempt)) + random.uniform(0, 0.5)
            last_exc = httpx.HTTPStatusError(
                f"Trends {r.status_code}", request=r.request, response=r
            )
            if attempt < max_attempts - 1:
                time.sleep(delay)
                continue
        # non-retryable or final attempt
        r.raise_for_status()
    if last_exc:
        raise TrendsRateLimited(
            "Google Trends rate-limited the request after retries. "
            "Try again in a few minutes."
        ) from last_exc
    raise RuntimeError("unreachable")


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(CACHE_DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trends_cache (
            key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        )
        """
    )
    return c


def _cache_key(keywords: list[str], years: list[int], geo: str) -> str:
    raw = json.dumps(
        {"k": sorted(k.strip() for k in keywords), "y": sorted(years), "g": geo},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    with _db() as c:
        row = c.execute(
            "SELECT payload, fetched_at FROM trends_cache WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    payload, fetched_at = row
    if time.time() - fetched_at > CACHE_TTL:
        return None
    return json.loads(payload)


def _cache_set(key: str, payload: dict) -> None:
    with _db() as c:
        c.execute(
            "INSERT OR REPLACE INTO trends_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload), int(time.time())),
        )


def _strip_jsonp(text: str) -> dict:
    i = text.find("{")
    return json.loads(text[i:])


def _kw_join(keywords: list[str]) -> str:
    # Trends explore accepts OR-joined keywords as " + " separated string
    return " + ".join(keywords)


def _explore_token(
    client: httpx.Client, keywords: list[str], time_range: str, geo: str
) -> tuple[str, dict]:
    explore_req = {
        "comparisonItem": [
            {"keyword": _kw_join(keywords), "geo": geo or "", "time": time_range}
        ],
        "category": 0,
        "property": "",
    }
    r = _get_with_retry(
        client,
        EXPLORE_URL,
        {
            "hl": HL,
            "tz": str(TZ),
            "req": json.dumps(explore_req, separators=(",", ":")),
        },
    )
    data = _strip_jsonp(r.text)
    for w in data.get("widgets", []):
        if w.get("id") == "TIMESERIES":
            return w["token"], w["request"]
    raise RuntimeError("No TIMESERIES widget in explore response")


def _fetch_year(
    client: httpx.Client, keywords: list[str], year: int, geo: str
) -> list[dict]:
    token, request = _explore_token(
        client, keywords, f"{year}-01-01 {year}-12-31", geo
    )
    r = _get_with_retry(
        client,
        SINGLE_URL,
        {
            "hl": HL,
            "tz": str(TZ),
            "req": json.dumps(request, separators=(",", ":")),
            "token": token,
        },
    )
    data = _strip_jsonp(r.text)
    timeline = (data.get("default") or {}).get("timelineData", [])
    out = []
    for entry in timeline:
        ts = entry.get("time")
        values = entry.get("value", [])
        if ts is None or not values:
            continue
        date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"date": date, "value": values[0]})
    return out


def fetch_years(
    keywords: list[str], years: list[int], geo: str = ""
) -> dict:
    """Return per-year weekly Google Trends interest series.

    Output: {"keywords": [...], "years": [...], "series": {"2024": [{date, value}, ...]}}
    Each year is fetched and normalized independently (0-100 within that year),
    which is required for true YoY comparison.
    """
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        raise ValueError("at least one keyword required")
    years = sorted(set(int(y) for y in years))

    key = _cache_key(keywords, years, geo)
    cached = _cache_get(key)
    if cached:
        return cached

    series: dict[str, list[dict]] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        client.get("https://trends.google.com/trends/", headers=HEADERS)
        for y in years:
            try:
                series[str(y)] = _fetch_year(client, keywords, y, geo)
            except Exception as e:
                series[str(y)] = []
                series.setdefault("_errors", {})[str(y)] = str(e)
            time.sleep(0.4)  # be polite

    payload = {
        "keywords": keywords,
        "geo": geo,
        "years": years,
        "series": series,
    }
    _cache_set(key, payload)
    return payload


def fetch_long_range(
    keywords: list[str], start: str, end: str, geo: str = ""
) -> dict:
    """Single continuous range (used for Study 1 to compute YoY on aligned weekly series).
    `start`, `end` are YYYY-MM-DD."""
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        raise ValueError("at least one keyword required")

    key = _cache_key(keywords, [hash((start, end)) & 0xFFFFFFFF], geo) + "_long"
    cached = _cache_get(key)
    if cached:
        return cached

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        client.get("https://trends.google.com/trends/", headers=HEADERS)
        token, request = _explore_token(client, keywords, f"{start} {end}", geo)
        r2 = _get_with_retry(
            client,
            SINGLE_URL,
            {
                "hl": HL,
                "tz": str(TZ),
                "req": json.dumps(request, separators=(",", ":")),
                "token": token,
            },
        )
        timeline = (_strip_jsonp(r2.text).get("default") or {}).get("timelineData", [])

    points = []
    for entry in timeline:
        ts = entry.get("time")
        values = entry.get("value", [])
        if ts is None or not values:
            continue
        date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        points.append({"date": date, "value": values[0]})

    payload = {"keywords": keywords, "geo": geo, "start": start, "end": end, "series": points}
    _cache_set(key, payload)
    return payload
