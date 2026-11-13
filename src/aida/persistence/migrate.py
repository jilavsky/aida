"""Transactional ``PRAGMA user_version`` migrations, shared by both databases.

Used by the conversation database (``aida.persistence.db``) and by every
knowledge-base index (``aida.knowledge.rag.index``). Each migration and its
version bump run in one ``BEGIN IMMEDIATE`` transaction, and the version is
re-read inside it, so:

- two AIDA processes opening the same file after an upgrade cannot both run
  the same ``ALTER TABLE`` (the second waits for the lock, then sees the new
  version and skips it);
- a crash mid-migration rolls back to the previous version instead of
  leaving the schema changed but the version not bumped.

``executescript`` cannot be used for this: it commits any open transaction
before running. Scripts are split into statements with
``sqlite3.complete_statement`` and executed one by one instead.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def split_sql_script(script: str) -> list[str]:
    """Split a migration script into complete SQL statements.

    Uses ``sqlite3.complete_statement``, so semicolons inside string
    literals or ``CREATE TRIGGER ... BEGIN ... END`` bodies do not split a
    statement.
    """
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        # A trailing statement without a semicolon is still a statement.
        statements.append(buffer.strip())
    return statements


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: Mapping[int, str],
    target_version: int,
    *,
    database_label: str,
) -> None:
    """Bring ``conn``'s schema up to ``target_version``.

    Args:
        conn: An open connection. Any transaction it has open is committed
            first.
        migrations: Version number to SQL script. A version with no entry
            only bumps ``user_version``.
        target_version: The schema version this code expects.
        database_label: Name used in the warning for a database newer than
            the code (for example ``"conversation database"``).

    A database whose ``user_version`` is above ``target_version`` was
    written by a newer AIDA. It is opened unchanged and a warning is logged.
    """
    if conn.in_transaction:
        conn.commit()
    current = _user_version(conn)
    if current > target_version:
        logger.warning(
            "%s is at schema version %d, newer than this AIDA (%d); it was "
            "written by a newer AIDA installation. Opening it anyway.",
            database_label,
            current,
            target_version,
        )
        return
    if current == target_version:
        return

    saved_isolation = conn.isolation_level
    conn.isolation_level = None  # manual transaction control below
    try:
        for version in range(current + 1, target_version + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Another process may have migrated while we waited for the lock.
                if _user_version(conn) >= version:
                    conn.execute("COMMIT")
                    continue
                for statement in split_sql_script(migrations.get(version, "")):
                    conn.execute(statement)
                # PRAGMA takes no bound parameters; `version` is our own int.
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.isolation_level = saved_isolation


__all__ = ["apply_migrations", "split_sql_script"]
