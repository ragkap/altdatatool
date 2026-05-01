"""Tiny KV cache abstraction.

Uses Upstash Redis REST when configured, otherwise SQLite (local dev).
Keys are namespaced strings; values are JSON-serializable dicts.
"""
import json
import sqlite3
import time
from typing import Any

import httpx

from app.config import (
    CACHE_DB_PATH,
    UPSTASH_REDIS_REST_TOKEN,
    UPSTASH_REDIS_REST_URL,
)


def _use_redis() -> bool:
    return bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)


def _redis_get(key: str) -> dict | None:
    url = f"{UPSTASH_REDIS_REST_URL}/get/{key}"
    headers = {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}
    try:
        with httpx.Client(timeout=4.0) as c:
            r = c.get(url, headers=headers)
        if r.status_code != 200:
            return None
        result = r.json().get("result")
        if not result:
            return None
        return json.loads(result)
    except Exception:
        return None


def _redis_set(key: str, payload: dict, ttl_seconds: int) -> None:
    url = f"{UPSTASH_REDIS_REST_URL}/set/{key}?EX={ttl_seconds}"
    headers = {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}
    body = json.dumps(payload)
    try:
        with httpx.Client(timeout=4.0) as c:
            c.post(url, headers=headers, content=body)
    except Exception:
        pass


def _sqlite_init(table: str) -> sqlite3.Connection:
    c = sqlite3.connect(CACHE_DB_PATH)
    c.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        )
        """
    )
    return c


def _sqlite_get(table: str, key: str, ttl_seconds: int) -> dict | None:
    with _sqlite_init(table) as c:
        row = c.execute(
            f"SELECT payload, fetched_at FROM {table} WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    payload, fetched_at = row
    if time.time() - fetched_at > ttl_seconds:
        return None
    return json.loads(payload)


def _sqlite_set(table: str, key: str, payload: dict) -> None:
    with _sqlite_init(table) as c:
        c.execute(
            f"INSERT OR REPLACE INTO {table} (key, payload, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload), int(time.time())),
        )


def get(namespace: str, key: str, ttl_seconds: int) -> dict | None:
    full = f"{namespace}:{key}"
    if _use_redis():
        return _redis_get(full)
    return _sqlite_get(namespace, key, ttl_seconds)


def set(namespace: str, key: str, payload: dict, ttl_seconds: int) -> None:
    full = f"{namespace}:{key}"
    if _use_redis():
        _redis_set(full, payload, ttl_seconds)
        return
    _sqlite_set(namespace, key, payload)
