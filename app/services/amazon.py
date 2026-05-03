"""Momentum Commerce Amazon branded-search volumes fetcher.

Public app at https://www.momentumcommerce.com/velocity/apps/amazon-search-trends.
The /api/branded-search/volumes endpoint requires a Laravel-style XSRF token
+ session cookie obtained by hitting the app page first."""
import hashlib
import json
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime

import httpx

from app.services import cache

APP_URL = "https://www.momentumcommerce.com/velocity/apps/amazon-search-trends"
VOLUMES_URL = "https://www.momentumcommerce.com/api/branded-search/volumes"
BRAND_TERMS_URL = "https://www.momentumcommerce.com/api/branded-search/brand-terms"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0 Safari/537.36"
)

CACHE_TTL = 60 * 60 * 24 * 7  # 7 days


def _bootstrap_session(client: httpx.Client) -> str:
    """Hit the public app page to populate XSRF-TOKEN + velocity_session cookies.
    Returns the URL-decoded XSRF token (to be sent as the x-xsrf-token header)."""
    r = client.get(APP_URL, headers={"user-agent": UA, "accept": "text/html"})
    r.raise_for_status()
    token = client.cookies.get("XSRF-TOKEN")
    if not token:
        raise RuntimeError("Momentum Commerce session bootstrap returned no XSRF token")
    return urllib.parse.unquote(token)


def _post_volumes(
    client: httpx.Client, xsrf: str, terms: list[str], start: str, end: str
) -> list[dict]:
    body = {"terms": terms, "start": start, "end": end}
    r = client.post(
        VOLUMES_URL,
        json=body,
        headers={
            "user-agent": UA,
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://www.momentumcommerce.com",
            "referer": f"{APP_URL}?term={urllib.parse.quote(terms[0])}",
            "x-xsrf-token": xsrf,
            "x-velocity-view": "apps.amazon-search-trends",
            "x-request-name": "volumes",
        },
    )
    r.raise_for_status()
    payload = r.json()
    return payload.get("data") or []


def _cache_key(terms: list[str], start: str, end: str) -> str:
    raw = json.dumps({"t": sorted(t.strip().lower() for t in terms), "s": start, "e": end}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def fetch_volumes(terms: list[str], start: str, end: str) -> dict:
    """Returns {"by_term": {term: [{date, volume}]}, "aggregated": [{date, volume}]}.
    Volumes are summed across all terms to a single monthly aggregate."""
    terms = [t.strip() for t in terms if t.strip()]
    if not terms:
        raise ValueError("at least one Amazon search term required")

    key = _cache_key(terms, start, end)
    cached = cache.get("amazon_volumes", key, CACHE_TTL)
    if cached:
        return cached

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        xsrf = _bootstrap_session(client)
        rows = _post_volumes(client, xsrf, terms, start, end)

    by_term: dict[str, list[dict]] = defaultdict(list)
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        term = row.get("search_term") or ""
        month = row.get("month")
        vol = row.get("volume")
        if not month or vol is None:
            continue
        # `month` is YYYY-MM-DD already (e.g. 2024-01-01)
        by_term[term].append({"date": month, "volume": float(vol)})
        totals[month] += float(vol)

    # Sort each term's series by date
    for term in by_term:
        by_term[term].sort(key=lambda p: p["date"])
    aggregated = [{"date": d, "volume": v} for d, v in sorted(totals.items())]

    payload = {
        "terms": terms,
        "start": start,
        "end": end,
        "by_term": dict(by_term),
        "aggregated": aggregated,
    }
    cache.set("amazon_volumes", key, payload, CACHE_TTL)
    return payload


def fetch_brand_terms(brand: str, limit: int = 50) -> list[dict]:
    """Returns Amazon search terms associated with a brand, sorted by rank
    (lowest rank first = most popular). Each item: {term, source, branded, rank}.
    Cached for 7 days per brand."""
    brand = (brand or "").strip()
    if not brand:
        return []

    key = "bt:" + hashlib.sha256(brand.lower().encode()).hexdigest()
    cached = cache.get("amazon_brand_terms", key, CACHE_TTL)
    if cached is None:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            xsrf = _bootstrap_session(client)
            r = client.post(
                BRAND_TERMS_URL,
                json={"brand": brand},
                headers={
                    "user-agent": UA,
                    "accept": "application/json",
                    "content-type": "application/json",
                    "origin": "https://www.momentumcommerce.com",
                    "referer": f"{APP_URL}?brand={urllib.parse.quote(brand)}",
                    "x-xsrf-token": xsrf,
                    "x-velocity-view": "apps.amazon-search-trends",
                    "x-request-name": "terms",
                },
            )
            r.raise_for_status()
            cached = r.json()
        cache.set("amazon_brand_terms", key, cached, CACHE_TTL)

    terms = cached.get("terms") or []
    # Sort by rank ascending (most popular first); rank may be missing
    terms = sorted(
        [t for t in terms if t.get("term")],
        key=lambda t: (t.get("rank") if t.get("rank") is not None else 1e9),
    )
    return terms[:limit]


def yoy(series: list[dict]) -> list[dict]:
    """Compute YoY % change on a monthly volume series.
    Input: [{date, volume}] sorted ascending by date.
    Output: [{date, volume, yoy}] for points where a year-prior point exists."""
    if not series:
        return []
    by_date = {p["date"]: p["volume"] for p in series}
    out = []
    for p in series:
        d = datetime.strptime(p["date"], "%Y-%m-%d")
        # Same month, previous year
        prev_d = d.replace(year=d.year - 1).strftime("%Y-%m-%d")
        prev = by_date.get(prev_d)
        if prev is None or prev == 0:
            continue
        yoy_pct = (p["volume"] / prev - 1.0) * 100.0
        out.append({"date": p["date"], "volume": p["volume"], "yoy": round(yoy_pct, 2)})
    return out
