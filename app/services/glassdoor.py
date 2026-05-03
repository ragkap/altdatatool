"""Glassdoor reviews fetcher (RapidAPI 'glassdoor-real-time' provider).

Endpoints:
  GET /companies/auto-complete?query=<name>     -> resolve company name -> ID
  GET /companies/reviews?companyId=<id>&page=N  -> paginated reviews (20/page)

Each review carries a `reviewDateTime` so we can bucket into monthly aggregates
to plot avg rating, review count, and % recommend over time.

Quota: free RapidAPI tier is ~100 req/day. We page-cap to MAX_PAGES (default
10 = ~200 most recent reviews) and cache the full payload for 7 days."""
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

import httpx

from app.config import RAPIDAPI_KEY
from app.services import cache

HOST = "glassdoor-real-time.p.rapidapi.com"
AUTOCOMPLETE_URL = f"https://{HOST}/companies/auto-complete"
REVIEWS_URL = f"https://{HOST}/companies/reviews"

CACHE_TTL = 60 * 60 * 24 * 7  # 7 days
MAX_PAGES = 10                 # ~200 most recent reviews per company


class CompanyNotFound(Exception):
    """Glassdoor's autocomplete returned no candidates."""


def _headers() -> dict:
    if not RAPIDAPI_KEY:
        raise RuntimeError("RAPIDAPI_KEY not configured")
    return {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": HOST,
        "Content-Type": "application/json",
    }


def autocomplete(query: str, limit: int = 8) -> list[dict]:
    """Returns candidate companies matching `query` as
    [{id, name, industry, logo, ratings_count}]. Cached for 7 days per query."""
    q = (query or "").strip()
    if not q:
        return []

    cache_key = "ac:" + hashlib.sha256(q.lower().encode()).hexdigest()
    cached = cache.get("glassdoor", cache_key, CACHE_TTL)
    if cached is not None:
        return cached[:limit]

    with httpx.Client(timeout=15.0) as client:
        r = client.get(AUTOCOMPLETE_URL, params={"query": q}, headers=_headers())
    r.raise_for_status()
    payload = r.json() or {}
    raw = payload.get("data") or []

    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = item.get("id") or item.get("companyId")
        name = item.get("name") or item.get("shortName")
        if not cid or not name:
            continue
        out.append({
            "id": str(cid),
            "name": str(name),
            "industry": item.get("industry") or item.get("primaryIndustry") or "",
            "logo": item.get("squareLogoUrl") or item.get("logoUrl") or "",
            "review_count": item.get("reviewCount") or item.get("numberOfReviews"),
        })

    cache.set("glassdoor", cache_key, out, CACHE_TTL)
    return out[:limit]


def _fetch_reviews_page(client: httpx.Client, company_id: str, page: int) -> dict:
    params: dict[str, Any] = {"companyId": company_id}
    if page > 1:
        params["page"] = str(page)
    r = client.get(REVIEWS_URL, params=params, headers=_headers())
    r.raise_for_status()
    return r.json() or {}


def fetch_reviews(company_id: str, max_pages: int = MAX_PAGES) -> dict:
    """Returns aggregated monthly review stats for the company, plus the
    company's overall summary block.

    Output shape:
      {
        "company": {name, total_reviews, current_rating},
        "monthly": [
          {"month": "2024-09", "review_count": int, "avg_overall": float,
           "avg_recommend": float, "avg_business_outlook": float}
        ],
        "fetched_pages": int,
        "fetched_reviews": int,
      }

    Pages are fetched newest-first; we stop when no more pages or max_pages hit.
    """
    company_id = str(company_id or "").strip()
    if not company_id:
        raise ValueError("company_id required")

    cache_key = f"reviews:{company_id}:p{max_pages}"
    cached = cache.get("glassdoor", cache_key, CACHE_TTL)
    if cached is not None:
        return cached

    all_reviews: list[dict] = []
    company_summary: dict = {}
    fetched_pages = 0

    with httpx.Client(timeout=20.0) as client:
        # First page also gives us the company summary and total page count
        first = _fetch_reviews_page(client, company_id, 1)
        fetched_pages = 1
        data = (first or {}).get("data") or {}
        employer = data.get("employer") or {}
        rating = data.get("rating") or {}
        rd = data.get("reviewsData") or {}
        total_pages = int(rd.get("numberOfPages") or 1)
        all_reviews.extend(rd.get("reviews") or [])

        ratings = (rating or {}).get("ratings") or {}
        company_summary = {
            "name": employer.get("name") or "",
            "short_name": employer.get("shortName") or "",
            "industry_id": employer.get("primaryIndustryId"),
            "total_reviews": rd.get("filteredReviewsCount") or 0,
            "total_pages": total_pages,
            "current_overall": ratings.get("overallRating"),
            "current_recommend": ratings.get("recommendToFriendRating"),
            "current_business_outlook": ratings.get("businessOutlookRating"),
            "current_ceo": ratings.get("ceoRating"),
        }

        # Subsequent pages, capped
        page = 2
        while page <= min(total_pages, max_pages):
            try:
                pg = _fetch_reviews_page(client, company_id, page)
                fetched_pages += 1
                rev = ((pg or {}).get("data") or {}).get("reviewsData") or {}
                items = rev.get("reviews") or []
                if not items:
                    break
                all_reviews.extend(items)
            except Exception:
                # If pagination starts failing (rate limit, etc.), keep what we have
                break
            page += 1
            time.sleep(0.15)  # be gentle on the upstream

    # Bucket by YYYY-MM
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in all_reviews:
        ts = r.get("reviewDateTime")
        if not ts:
            continue
        try:
            month = ts[:7]  # 'YYYY-MM'
            datetime.strptime(month, "%Y-%m")  # validate
        except (ValueError, TypeError):
            continue
        buckets[month].append(r)

    def _avg(items: list[dict], key: str) -> float | None:
        vals = [i.get(key) for i in items if i.get(key) is not None]
        nums = [float(v) for v in vals if isinstance(v, (int, float))]
        if not nums:
            return None
        return round(sum(nums) / len(nums), 3)

    monthly = []
    for month in sorted(buckets.keys()):
        items = buckets[month]
        # Use the first day of the month as the date (for chart x-axis alignment)
        date = f"{month}-01"
        monthly.append({
            "date": date,
            "month": month,
            "review_count": len(items),
            "avg_overall": _avg(items, "ratingOverall"),
            "avg_recommend": _avg(items, "ratingRecommendToFriend"),
            "avg_business_outlook": _avg(items, "ratingBusinessOutlook"),
            "avg_culture": _avg(items, "ratingCultureAndValues"),
            "avg_work_life": _avg(items, "ratingWorkLifeBalance"),
        })

    payload = {
        "company": company_summary,
        "monthly": monthly,
        "fetched_pages": fetched_pages,
        "fetched_reviews": len(all_reviews),
    }
    cache.set("glassdoor", cache_key, payload, CACHE_TTL)
    return payload
