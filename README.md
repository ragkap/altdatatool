# Alt-Data Analysis Tool

A Smartkarma tool for alt-data financial analysis. Each Study is a lens on a stock vs. its share price.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000

## Studies

1. **Search Interest YoY vs Share Price** — Google Trends YoY change overlaid on price.
2. **Search Interest by Year vs Share Price** — Per-year search interest series overlaid on price.
# altdatatool
