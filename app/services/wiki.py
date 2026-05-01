import hashlib
import json
import time
from datetime import datetime
from urllib.parse import quote

import httpx

from app.services import cache

SUGGEST_URL = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
HEADERS = {
    "User-Agent": "AltDataPlatform/1.0 (rk@smartkarma.com)",
    "Accept": "application/json",
}
CACHE_TTL = 60 * 60 * 24 * 7  # 7 days


def _cache_get(key: str) -> dict | None:
    return cache.get("wiki_cache", key, CACHE_TTL)


def _cache_set(key: str, payload: dict) -> None:
    cache.set("wiki_cache", key, payload, CACHE_TTL)


def suggest(q: str, limit: int = 8) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    params = {
        "action": "query",
        "list": "prefixsearch",
        "format": "json",
        "pssearch": q,
        "pslimit": str(limit),
        "cirrusUseCompletionSuggester": "yes",
    }
    with httpx.Client(timeout=10.0) as c:
        r = c.get(SUGGEST_URL, params=params, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    return [
        {"title": p["title"], "pageid": p.get("pageid")}
        for p in data.get("query", {}).get("prefixsearch", [])
    ]


def _fetch_article_daily(
    client: httpx.Client, title: str, start: str, end: str
) -> list[dict]:
    """`title` should be the URL-form (spaces -> underscores).
    `start`, `end` are YYYY-MM-DD; converted to YYYYMMDD00 timestamps for the API."""
    s = start.replace("-", "") + "00"
    e = end.replace("-", "") + "00"
    article = quote(title.replace(" ", "_"), safe="")
    url = f"{PAGEVIEWS_BASE}/en.wikipedia/all-access/user/{article}/daily/{s}/{e}"
    r = client.get(url, headers=HEADERS)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        ts = it.get("timestamp", "")
        # YYYYMMDDHH -> YYYY-MM-DD
        if len(ts) >= 8:
            d = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
            out.append({"date": d, "views": int(it.get("views", 0))})
    return out


def fetch_pageviews(
    titles: list[str], start: str, end: str
) -> dict:
    """Fetch daily pageviews for each title and an aggregated (summed) series.

    Returns:
      {
        "titles": [...],
        "start": "...", "end": "...",
        "by_title": {"Pop_Mart": [{date, views}], ...},
        "aggregated": [{date, views}],   # sum across all titles per date
      }
    """
    titles = [t.strip() for t in titles if t.strip()]
    if not titles:
        raise ValueError("at least one Wikipedia title required")

    raw = json.dumps({"t": sorted(titles), "s": start, "e": end}, sort_keys=True)
    key = "pv:" + hashlib.sha256(raw.encode()).hexdigest()
    cached = _cache_get(key)
    if cached:
        return cached

    by_title: dict[str, list[dict]] = {}
    with httpx.Client(timeout=30.0) as client:
        for t in titles:
            try:
                by_title[t] = _fetch_article_daily(client, t, start, end)
            except Exception as e:
                by_title[t] = []
                by_title.setdefault("_errors", {})  # type: ignore
            time.sleep(0.15)  # be polite

    # Aggregate by date
    totals: dict[str, int] = {}
    for series in by_title.values():
        if not isinstance(series, list):
            continue
        for pt in series:
            totals[pt["date"]] = totals.get(pt["date"], 0) + int(pt["views"])
    aggregated = [{"date": d, "views": v} for d, v in sorted(totals.items())]

    payload = {
        "titles": titles,
        "start": start,
        "end": end,
        "by_title": by_title,
        "aggregated": aggregated,
    }
    _cache_set(key, payload)
    return payload
