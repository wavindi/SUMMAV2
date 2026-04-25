"""
SUMMAV3 — tiny SQLite wrapper for match persistence.

Stdlib only. Used by padel_backend.py to survive restarts (the last V1 bug
flagged in SUMMAV2/docs/migration-from-v1.md).

Schema is append-only: one row per completed match. The UI does not consume
this yet — the endpoint `GET /matches` exposes rows for debugging and future
UI work.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

_DB_PATH: Optional[str] = None
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("store.init_db() not called")
    conn = sqlite3.connect(_DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str) -> None:
    """Create tables if missing. Safe to call more than once."""
    global _DB_PATH
    _DB_PATH = path
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                ended_at   TEXT,
                winner     TEXT,
                sets_json  TEXT,
                stats_json TEXT,
                mode       TEXT
            )
            """
        )


def save_match(record: dict[str, Any]) -> int:
    """Persist one completed match. Returns the new row id."""
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO matches
              (started_at, ended_at, winner, sets_json, stats_json, mode)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("started_at"),
                record.get("ended_at"),
                json.dumps(record.get("winner")) if not isinstance(record.get("winner"), (str, type(None))) else record.get("winner"),
                record.get("sets_json") or "[]",
                record.get("stats_json") or "{}",
                record.get("mode"),
            ),
        )
        return int(cur.lastrowid)


def list_matches(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most-recent matches, newest first. Parses JSON columns."""
    limit = max(1, min(200, int(limit)))
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM matches ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Re-hydrate JSON fields so callers don't have to json.loads
        for key in ("sets_json", "stats_json"):
            try:
                d[key.removesuffix("_json")] = json.loads(d.get(key) or "null")
            except (TypeError, ValueError):
                d[key.removesuffix("_json")] = None
        # Winner may have been stored as a JSON blob or a plain string
        w = d.get("winner")
        if isinstance(w, str) and w.startswith("{"):
            try:
                d["winner"] = json.loads(w)
            except ValueError:
                pass
        out.append(d)
    return out
