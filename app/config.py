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

CACHE_DB_PATH = ROOT / os.environ.get("CACHE_DB_PATH", "./data/cache.sqlite")
CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
