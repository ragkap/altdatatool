"""Smartkarma sellside consensus estimates fetcher."""
from datetime import datetime
import time

import httpx

from app.config import SMARTKARMA_API_TOKEN, SMARTKARMA_API_EMAIL

CONSENSUS_URL = "https://www.smartkarma.com/api/v3/consensus/graphs/estimates"
VALID_KEYS = {"sales", "epsgaap"}


def _auth_headers() -> dict:
    if not SMARTKARMA_API_TOKEN or not SMARTKARMA_API_EMAIL:
        raise RuntimeError("SMARTKARMA_API_TOKEN / SMARTKARMA_API_EMAIL not configured")
    auth = f'Token token="{SMARTKARMA_API_TOKEN}", email="{SMARTKARMA_API_EMAIL}"'
    return {
        "authorization": auth,
        "x-sk-authorization": auth,
        "accept": "application/json",
    }


# In-process TTL cache so repeated runs (different ranges) don't refetch
_CACHE: dict[tuple, tuple[float, dict]] = {}
_TTL = 60 * 30  # 30 min


def _cache_get(key: tuple) -> dict | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    fetched, payload = hit
    if time.time() - fetched > _TTL:
        return None
    return payload


def _cache_set(key: tuple, payload: dict) -> None:
    _CACHE[key] = (time.time(), payload)


def fetch(entity_id: int | str, key: str, start: str, end: str) -> dict:
    """Returns {"series": [{date, value}], "currency": str|None}.
    `start`, `end` are YYYY-MM-DD; the API returns daily points for the lookback,
    and we slice locally to [start, end] so the chart aligns with other studies."""
    if not entity_id:
        return {"series": [], "currency": None}
    if key not in VALID_KEYS:
        raise ValueError(f"key must be one of {VALID_KEYS}")

    # Smartkarma requires `date-from` as ISO with time portion. Use start.
    date_from = f"{start}T00:00:00.000Z"

    cache_key = (str(entity_id), key, start)
    cached = _cache_get(cache_key)
    if cached is None:
        params = {
            "entity-id": str(entity_id),
            "key": key,
            "date-from": date_from,
        }
        with httpx.Client(timeout=20.0) as client:
            r = client.get(CONSENSUS_URL, params=params, headers=_auth_headers())
        r.raise_for_status()
        cached = r.json()
        _cache_set(cache_key, cached)

    graph = cached.get("graph") or {}
    dates = graph.get("date") or []
    values = graph.get("value") or []
    currency = cached.get("currency")

    pairs = []
    for d, v in zip(dates, values):
        if v is None:
            continue
        # Filter to [start, end] (the API may return outside the requested range)
        if start <= d <= end:
            pairs.append({"date": d, "value": float(v)})
    pairs.sort(key=lambda p: p["date"])
    return {"series": pairs, "currency": currency}
