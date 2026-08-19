"""SQLite schema + connection management for ``~/.aida/aida.db``.

Schema versioning uses SQLite's own ``PRAGMA user_version`` — no need for a
bespoke version table. Migrations are a linear list of SQL scripts keyed by
the version they produce, applied in order from whatever version the
on-disk DB currently has up to ``CURRENT_SCHEMA_VERSION``. Phase 4 ships
schema v1 only; the migration *machinery* (not just a single step) is
proven by a v1->v1 no-op test — the next phase that needs a new column adds
version 2 here and the existing DBs upgrade transparently on next open.

Three tables (files stay on disk, never as DB blobs — PLAN.md's artifact
store already writes binaries under ``~/.aida/artifacts/``):

- ``conversations`` — one row per conversation, plus which workspace/profile
  it used and where its Markdown record lives.
- ``messages`` — every ``aida.providers.base.Message`` in a conversation, in
  order (``seq``), including tool-call requests (as JSON) and tool results.
- ``artifacts`` — metadata for every image/file artifact a tool call
  produced (kind/path/mime_type), linked to the conversation and the tool
  call that produced it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aida.config.paths import db_path

CURRENT_SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, str] = {
    # "IF NOT EXISTS" everywhere, deliberately: two connections can race to
    # be the first to ever open this DB file (e.g. the Phase 5 GUI starts a
    # session on a background thread while its own main thread independently
    # opens a ConversationStore for the sidebar) and both may read
    # PRAGMA user_version as 0 before either commits — idempotent DDL means
    # the loser of that race just re-declares the same schema instead of
    # crashing on "table already exists". See tests/ui/test_main_window.py's
    # concurrent-startup coverage.
    1: """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        title TEXT,
        workspace_name TEXT,
        profile_name TEXT,
        sidecar_dirname TEXT NOT NULL DEFAULT 'figures',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        record_path TEXT
    );

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        seq INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        tool_calls_json TEXT,
        tool_call_id TEXT,
        name TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(conversation_id, seq)
    );

    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        call_id TEXT,
        kind TEXT NOT NULL,
        path TEXT,
        mime_type TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, seq);
    CREATE INDEX IF NOT EXISTS idx_artifacts_conversation ON artifacts(conversation_id);
    """,
}


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (creating the file and/or migrating the schema if needed) the
    AIDA SQLite DB. Rows come back as ``sqlite3.Row`` (dict-like access by
    column name) rather than plain tuples."""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # The Phase 5 GUI opens more than one connection to this file from
    # different threads (a background session + the main thread's own
    # sidebar/cleanup queries) — a short busy timeout means a connection
    # that finds the file momentarily locked waits and retries instead of
    # raising "database is locked" immediately.
    conn.execute("PRAGMA busy_timeout = 5000")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
        script = _MIGRATIONS.get(version)
        if script is None:
            continue  # a version number with no schema change of its own
        conn.executescript(script)
        # PRAGMA doesn't accept bound parameters; `version` is an int from
        # our own fixed range, never user input.
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


__all__ = ["CURRENT_SCHEMA_VERSION", "connect"]
