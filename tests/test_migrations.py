"""Tests for migrations/__init__.py — idempotency and correctness."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from migrations import apply_all


def _make_projects_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()


def test_apply_all_adds_lock_columns():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        result = apply_all(db)
        assert result["001_requirements_lock"] is True

        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        conn.close()
        assert "requirements_hash" in cols
        assert "requirements_frozen_at" in cols
        assert "requirements_locked_json" in cols


def test_apply_all_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        apply_all(db)
        # Second run must not raise and must report no changes on 001.
        result = apply_all(db)
        assert result["001_requirements_lock"] is False


def test_apply_all_creates_pipeline_runs_and_llm_calls():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        apply_all(db)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "pipeline_runs" in tables
        assert "llm_calls" in tables


def test_apply_all_handles_missing_projects_table_gracefully():
    """If projects doesn't exist yet, 001 should no-op and 002 should still run."""
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        # No projects table created.
        result = apply_all(db)
        assert result["001_requirements_lock"] is False
        assert result["002_pipeline_runs_llm_calls"] is True
