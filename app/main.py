from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.services import amazon, artifacts, consensus, glassdoor, prices, smartscore, tickers, trends, wiki

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Alt-Data Analysis Tool")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")

STUDIES = [
    {"id": "1", "num": "01", "title": "Google Search Interest", "subtitle": ""},
    {"id": "2", "num": "02", "title": "Google Search Interest YoY", "subtitle": ""},
    {"id": "3", "num": "03", "title": "Wikipedia Pageviews", "subtitle": ""},
    {"id": "4", "num": "04", "title": "Smartkarma SmartScore", "subtitle": ""},
    {"id": "5", "num": "05", "title": "Sellside Consensus", "subtitle": ""},
    {"id": "6", "num": "06", "title": "Amazon Search Trends", "subtitle": ""},
    {"id": "7", "num": "07", "title": "Amazon Search Trends YoY", "subtitle": ""},
    {"id": "8", "num": "08", "title": "Glassdoor Reviews", "subtitle": ""},
]
STUDY_LABELS = {s["id"]: s["title"] for s in STUDIES}


def _record_artifact(
    study_id: str,
    bloomberg_ticker: str,
    yahoo_ticker: str,
    params: dict,
    has_data: bool,
) -> None:
    """Best-effort artifact log. Never raises — failure shouldn't block the chart."""
    if not has_data:
        return
    try:
        # Look up the company name + slug for nicer artifact rows
        company = None
        for r in tickers._all_entities():
            if (r.get("bloomberg_ticker") or "") == bloomberg_ticker:
                company = r
                break
        slug = (company or {}).get("slug") or ""
        name = (company or {}).get("name") or bloomberg_ticker

        # Build a shareable URL with params encoded the same way the JS does
        from urllib.parse import urlencode
        query = {"ticker": slug} if slug else {}
        for k, v in params.items():
            if v is None or v == "":
                continue
            if isinstance(v, list):
                if not v:
                    continue
                query[k] = ",".join(str(x) for x in v)
            else:
                query[k] = str(v)
        study_url = f"/study/{study_id}?{urlencode(query)}" if query else f"/study/{study_id}"

        artifacts.record({
            "study_id": study_id,
            "study_label": STUDY_LABELS.get(study_id, f"Study {study_id}"),
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "company_name": name,
            "slug": slug,
            "study_url": study_url,
            "params": params,
        })
    except Exception:
        pass


@app.middleware("http")
async def inject_studies(request: Request, call_next):
    request.state.studies = STUDIES
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url=f"/study/{STUDIES[0]['id']}", status_code=302)


@app.get("/study/{study_id}", response_class=HTMLResponse)
def study_page(request: Request, study_id: str):
    if study_id not in {s["id"] for s in STUDIES}:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        f"study{study_id}.html",
        {"request": request, "study_id": study_id, "studies": STUDIES},
    )


@app.get("/artifacts", response_class=HTMLResponse)
def artifacts_page(request: Request):
    return templates.TemplateResponse(
        "artifacts.html",
        {"request": request, "studies": STUDIES},
    )


@app.get("/api/artifacts")
def api_artifacts(limit: int = 50, offset: int = 0):
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    return {"results": artifacts.list_recent(limit=limit, offset=offset)}


