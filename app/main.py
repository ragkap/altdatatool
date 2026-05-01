from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.services import prices, tickers, trends, wiki

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Alt-Data Analysis Tool")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")

STUDIES = [
    {"id": "1", "num": "01", "title": "Google Search Interest", "subtitle": ""},
    {"id": "2", "num": "02", "title": "Google Search Interest YoY", "subtitle": ""},
    {"id": "3", "num": "03", "title": "Wikipedia Pageviews", "subtitle": ""},
]


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

    try:
        tr = trends.fetch_long_range(kw_list, fetch_start, end, geo)
    except trends.TrendsRateLimited as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"trends: {e}")

    series = tr["series"]
    yoy: list[dict] = []
    if series:
        df = pd.DataFrame(series)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        # Trends weekly cadence -> 52-period shift for YoY
        prev = df["value"].shift(52)
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

    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, visible_start, end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"prices: {e}")

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "keywords": kw_list,
            "geo": geo,
            "range": {"start": visible_start, "end": end},
            "trends_raw": tr["series"],
            "trends_yoy": yoy,
            "prices": price_data,
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

    try:
        tr = trends.fetch_long_range(kw_list, start, end, geo)
    except trends.TrendsRateLimited as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"trends: {e}")

    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"prices: {e}")

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "keywords": kw_list,
            "geo": geo,
            "range": {"key": range.upper(), "start": start, "end": end},
            "trends": tr["series"],
            "prices": price_data,
        }
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
    try:
        pv = wiki.fetch_pageviews(titles, start, end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"wiki: {e}")

    try:
        price_data = prices.by_date_range(bloomberg_ticker, yahoo_ticker, start, end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"prices: {e}")

    return JSONResponse(
        {
            "bloomberg_ticker": bloomberg_ticker,
            "yahoo_ticker": yahoo_ticker,
            "titles": titles,
            "range": {"key": range.upper(), "start": start, "end": end},
            "pageviews": pv["aggregated"],
            "by_title": pv["by_title"],
            "prices": price_data,
        }
    )
