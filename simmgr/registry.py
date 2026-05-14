from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .registry_schema import SCHEMA_SQL, SCHEMA_VERSION
from .time_utils import utc_now


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize(path: str | Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        now = utc_now()
        defaults = {
            "schema_version": str(SCHEMA_VERSION),
            "created_at": now,
            "updated_at": now,
            "simmgr_version": __version__,
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO registry_metadata(key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def row(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    value = conn.execute(sql, params).fetchone()
    return dict(value) if value is not None else None


def update_metadata(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO registry_metadata(key, value) VALUES ('updated_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (utc_now(),),
    )
    conn.execute(
        "INSERT INTO registry_metadata(key, value) VALUES ('simmgr_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (__version__,),
    )


def refresh_run_summary(conn: sqlite3.Connection, run_id: str) -> None:
    attempts = rows(
        conn,
        "SELECT * FROM attempts WHERE run_id = ? ORDER BY attempt",
        (run_id,),
    )
    now = utc_now()
    if not attempts:
        conn.execute(
            "UPDATE runs SET status = 'pending', attempt_count = 0, best_attempt_id = NULL, updated_at = ? WHERE run_id = ?",
            (now, run_id),
        )
        return
    successes = [a for a in attempts if a["status"] == "succeeded"]
    best = successes[-1] if successes else attempts[-1]
    conn.execute(
        "UPDATE runs SET status = ?, attempt_count = ?, best_attempt_id = ?, updated_at = ? WHERE run_id = ?",
        (best["status"], len(attempts), best["attempt_id"], now, run_id),
    )