@app.get("/api/tickers")
def api_tickers(q: str = "", limit: int = 20):
    try:
        return {"results": tickers.search(q, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ticker/{slug}")
def api_ticker_by_slug(slug: str):
    row = tickers.get_by_slug(slug)
    if not row:
        raise HTTPException(status_code=404, detail="ticker not found")
    return row


@app.get("/api/prices")
def api_prices(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    start: str = Query(...),
    end: str = Query(...),
):
    return {
        "bloomberg_ticker": bloomberg_ticker,
        "yahoo_ticker": yahoo_ticker,
        "data": prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end),
    }


@app.get("/api/study2")
def api_study2_yoy(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    keywords: str = Query(..., description="comma-separated keywords"),
    years: int = Query(1, ge=1, le=8),
    geo: str = Query(""),
):
    """Search Interest YoY vs Share Price.

    Fetches Trends with one extra year back so YoY (52-week shift) is computable
    even when the user asks for 1Y. The output YoY + price series is sliced to
    the user-requested `years` window.
    """
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="keywords required")

    today = datetime.utcnow().date()
    visible_start_dt = today.replace(year=today.year - years)
    fetch_start_dt = today.replace(year=today.year - years - 1)
    visible_start = visible_start_dt.isoformat()
    fetch_start = fetch_start_dt.isoformat()
    end = today.isoformat()

    warnings: list[dict] = []

    series: list[dict] = []
    try:
        tr = trends.fetch_long_range(kw_list, fetch_start, end, geo)
        series = tr.get("series", []) or []
    except trends.TrendsRateLimited:
        warnings.append({
            "source": "Google Trends",
            "message": "Google rate-limited us. Search interest data is unavailable right now — please try again in a few minutes.",
        })
    except Exception:
        warnings.append({
            "source": "Google Trends",
            "message": "Couldn't load search interest data for these keywords. Try again in a moment, or adjust the keywords.",
        })

    yoy: list[dict] = []
    if series:
        df = pd.DataFrame(series)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        # Trends downsamples cadence based on range length:
        #   <=4 years -> weekly (so 1Y back = shift 52)
        #   >4 years  -> monthly (so 1Y back = shift 12)
        # Detect from the median spacing between consecutive timestamps.
        if len(df) >= 2:
            spacings = df["date"].diff().dt.days.dropna()
            median_days = float(spacings.median())
        else:
            median_days = 7.0
        shift_periods = 12 if median_days >= 20 else 52
        prev = df["value"].shift(shift_periods)
        df["yoy"] = (df["value"] / prev - 1.0) * 100.0
        df.loc[~prev.gt(0), "yoy"] = None  # avoid div-by-zero blowups
        for d, raw, y in zip(df["date"], df["value"], df["yoy"]):
            if pd.isna(y):
                continue
            date_str = d.strftime("%Y-%m-%d")
            if date_str < visible_start:
                continue
            yoy.append(
                {
                    "date": date_str,
                    "interest": float(raw),
                    "yoy": round(float(y), 2),
                }
            )

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, visible_start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="2",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"keywords": kw_list, "years": years},
        has_data=bool(yoy or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "keywords": kw_list,
            "geo": geo,
            "range": {"start": visible_start, "end": end},
            "trends_raw": series,
            "trends_yoy": yoy,
            "prices": price_data,
            "warnings": warnings,
        }
    )


RANGE_PRESETS = {"1Y": 1, "3Y": 3, "5Y": 5}


def _resolve_range(range_key: str) -> tuple[str, str]:
    today = datetime.utcnow().date()
    key = (range_key or "").upper()
    if key not in RANGE_PRESETS:
        raise HTTPException(status_code=400, detail=f"range must be one of {list(RANGE_PRESETS)}")
    start = today.replace(year=today.year - RANGE_PRESETS[key]).isoformat()
    return start, today.isoformat()


