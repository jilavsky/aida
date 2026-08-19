from __future__ import annotations

import sqlite3
from pathlib import Path

from aida.persistence.db import CURRENT_SCHEMA_VERSION, connect


def test_connect_creates_db_file(tmp_path: Path):
    path = tmp_path / "aida.db"
    assert not path.exists()
    conn = connect(path)
    conn.close()
    assert path.exists()


def test_connect_creates_expected_tables(tmp_path: Path):
    conn = connect(tmp_path / "aida.db")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"conversations", "messages", "artifacts"} <= tables
    conn.close()


def test_connect_sets_user_version(tmp_path: Path):
    conn = connect(tmp_path / "aida.db")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_SCHEMA_VERSION
    conn.close()


def test_reopening_existing_db_is_a_no_op_migration(tmp_path: Path):
    path = tmp_path / "aida.db"
    conn1 = connect(path)
    conn1.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("c1", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn1.commit()
    conn1.close()

    # Reopening a DB already at CURRENT_SCHEMA_VERSION must not touch
    # existing data or re-run any CREATE TABLE (which would raise on an
    # already-existing table) — this is the "migration machinery proven,
    # even though v1 is the only real step today" acceptance criterion.
    conn2 = connect(path)
    row = conn2.execute("SELECT * FROM conversations WHERE id = 'c1'").fetchone()
    assert row is not None
    assert row["id"] == "c1"
    conn2.close()


def test_row_factory_allows_dict_like_access(tmp_path: Path):
    conn = connect(tmp_path / "aida.db")
    conn.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("c1", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM conversations").fetchone()
    assert row["id"] == "c1"
    assert isinstance(row, sqlite3.Row)
    conn.close()


def test_foreign_keys_enforced(tmp_path: Path):
    conn = connect(tmp_path / "aida.db")
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, seq, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("does-not-exist", 0, "user", "hi", "2026-01-01T00:00:00"),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "foreign key constraint should reject a message with no matching conversation"
    conn.close()
