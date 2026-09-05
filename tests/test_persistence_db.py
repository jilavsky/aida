from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from aida.persistence.db import _MIGRATIONS, CURRENT_SCHEMA_VERSION, connect


def test_connect_creates_db_file(tmp_path: Path):
    path = tmp_path / "aida.db"
    assert not path.exists()
    conn = connect(path)
    conn.close()
    assert path.exists()


def test_connect_creates_expected_tables(tmp_path: Path):
    conn = connect(tmp_path / "aida.db")
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"conversations", "messages", "artifacts", "schedule_runs"} <= tables
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


def test_concurrent_first_connect_from_two_threads_does_not_raise_database_is_locked(
    tmp_path: Path,
):
    """Real-use bug: the GUI opens the first-ever connection to a fresh DB
    file from two threads at once (MainWindow.__init__ starts a session on
    the background loop thread while its own Qt-thread constructor opens a
    ConversationStore for the sidebar) — both see PRAGMA user_version == 0
    and race to run the migration. Windows CI hit this as a real
    sqlite3.OperationalError: database is locked. connect()'s in-process
    _migrate_lock (aida/persistence/db.py) must serialize the race away."""
    path = tmp_path / "aida.db"
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def _connect_and_close() -> None:
        barrier.wait(timeout=5.0)
        try:
            conn = connect(path)
            conn.close()
        except Exception as exc:  # noqa: BLE001 - captured to fail the test explicitly, not to swallow it
            errors.append(exc)

    threads = [threading.Thread(target=_connect_and_close) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"concurrent first connect() raised: {errors}"


def test_artifacts_table_has_a_seq_column(tmp_path: Path):
    """U6(b): "add a seq/message anchor to artifact records at write time
    so resumed images can interleave at their original positions" —
    schema v2."""
    conn = connect(tmp_path / "aida.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(artifacts)")}
    assert "seq" in columns
    conn.close()


def test_migrating_from_v1_adds_artifacts_seq_column_without_data_loss(tmp_path: Path):
    """Builds a genuine v1-only DB by hand (bypassing connect(), which
    always migrates straight to CURRENT_SCHEMA_VERSION) so the v1->v2 step
    itself — not just "works on a brand new DB" — is proven additive: an
    existing artifact row survives with seq defaulting to NULL."""
    path = tmp_path / "aida.db"
    raw = sqlite3.connect(path)
    raw.executescript(_MIGRATIONS[1])
    raw.execute("PRAGMA user_version = 1")
    raw.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("c1", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    raw.execute(
        "INSERT INTO artifacts (id, conversation_id, kind, created_at) VALUES (?, ?, ?, ?)",
        ("a1", "c1", "ImageArtifact", "2026-01-01T00:00:00"),
    )
    raw.commit()
    raw.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    row = conn.execute("SELECT * FROM artifacts WHERE id = 'a1'").fetchone()
    assert row["id"] == "a1"  # pre-migration row survived
    assert row["seq"] is None  # new column, no data for a pre-existing row
    conn.close()


def test_conversations_table_has_an_origin_column(tmp_path: Path):
    """Phase 10: distinguishes an interactive chat conversation (``origin``
    NULL) from one ``aida.core.workflows.run_workflow`` created
    (``"workflow"``/``"schedule"``) — schema v3."""
    conn = connect(tmp_path / "aida.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
    assert "origin" in columns
    conn.close()


def test_connect_creates_schedule_runs_table(tmp_path: Path):
    conn = connect(tmp_path / "aida.db")
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "schedule_runs" in tables
    conn.close()


def test_migrating_from_v2_adds_origin_column_and_schedule_runs_table(tmp_path: Path):
    """Same "build a genuine old-version DB by hand" pattern as the v1->v2
    test above, proving the v2->v3 step itself is additive: an existing
    conversation row survives with ``origin`` defaulting to NULL."""
    path = tmp_path / "aida.db"
    raw = sqlite3.connect(path)
    raw.executescript(_MIGRATIONS[1])
    raw.executescript(_MIGRATIONS[2])
    raw.execute("PRAGMA user_version = 2")
    raw.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("c1", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    raw.commit()
    raw.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    row = conn.execute("SELECT * FROM conversations WHERE id = 'c1'").fetchone()
    assert row["id"] == "c1"  # pre-migration row survived
    assert row["origin"] is None  # new column, no data for a pre-existing row
    conn.execute(
        "INSERT INTO schedule_runs (schedule_name, fired_at, status) VALUES (?, ?, ?)",
        ("nightly-report", "2026-01-01T00:00:00", "ok"),
    )
    conn.commit()
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
