"""Artifacts: a public log of every study run, for discovery.

Backed by Upstash Redis (sorted set scored by epoch ms) when configured,
with a SQLite fallback for local dev. Deduplicated within a 1-hour window
on (study_id, ticker, params)."""
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import (
    CACHE_DB_PATH,
    UPSTASH_REDIS_REST_TOKEN,
    UPSTASH_REDIS_REST_URL,
)

ZSET_KEY = "artifacts:list"
DEDUP_PREFIX = "artifacts:seen:"
MAX_ITEMS = 500
DEDUP_WINDOW = 60 * 60  # 1 hour


def _use_redis() -> bool:
    return bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)


def _redis_headers() -> dict:
    return {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}


def _dedup_key(payload: dict) -> str:
    canonical = json.dumps(
        {
            "s": payload.get("study_id"),
            "t": payload.get("bloomberg_ticker"),
            "p": payload.get("params") or {},
        },
        sort_keys=True,
    )
    return DEDUP_PREFIX + hashlib.sha256(canonical.encode()).hexdigest()


def _sqlite_init() -> sqlite3.Connection:
    c = sqlite3.connect(CACHE_DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            ts_ms INTEGER NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts_seen (
            dedup_key TEXT PRIMARY KEY,
            seen_at INTEGER NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_ts ON artifacts(ts_ms DESC)")
    return c


def _redis_pipeline(commands: list[list[str]]) -> Any:
    with httpx.Client(timeout=5.0) as c:
        r = c.post(
            f"{UPSTASH_REDIS_REST_URL}/pipeline",
            headers=_redis_headers(),
            json=commands,
        )
        r.raise_for_status()
        return r.json()


def record(payload: dict) -> bool:
    """Record a new artifact. Returns True if written, False if deduped.
    `payload` should include: study_id, study_label, bloomberg_ticker,
    company_name, slug, study_url, params (dict)."""
    now_ms = int(time.time() * 1000)
    payload = {**payload, "ts_ms": now_ms, "ts": datetime.now(timezone.utc).isoformat()}
    member = json.dumps(payload, sort_keys=True)
    seen_key = _dedup_key(payload)

    if _use_redis():
        try:
            # SETNX with TTL via SET ... NX EX
            cmds = [
                ["SET", seen_key, "1", "NX", "EX", str(DEDUP_WINDOW)],
            ]
            res = _redis_pipeline(cmds)
            ok = res and res[0].get("result") in ("OK", 1, "1")
            if not ok:
                return False
            _redis_pipeline([
                ["ZADD", ZSET_KEY, str(now_ms), member],
                # Keep the set bounded — drop oldest beyond MAX_ITEMS
                ["ZREMRANGEBYRANK", ZSET_KEY, "0", str(-MAX_ITEMS - 1)],
            ])
            return True
        except Exception:
            # Fall through to SQLite if Redis fails
            pass

    with _sqlite_init() as c:
        # Dedup
        row = c.execute(
            "SELECT seen_at FROM artifacts_seen WHERE dedup_key = ?", (seen_key,)
        ).fetchone()
        if row and (time.time() - row[0]) < DEDUP_WINDOW:
            return False
        c.execute(
            "INSERT OR REPLACE INTO artifacts_seen (dedup_key, seen_at) VALUES (?, ?)",
            (seen_key, int(time.time())),
        )
        c.execute("INSERT INTO artifacts (ts_ms, payload) VALUES (?, ?)", (now_ms, member))
        # Trim to MAX_ITEMS
        c.execute(
            """
            DELETE FROM artifacts
            WHERE ts_ms NOT IN (
              SELECT ts_ms FROM artifacts ORDER BY ts_ms DESC LIMIT ?
            )
            """,
            (MAX_ITEMS,),
        )
    return True


def list_recent(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return newest-first artifacts."""
    if _use_redis():
        try:
            cmd = [
                "ZRANGE", ZSET_KEY,
                str(offset), str(offset + limit - 1),
                "REV",
            ]
            res = _redis_pipeline([cmd])
            members = (res and res[0].get("result")) or []
            out = []
            for m in members:
                try:
                    out.append(json.loads(m))
                except Exception:
                    continue
            return out
        except Exception:
            pass

    with _sqlite_init() as c:
        rows = c.execute(
            "SELECT payload FROM artifacts ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    out = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out
