from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator
import psycopg2
import psycopg2.extras

from app.config import STOCKS_DB


@contextmanager
def _conn() -> Iterator[psycopg2.extensions.connection]:
    c = psycopg2.connect(**STOCKS_DB, connect_timeout=10)
    try:
        yield c
    finally:
        c.close()


@lru_cache(maxsize=1)
def _all_entities() -> list[dict]:
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id AS entity_id,
                   bloomberg_ticker,
                   yahoo_ticker,
                   slug,
                   pretty_name AS name,
                   market_status
            FROM entities
            """
        )
        return [dict(r) for r in cur.fetchall()]


def search(query: str, limit: int = 20) -> list[dict]:
    q = (query or "").strip().lower()
    rows = _all_entities()
    if not q:
        return rows[:limit]

    def score(r: dict) -> tuple:
        name = (r.get("name") or "").lower()
        bbg = (r.get("bloomberg_ticker") or "").lower()
        yh = (r.get("yahoo_ticker") or "").lower()
        slug = (r.get("slug") or "").lower()
        if name == q or bbg == q or yh == q:
            return (0, len(name))
        if name.startswith(q) or bbg.startswith(q) or yh.startswith(q):
            return (1, len(name))
        if q in name or q in bbg or q in yh or q in slug:
            return (2, len(name))
        return (9, 0)

    matched = [r for r in rows if score(r)[0] < 9]
    matched.sort(key=score)
    return matched[:limit]


def get_by_slug(slug: str) -> dict | None:
    s = slug.lower()
    for r in _all_entities():
        if (r.get("slug") or "").lower() == s:
            return r
    return None
