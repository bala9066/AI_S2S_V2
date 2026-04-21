"""
Lightweight idempotent SQLite migrations for Hardware Pipeline V2.

We deliberately avoid Alembic — the schema is small, the app is single-tenant
SQLite, and we want zero new runtime dependencies. Migrations live as .sql
files in this package. `apply_all(db_path)` runs each migration if its
referenced columns/tables are missing; running it twice is a no-op.

Call order is controlled by `_MIGRATIONS` below (kept explicit, not discovered).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_HERE = Path(__file__).parent


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _apply_001(conn: sqlite3.Connection) -> bool:
    """001 — requirements lock columns on projects."""
    changed = False
    if not _table_exists(conn, "projects"):
        # Fresh DB: the ORM will create projects; nothing to ALTER yet.
        return False
    needed = [
        ("requirements_hash", "TEXT"),
        ("requirements_frozen_at", "DATETIME"),
        ("requirements_locked_json", "TEXT"),
    ]
    for col, ddl in needed:
        if not _column_exists(conn, "projects", col):
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
            changed = True
    return changed


def _apply_002(conn: sqlite3.Connection) -> bool:
    """002 — pipeline_runs + llm_calls tables."""
    changed = False
    if not _table_exists(conn, "pipeline_runs"):
        conn.execute("""
            CREATE TABLE pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                phase_id TEXT NOT NULL,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                status TEXT NOT NULL DEFAULT 'running',
                requirements_hash_at_run TEXT,
                model TEXT,
                model_version TEXT,
                total_tokens_in INTEGER DEFAULT 0,
                total_tokens_out INTEGER DEFAULT 0,
                wall_clock_ms INTEGER
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_runs_project "
            "ON pipeline_runs(project_id, phase_id)"
        )
        changed = True
    if not _table_exists(conn, "llm_calls"):
        conn.execute("""
            CREATE TABLE llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                model TEXT NOT NULL,
                model_version TEXT,
                temperature REAL,
                top_p REAL,
                prompt_sha256 TEXT,
                response_sha256 TEXT,
                tokens_in INTEGER,
                tokens_out INTEGER,
                latency_ms INTEGER,
                tool_calls_json TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_calls_run "
            "ON llm_calls(pipeline_run_id)"
        )
        changed = True
    return changed


def _apply_003(conn: sqlite3.Connection) -> bool:
    """003 — design_scope column on projects.

    Adds a non-null TEXT column with default 'full'. Existing rows get 'full'
    automatically (SQLite backfills DEFAULTs during ALTER TABLE ADD COLUMN).
    """
    if not _table_exists(conn, "projects"):
        return False
    if _column_exists(conn, "projects", "design_scope"):
        return False
    conn.execute(
        "ALTER TABLE projects ADD COLUMN design_scope TEXT NOT NULL DEFAULT 'full'"
    )
    return True


def _apply_004(conn: sqlite3.Connection) -> bool:
    """004 — project_type column on projects.

    Distinguishes receiver vs transmitter projects. Idempotent — existing
    rows get 'receiver' automatically on ALTER TABLE ADD COLUMN.
    """
    if not _table_exists(conn, "projects"):
        return False
    if _column_exists(conn, "projects", "project_type"):
        return False
    conn.execute(
        "ALTER TABLE projects ADD COLUMN project_type TEXT NOT NULL DEFAULT 'receiver'"
    )
    return True


_MIGRATIONS = [
    ("001_requirements_lock", _apply_001),
    ("002_pipeline_runs_llm_calls", _apply_002),
    ("003_design_scope", _apply_003),
    ("004_project_type", _apply_004),
]


def apply_all(db_path: str) -> dict[str, bool]:
    """
    Apply every migration that still has work to do. Returns a dict
    {migration_name: True_if_it_changed_something}.
    Safe to call on every FastAPI startup — idempotent.
    """
    results: dict[str, bool] = {}
    conn = sqlite3.connect(db_path)
    try:
        for name, fn in _MIGRATIONS:
            results[name] = fn(conn)
        conn.commit()
    finally:
        conn.close()
    return results
