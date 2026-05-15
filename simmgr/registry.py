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
    _migrate_if_needed(conn)
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


def _migrate_if_needed(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'attempts'"
    ).fetchone()
    if existing is None:
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()}
    if "allocated_ram_mb" in columns or "max_rss_mb" in columns:
        _rebuild_attempts_table_with_gb(conn, columns)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()}
    if "max_rss_source" not in columns:
        conn.execute("ALTER TABLE attempts ADD COLUMN max_rss_source TEXT")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.execute(
        "INSERT INTO registry_metadata(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _rebuild_attempts_table_with_gb(conn: sqlite3.Connection, columns: set[str]) -> None:
    allocated_expr = "allocated_ram_gb"
    if "allocated_ram_gb" not in columns and "allocated_ram_mb" in columns:
        allocated_expr = "allocated_ram_mb / 1024.0"
    max_rss_expr = "max_rss_gb"
    if "max_rss_gb" not in columns and "max_rss_mb" in columns:
        max_rss_expr = "max_rss_mb / 1024.0"
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE attempts_new (
          attempt_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          param_set_id TEXT NOT NULL,
          replicate INTEGER NOT NULL,
          attempt INTEGER NOT NULL,
          status TEXT NOT NULL,
          plan_id TEXT,
          group_id TEXT,
          array_id TEXT,
          slurm_job_id TEXT,
          slurm_array_task_id TEXT,
          allocated_time_minutes INTEGER,
          allocated_ram_gb REAL,
          allocated_cpus INTEGER,
          attempt_log_path TEXT,
          created_at TEXT NOT NULL,
          submitted_at TEXT,
          started_at TEXT,
          ended_at TEXT,
          elapsed_seconds REAL,
          max_rss_gb REAL,
          max_rss_source TEXT,
          exit_code INTEGER,
          exit_reason TEXT,
          updated_at TEXT NOT NULL,
          UNIQUE(run_id, attempt),
          FOREIGN KEY(run_id) REFERENCES runs(run_id),
          FOREIGN KEY(param_set_id) REFERENCES param_sets(param_set_id)
        );
        """
    )
    conn.execute(
        f"""
        INSERT INTO attempts_new(
          attempt_id, run_id, param_set_id, replicate, attempt, status, plan_id, group_id,
          array_id, slurm_job_id, slurm_array_task_id, allocated_time_minutes,
          allocated_ram_gb, allocated_cpus, attempt_log_path, created_at, submitted_at,
          started_at, ended_at, elapsed_seconds, max_rss_gb, max_rss_source, exit_code, exit_reason, updated_at
        )
        SELECT attempt_id, run_id, param_set_id, replicate, attempt, status, plan_id, group_id,
          array_id, slurm_job_id, slurm_array_task_id, allocated_time_minutes,
          {allocated_expr}, allocated_cpus, attempt_log_path, created_at, submitted_at,
          started_at, ended_at, elapsed_seconds, {max_rss_expr}, NULL, exit_code, exit_reason, updated_at
        FROM attempts
        """
    )
    conn.execute("DROP TABLE attempts")
    conn.execute("ALTER TABLE attempts_new RENAME TO attempts")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_attempts_run_id ON attempts(run_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);
        CREATE INDEX IF NOT EXISTS idx_attempts_plan_id ON attempts(plan_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_slurm_job_id ON attempts(slurm_job_id);
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


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
