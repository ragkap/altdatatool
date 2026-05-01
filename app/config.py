import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

STOCKS_DB = {
    "host": os.environ["STOCKS_DB_HOST"],
    "port": int(os.environ.get("STOCKS_DB_PORT", "5432")),
    "dbname": os.environ["STOCKS_DB_NAME"],
    "user": os.environ["STOCKS_DB_USER"],
    "password": os.environ["STOCKS_DB_PASSWORD"],
}

SMARTKARMA_API_TOKEN = os.environ.get("SMARTKARMA_API_TOKEN", "")
SMARTKARMA_API_EMAIL = os.environ.get("SMARTKARMA_API_EMAIL", "")

# Upstash Redis (REST). When unset, services fall back to local SQLite.
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# On Vercel/Lambda only /tmp is writable. Default cache path falls back to /tmp there.
_default_cache = "/tmp/cache.sqlite" if os.environ.get("VERCEL") else "./data/cache.sqlite"
_cache_raw = os.environ.get("CACHE_DB_PATH", _default_cache)
CACHE_DB_PATH = Path(_cache_raw) if _cache_raw.startswith("/") else (ROOT / _cache_raw)
CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