@app.get("/api/study1")
def api_study1_continuous(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    keywords: str = Query(...),
    range: str = Query("1Y", description="1Y | 3Y | 5Y"),
    geo: str = Query(""),
):
    """Search Interest vs Share Price (continuous range)."""
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="keywords required")

    start, end = _resolve_range(range)
    warnings: list[dict] = []

    trends_series: list[dict] = []
    try:
        tr = trends.fetch_long_range(kw_list, start, end, geo)
        trends_series = tr.get("series", []) or []
    except trends.TrendsRateLimited:
        warnings.append({
            "source": "Google Trends",
            "message": "Google rate-limited us. Search interest data is unavailable right now — please try again in a few minutes.",
        })
    except Exception:
        warnings.append({
            "source": "Google Trends",
            "message": "Couldn't load search interest data for these keywords. Try again in a moment, or adjust the keywords.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="1",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"keywords": kw_list, "range": range.upper()},
        has_data=bool(trends_series or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "keywords": kw_list,
            "geo": geo,
            "range": {"key": range.upper(), "start": start, "end": end},
            "trends": trends_series,
            "prices": price_data,
            "warnings": warnings,
        }
    )


@app.get("/api/amazon/brand-terms")
def api_amazon_brand_terms(brand: str = Query(...), limit: int = Query(50, ge=1, le=200)):
    try:
        return {"brand": brand, "terms": amazon.fetch_brand_terms(brand, limit=limit)}
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"No Amazon brand data for \"{brand}\". Try a different brand name.",
            )
        raise HTTPException(
            status_code=502,
            detail=f"Couldn't fetch Amazon brand terms right now. Please try again in a moment.",
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=f"Couldn't fetch Amazon brand terms right now. Please try again in a moment.",
        )


