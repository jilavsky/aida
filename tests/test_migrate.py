"""Transactional schema migrations (review item B6)."""

from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

from aida.knowledge.rag import index as kb_index
from aida.persistence import db as conv_db
from aida.persistence.migrate import apply_migrations, split_sql_script

MIGRATIONS = {
    1: "CREATE TABLE a (id INTEGER PRIMARY KEY);",
    2: "ALTER TABLE a ADD COLUMN name TEXT;\nCREATE INDEX idx_a_name ON a(name);",
}


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_split_keeps_trigger_bodies_and_string_semicolons_whole():
    script = """
    CREATE TABLE t (x TEXT DEFAULT 'a;b');
    CREATE TRIGGER trg AFTER INSERT ON t BEGIN
        UPDATE t SET x = 'c;d';
        DELETE FROM t WHERE x = 'e';
    END;
    INSERT INTO t VALUES ('f')
    """
    statements = split_sql_script(script)
    assert len(statements) == 3
    assert statements[1].startswith("CREATE TRIGGER") and statements[1].endswith("END;")
    assert statements[2] == "INSERT INTO t VALUES ('f')"


def test_migrates_fresh_database_to_target(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    apply_migrations(conn, MIGRATIONS, 2, database_label="test db")
    assert _version(conn) == 2
    assert _columns(conn, "a") == ["id", "name"]
    assert not conn.in_transaction


def test_failed_migration_rolls_back_schema_and_version(tmp_path):
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    apply_migrations(conn, {1: MIGRATIONS[1]}, 1, database_label="test db")
    broken = {**MIGRATIONS, 2: "ALTER TABLE a ADD COLUMN name TEXT;\nTHIS IS NOT SQL;"}
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn, broken, 2, database_label="test db")
    # Neither the column nor the version bump survived the failure, so the
    # next start retries version 2 cleanly instead of hitting "duplicate column".
    assert _version(conn) == 1
    assert _columns(conn, "a") == ["id"]
    apply_migrations(conn, MIGRATIONS, 2, database_label="test db")
    assert _columns(conn, "a") == ["id", "name"]


def test_second_connection_skips_a_migration_another_already_ran(tmp_path):
    path = tmp_path / "x.db"
    first = sqlite3.connect(path)
    second = sqlite3.connect(path)
    apply_migrations(first, {1: MIGRATIONS[1]}, 1, database_label="test db")
    apply_migrations(second, {1: MIGRATIONS[1]}, 1, database_label="test db")
    # Both connections are open when `first` migrates; `second` must notice.
    apply_migrations(first, MIGRATIONS, 2, database_label="test db")
    apply_migrations(second, MIGRATIONS, 2, database_label="test db")
    assert _version(second) == 2


def test_concurrent_processes_style_race_does_not_duplicate_column(tmp_path):
    path = tmp_path / "x.db"
    seed = sqlite3.connect(path)
    apply_migrations(seed, {1: MIGRATIONS[1]}, 1, database_label="test db")
    seed.close()

    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker():
        conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        try:
            barrier.wait()
            apply_migrations(conn, MIGRATIONS, 2, database_label="test db")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    check = sqlite3.connect(path)
    assert _version(check) == 2
    assert _columns(check, "a") == ["id", "name"]


def test_newer_database_is_left_alone_with_a_warning(tmp_path, caplog):
    conn = sqlite3.connect(tmp_path / "x.db")
    conn.execute("PRAGMA user_version = 9")
    with caplog.at_level(logging.WARNING, logger="aida.persistence.migrate"):
        apply_migrations(conn, MIGRATIONS, 2, database_label="Conversation database")
    assert _version(conn) == 9
    assert "newer than this AIDA" in caplog.text
    assert "Conversation database" in caplog.text


def test_restores_the_connections_isolation_level(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    before = conn.isolation_level
    apply_migrations(conn, MIGRATIONS, 2, database_label="test db")
    assert conn.isolation_level == before


def test_real_schemas_migrate_from_scratch(tmp_path):
    conv = conv_db.connect(tmp_path / "aida.db")
    assert _version(conv) == conv_db.CURRENT_SCHEMA_VERSION
    kb = kb_index.connect(tmp_path / "kb" / "index.db")
    assert _version(kb) == kb_index.CURRENT_SCHEMA_VERSION
