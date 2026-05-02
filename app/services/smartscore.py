from contextlib import contextmanager
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


def fetch(bloomberg_ticker: str, start: str, end: str) -> list[dict]:
    """Return [{date, score}] for the given ticker, filtered to [start, end].
    Empty list if the ticker has no SmartScore history."""
    if not bloomberg_ticker:
        return []
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT esss.report_date,
                   esss.country_and_sector_score AS smart_score
            FROM entity_smart_score_snapshots esss
            LEFT OUTER JOIN entities e ON e.id = esss.entity_id
            WHERE e.bloomberg_ticker = %s
              AND esss.report_date BETWEEN %s AND %s
            ORDER BY esss.report_date ASC
            """,
            (bloomberg_ticker, start, end),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for r in rows:
        score = r.get("smart_score")
        if score is None:
            continue
        out.append({
            "date": r["report_date"].isoformat(),
            "score": float(score),
        })
    return out