@app.get("/api/wiki/suggest")
def api_wiki_suggest(q: str = Query("", min_length=0), limit: int = Query(8, ge=1, le=15)):
    try:
        return {"results": wiki.suggest(q, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"wiki: {e}")


@app.get("/api/study3")
def api_study3(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    pages: str = Query(..., description="comma-separated Wikipedia page titles (max 5)"),
    range: str = Query("1Y", description="1Y | 3Y | 5Y"),
):
    """Wikipedia Pageviews vs Share Price.

    Sums daily pageviews across up to 5 Wikipedia titles and overlays on price."""
    titles = [p.strip() for p in pages.split(",") if p.strip()]
    if not titles:
        raise HTTPException(status_code=400, detail="at least one Wikipedia page required")
    if len(titles) > 5:
        raise HTTPException(status_code=400, detail="max 5 Wikipedia pages")

    start, end = _resolve_range(range)
    warnings: list[dict] = []

    aggregated: list[dict] = []
    by_title: dict = {}
    try:
        pv = wiki.fetch_pageviews(titles, start, end)
        aggregated = pv.get("aggregated", []) or []
        by_title = pv.get("by_title", {}) or {}
    except Exception:
        warnings.append({
            "source": "Wikipedia pageviews",
            "message": "Couldn't load pageviews for these articles. Check the page titles or try again shortly.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="3",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"pages": titles, "range": range.upper()},
        has_data=bool(aggregated or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "titles": titles,
            "range": {"key": range.upper(), "start": start, "end": end},
            "pageviews": aggregated,
            "by_title": by_title,
            "prices": price_data,
            "warnings": warnings,
        }
    )


@app.get("/api/study4")
def api_study4(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    range: str = Query("1Y", description="1Y | 3Y | 5Y"),
):
    """Smartkarma SmartScore vs Share Price."""
    if not bloomberg_ticker:
        raise HTTPException(status_code=400, detail="bloomberg_ticker required")

    start, end = _resolve_range(range)
    warnings: list[dict] = []

    smart: list[dict] = []
    try:
        smart = smartscore.fetch(bloomberg_ticker, start, end)
        if not smart:
            warnings.append({
                "source": "SmartScore",
                "message": f"No SmartScore snapshots found for {bloomberg_ticker} over this range.",
            })
    except Exception:
        warnings.append({
            "source": "SmartScore",
            "message": f"Couldn't load SmartScore data for {bloomberg_ticker}.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="4",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"range": range.upper()},
        has_data=bool(smart or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "range": {"key": range.upper(), "start": start, "end": end},
            "smart_score": smart,
            "prices": price_data,
            "warnings": warnings,
        }
    )


CONSENSUS_KEYS = {
    "sales": "Sales",
    "epsgaap": "EPS (GAAP)",
}


@app.get("/api/study5")
def api_study5(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    metric: str = Query("sales", description="sales | epsgaap"),
    range: str = Query("1Y", description="1Y | 3Y | 5Y"),
):
    """Sellside Consensus Estimates vs Share Price.

    User picks one of `sales` or `epsgaap` to plot at a time."""
    if metric not in CONSENSUS_KEYS:
        raise HTTPException(status_code=400, detail=f"metric must be one of {list(CONSENSUS_KEYS)}")

    start, end = _resolve_range(range)
    warnings: list[dict] = []

    # Look up the entity_id from the bloomberg ticker (consensus API uses Smartkarma's
    # internal entity-id, not the Bloomberg ticker)
    entity = None
    for row in tickers._all_entities():
        if (row.get("bloomberg_ticker") or "") == bloomberg_ticker:
            entity = row
            break
    if not entity or not entity.get("entity_id"):
        warnings.append({
            "source": "Consensus estimates",
            "message": f"No internal entity ID found for {bloomberg_ticker}.",
        })
        consensus_payload = {"series": [], "currency": None}
    else:
        try:
            consensus_payload = consensus.fetch(entity["entity_id"], metric, start, end)
        except Exception:
            warnings.append({
                "source": "Consensus estimates",
                "message": f"Couldn't load {CONSENSUS_KEYS[metric]} estimates for {bloomberg_ticker}.",
            })
            consensus_payload = {"series": [], "currency": None}

    if entity and entity.get("entity_id") and not consensus_payload["series"]:
        warnings.append({
            "source": "Consensus estimates",
            "message": f"No {CONSENSUS_KEYS[metric]} consensus data found for {bloomberg_ticker} over this range.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="5",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"metric": metric, "range": range.upper()},
        has_data=bool(consensus_payload["series"] or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "metric": metric,
            "metric_label": CONSENSUS_KEYS[metric],
            "currency": consensus_payload.get("currency"),
            "range": {"key": range.upper(), "start": start, "end": end},
            "estimates": consensus_payload["series"],
            "prices": price_data,
            "warnings": warnings,
        }
    )


@app.get("/api/study6")
def api_study6(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    terms: str = Query(..., description="comma-separated Amazon search terms"),
    range: str = Query("3Y", description="1Y | 3Y | 5Y"),
):
    """Amazon Search Trends vs Share Price.

    Volumes from Momentum Commerce branded-search API are summed across all
    terms (monthly cadence) and plotted against share price."""
    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    if not term_list:
        raise HTTPException(status_code=400, detail="at least one Amazon search term required")
    if len(term_list) > 50:
        raise HTTPException(status_code=400, detail="max 50 search terms")

    start, end = _resolve_range(range)

    warnings: list[dict] = []

    aggregated: list[dict] = []
    by_term: dict = {}
    try:
        vol = amazon.fetch_volumes(term_list, start, end)
        aggregated = vol.get("aggregated", []) or []
        by_term = vol.get("by_term", {}) or {}
    except Exception:
        warnings.append({
            "source": "Amazon Search Trends",
            "message": "Couldn't load Amazon search volumes for these terms. Try again in a moment, or adjust the terms.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="6",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"terms": term_list, "range": range.upper()},
        has_data=bool(aggregated or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "terms": term_list,
            "range": {"key": range.upper(), "start": start, "end": end},
            "volumes": aggregated,
            "by_term": by_term,
            "prices": price_data,
            "warnings": warnings,
        }
    )


@app.get("/api/study7")
def api_study7(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    terms: str = Query(..., description="comma-separated Amazon search terms"),
    range: str = Query("1Y", description="1Y | 3Y | 5Y"),
):
    """Amazon Search Trends YoY vs Share Price.

    Same volume source as Study 6, but plotted as YoY % change. Fetches one
    extra year of raw data so even 1Y produces a full year of YoY points."""
    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    if not term_list:
        raise HTTPException(status_code=400, detail="at least one Amazon search term required")
    if len(term_list) > 50:
        raise HTTPException(status_code=400, detail="max 50 search terms")

    range_years = RANGE_PRESETS.get((range or "").upper())
    if not range_years:
        raise HTTPException(status_code=400, detail=f"range must be one of {list(RANGE_PRESETS)}")

    today = datetime.utcnow().date()
    visible_start_dt = today.replace(year=today.year - range_years)
    fetch_start_dt = today.replace(year=today.year - range_years - 1)
    visible_start = visible_start_dt.isoformat()
    fetch_start = fetch_start_dt.isoformat()
    end = today.isoformat()

    warnings: list[dict] = []

    aggregated: list[dict] = []
    by_term: dict = {}
    yoy_series: list[dict] = []
    try:
        vol = amazon.fetch_volumes(term_list, fetch_start, end)
        aggregated = vol.get("aggregated", []) or []
        by_term = vol.get("by_term", {}) or {}
        yoy_full = amazon.yoy(aggregated)
        yoy_series = [p for p in yoy_full if p["date"] >= visible_start]
    except Exception:
        warnings.append({
            "source": "Amazon Search Trends",
            "message": "Couldn't load Amazon search volumes for these terms. Try again in a moment, or adjust the terms.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, visible_start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="7",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"terms": term_list, "range": range.upper()},
        has_data=bool(yoy_series or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "terms": term_list,
            "range": {"key": range.upper(), "start": visible_start, "end": end},
            "volumes_yoy": yoy_series,
            "volumes_raw": aggregated,
            "by_term": by_term,
            "prices": price_data,
            "warnings": warnings,
        }
    )


GLASSDOOR_METRICS = {
    "avg_overall": "Avg overall rating",
    "review_count": "Review count",
    "avg_recommend": "% recommend to friend",
    "avg_business_outlook": "% positive business outlook",
}


@app.get("/api/glassdoor/suggest")
def api_glassdoor_suggest(query: str = Query(..., min_length=1)):
    try:
        return {"results": glassdoor.autocomplete(query)}
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Couldn't search Glassdoor right now. Please try again in a moment.",
        )


@app.get("/api/study8")
def api_study8(
    bloomberg_ticker: str = Query(...),
    yahoo_ticker: str = Query(""),
    company_id: str = Query(..., description="Glassdoor company id (from /api/glassdoor/suggest)"),
    metric: str = Query("avg_overall", description=f"one of: {list(GLASSDOOR_METRICS)}"),
    range: str = Query("3Y", description="1Y | 3Y | 5Y"),
):
    """Glassdoor Reviews vs Share Price.

    Plots a chosen monthly review metric (avg rating / count / % recommend /
    % positive outlook) against share price."""
    if metric not in GLASSDOOR_METRICS:
        raise HTTPException(status_code=400, detail=f"metric must be one of {list(GLASSDOOR_METRICS)}")

    start, end = _resolve_range(range)
    warnings: list[dict] = []

    monthly: list[dict] = []
    company: dict = {}
    try:
        gd = glassdoor.fetch_reviews(company_id)
        company = gd.get("company") or {}
        # Slice monthly aggregates to the requested range
        monthly = [m for m in (gd.get("monthly") or []) if start <= m["date"] <= end]
        if not monthly:
            warnings.append({
                "source": "Glassdoor",
                "message": "No reviews found in this range — try a longer range, or pagination capped before reaching it.",
            })
    except Exception:
        warnings.append({
            "source": "Glassdoor",
            "message": "Couldn't load Glassdoor reviews right now. Please try again in a moment.",
        })

    price_data: list[dict] = []
    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception:
        warnings.append({
            "source": "Share price",
            "message": f"No share price data available for {bloomberg_ticker} over this range.",
        })

    _record_artifact(
        study_id="8",
        bloomberg_ticker=bloomberg_ticker,
        yahoo_ticker=yahoo_ticker,
        params={"company_id": company_id, "metric": metric, "range": range.upper()},
        has_data=bool(monthly or price_data),
    )

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "company_id": company_id,
            "company": company,
            "metric": metric,
            "metric_label": GLASSDOOR_METRICS[metric],
            "range": {"key": range.upper(), "start": start, "end": end},
            "monthly": monthly,
            "prices": price_data,
            "warnings": warnings,
        }
    )


