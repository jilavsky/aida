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
import threading
from pathlib import Path

from aida.config.paths import db_path

CURRENT_SCHEMA_VERSION = 5

# The Phase 5 GUI opens the first-ever connection to a fresh DB file from
# two threads at once (MainWindow.__init__ starts a session on the
# background loop thread, then immediately opens its own ConversationStore
# for the sidebar on the Qt thread) — both can see `PRAGMA user_version`
# as 0 and race to run the migration's CREATE TABLE statements. The
# migrations are idempotent (IF NOT EXISTS) so the race is harmless
# *content*-wise, but was still observed to raise "database is locked" on
# Windows CI (busy_timeout alone wasn't a reliable enough guard against two
# threads in the same process serializing through SQLite's file lock).
# A plain in-process lock removes the race outright: only one thread ever
# runs `_migrate` at a time, regardless of platform lock timing.
_migrate_lock = threading.Lock()

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
    # U6(b): "Better resumed-conversation rendering... add a seq/message
    # anchor to artifact records at write time so resumed images can
    # interleave at their original positions." NULL for every row written
    # before this migration (and for the rare case a caller doesn't know
    # the owning message's seq yet) — the resume-rendering path falls back
    # to appending those at the end of the transcript, exactly like v1's
    # behavior, so old conversations keep working, just without
    # interleaving for artifacts recorded before the upgrade.
    2: """
    ALTER TABLE artifacts ADD COLUMN seq INTEGER;
    CREATE INDEX IF NOT EXISTS idx_artifacts_conversation_seq ON artifacts(conversation_id, seq);
    """,
    # Phase 10: workflow/schedule runs (planning/phase10_scheduling_design.md
    # §4). `conversations.origin` is NULL for every interactive chat
    # conversation ever created (unchanged behavior) and "workflow" /
    # "schedule" for one created by aida.core.workflows.run_workflow — lets
    # the GUI sidebar and `aida conversations list` distinguish them without
    # a join. `schedule_runs` is last-fired/status bookkeeping for the
    # scheduler (deliberately NOT part of schedules.yaml — see the design
    # doc's §5 rationale: user-edited config and machine-written run history
    # should not be the same file).
    3: """
    ALTER TABLE conversations ADD COLUMN origin TEXT;

    CREATE TABLE IF NOT EXISTS schedule_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_name TEXT NOT NULL,
        fired_at TEXT NOT NULL,
        status TEXT NOT NULL,
        conversation_id TEXT REFERENCES conversations(id),
        error TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_schedule_runs_name ON schedule_runs(schedule_name, fired_at);
    """,
    # The "user" organization axis (planning/multiuser_plan.md). NULL for
    # every conversation created before this column existed and for every
    # install that never sets an active user — which is why the filtering
    # side treats NULL as "visible to everyone" rather than "belongs to
    # nobody": on upgrade, a user's entire history must not vanish from
    # their sidebar the first time they type a name.
    #
    # Quoted as "user" in every statement here and in store.py. SQLite
    # accepts it bare, but it is reserved in other engines and reads
    # confusingly next to `PRAGMA user_version`, which is unrelated to it.
    #
    # This is an organization label, not an identity: no password, no
    # permission difference, and nothing here is a security boundary. It is
    # as likely to hold a project name as a person's.
    4: """
    ALTER TABLE conversations ADD COLUMN "user" TEXT;

    CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations("user", updated_at);
    """,
    # Where this conversation's files actually went, recorded rather than
    # recomputed. `delete_conversation` used to derive the sidecar folder
    # from the *current* records_dir setting — so changing the Records
    # folder in Settings orphaned every older conversation's sidecar,
    # undeletable because nothing pointed at it any more. For `figures/`
    # that is clutter; for `attachments/`, which holds copies of documents
    # the user fed into the conversation, it would be a broken promise:
    # deleting a chat has to delete its documents. Both columns are NULL
    # for conversations created before this migration, and deletion falls
    # back to the computed path for those — the old behaviour, unchanged.
    5: """
    ALTER TABLE conversations ADD COLUMN attachments_path TEXT;
    ALTER TABLE conversations ADD COLUMN sidecar_path TEXT;
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
    with _migrate_lock:
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
