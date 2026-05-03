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

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

# Upstash Redis (REST). When unset, services fall back to local SQLite.
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# On serverless (Vercel/Lambda) only /tmp is writable. Detect via env vars Vercel/Lambda set.
_is_serverless = bool(
    os.environ.get("VERCEL")
    or os.environ.get("VERCEL_ENV")
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
)
_default_cache = "/tmp/cache.sqlite" if _is_serverless else "./data/cache.sqlite"
_cache_raw = os.environ.get("CACHE_DB_PATH", _default_cache)
CACHE_DB_PATH = Path(_cache_raw) if _cache_raw.startswith("/") else (ROOT / _cache_raw)
try:
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except OSError:
    # Read-only filesystem (e.g. Lambda layer); fall back to /tmp.
    CACHE_DB_PATH = Path("/tmp/cache.sqlite")
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
