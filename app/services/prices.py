from datetime import datetime
import time

import httpx

from app.config import SMARTKARMA_API_TOKEN, SMARTKARMA_API_EMAIL

CHART_URL = "https://www.smartkarma.com/api/v2/price-api/get-chart"


def _auth_headers() -> dict:
    if not SMARTKARMA_API_TOKEN or not SMARTKARMA_API_EMAIL:
        raise RuntimeError("SMARTKARMA_API_TOKEN / SMARTKARMA_API_EMAIL not configured")
    auth = f'Token token="{SMARTKARMA_API_TOKEN}", email="{SMARTKARMA_API_EMAIL}"'
    return {
        "authorization": auth,
        "x-sk-authorization": auth,
        "accept": "application/json",
    }


# Tiny in-memory TTL cache so repeated study runs (different keywords) don't refetch
_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_TTL = 60 * 30  # 30 min


def _cache_get(key: tuple) -> list[dict] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    fetched, payload = hit
    if time.time() - fetched > _TTL:
        return None
    return payload


def _cache_set(key: tuple, payload: list[dict]) -> None:
    _CACHE[key] = (time.time(), payload)


def fetch_chart(bloomberg_ticker: str, yahoo_ticker: str, interval: str) -> list[dict]:
    """Returns a list of {date: 'YYYY-MM-DD', close: float} from Smartkarma.
    `yahoo_ticker` may be empty for tickers that only have a Bloomberg code."""
    if not bloomberg_ticker:
        return []

    key = (bloomberg_ticker, yahoo_ticker, interval)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    params = {
        "ticker": bloomberg_ticker,
        "yahoo_ticker": yahoo_ticker,
        "interval": interval,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.get(CHART_URL, params=params, headers=_auth_headers())
    r.raise_for_status()
    data = r.json()
    times = data.get("time_period") or []
    closes = data.get("close") or []
    out: list[dict] = []
    for t, c in zip(times, closes):
        if c is None:
            continue
        # ISO timestamp -> YYYY-MM-DD
        date = t[:10] if isinstance(t, str) else None
        if not date:
            continue
        out.append({"date": date, "close": float(c)})

    _cache_set(key, out)
    return out


def _pick_interval(start: str, end: str) -> str:
    """Smartkarma intervals are lookbacks from today: m3, y1, y5.
    Pick the smallest one whose lookback window starts on/before `start`.
    `end` is informational; we always slice locally."""
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
    except ValueError:
        return "y5"
    today = datetime.utcnow()
    days_back = (today - start_dt).days
    if days_back <= 90:
        return "m3"
    if days_back <= 365:
        return "y1"
    return "y5"


def by_date_range(
    bloomberg_ticker: str,
    yahoo_ticker: str,
    start: str,
    end: str,
) -> list[dict]:
    """Fetch a chart at appropriate interval and slice to [start, end]."""
    interval = _pick_interval(start, end)
    rows = fetch_chart(bloomberg_ticker, yahoo_ticker, interval)
    if not rows:
        return []
    return [r for r in rows if start <= r["date"] <= end]
