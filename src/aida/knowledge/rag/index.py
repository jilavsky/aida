"""SQLite storage for one knowledge base's chunks + embeddings (Phase 8).

Deliberately its own small schema and its own ``connect()`` — not
``aida.persistence.db.connect()``, which bakes in *that* module's own
conversations/messages/artifacts schema via its migration list; reusing it
here would create those unrelated tables inside every knowledge base's own
db file. Same ``PRAGMA user_version`` + migrations-dict *pattern*, applied
independently, one file per knowledge base
(``aida.config.paths.knowledge_db_path``).

Embeddings are stored as raw little-endian float32 bytes (``struct.pack``)
— no numpy dependency needed for storage, matching this phase's "minimal,
no vector DB" decision (see planning/phase08_rag.md).
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

CURRENT_SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_path TEXT NOT NULL,
        heading TEXT,
        chunk_index INTEGER NOT NULL,
        mtime REAL NOT NULL,
        text TEXT NOT NULL,
        embedding BLOB NOT NULL
    );

    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_chunks_source_path ON chunks(source_path);
    """,
}

#: The ``meta`` key recording which embedding profile last built (or
#: extended) this index — the "guard" task item: querying with a different
#: profile than the one that built the index is a nonsense comparison
#: (different models' vector spaces aren't comparable), so retrieval checks
#: this before running similarity search.
_EMBEDDING_PROFILE_META_KEY = "embedding_profile"


@dataclass
class StoredChunk:
    """One chunk as read back from the index, embedding included — what
    ``aida.knowledge.rag.retrieval`` loads into memory to rank."""

    source_path: str
    heading: str | None
    chunk_index: int
    text: str
    embedding: list[float]


def pack_embedding(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_embedding(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating the file and/or migrating the schema if needed) one
    knowledge base's index. Rows come back as ``sqlite3.Row``, same
    dict-like access convention as ``aida.persistence.db.connect``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
        script = _MIGRATIONS.get(version)
        if script is None:
            continue
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {version}")  # noqa: S608 - version is our own fixed int range
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def embedding_profile_used(conn: sqlite3.Connection) -> str | None:
    """Which embedding profile built this index, or ``None`` for an empty/
    never-built one."""
    return get_meta(conn, _EMBEDDING_PROFILE_META_KEY)


def replace_file_chunks(
    conn: sqlite3.Connection,
    *,
    source_path: str,
    mtime: float,
    chunks_with_embeddings: list[tuple[object, list[float]]],
    embedding_profile: str,
) -> None:
    """Delete a file's existing chunks (if any) and insert its current
    ones — the unit of work for both a first-time ingest and a re-ingest of
    a changed file. ``chunks_with_embeddings`` pairs each
    ``aida.knowledge.rag.chunking.Chunk`` with its embedding vector, same
    order. Recording ``embedding_profile`` on every call (not just once)
    means the guard reflects whichever profile most recently touched the
    index, matching "rebuilt per profile" from the phase's acceptance
    criteria.
    """
    conn.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))
    conn.executemany(
        "INSERT INTO chunks (source_path, heading, chunk_index, mtime, text, embedding) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (source_path, chunk.heading, chunk.chunk_index, mtime, chunk.text, pack_embedding(vector))
            for chunk, vector in chunks_with_embeddings
        ],
    )
    set_meta(conn, _EMBEDDING_PROFILE_META_KEY, embedding_profile)
    conn.commit()


def delete_source(conn: sqlite3.Connection, source_path: str) -> None:
    """Remove every chunk belonging to a file — used when ingest notices a
    previously-indexed file no longer exists."""
    conn.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))
    conn.commit()


def indexed_source_mtimes(conn: sqlite3.Connection) -> dict[str, float]:
    """``{source_path: mtime}`` for every distinct file currently indexed —
    what ``aida.knowledge.rag.ingest``'s incremental-update pass diffs
    against the folder's real files."""
    rows = conn.execute("SELECT source_path, MAX(mtime) AS mtime FROM chunks GROUP BY source_path").fetchall()
    return {row["source_path"]: row["mtime"] for row in rows}


def all_chunks(conn: sqlite3.Connection) -> list[StoredChunk]:
    """Every chunk + its embedding — what retrieval loads into memory to
    rank against a query. Fine at the corpus sizes this phase targets
    (hundreds to low thousands of chunks per knowledge base); a knowledge
    base that grows far beyond that is a documented follow-up, not handled
    here (see planning/phase08_rag.md)."""
    rows = conn.execute("SELECT source_path, heading, chunk_index, text, embedding FROM chunks").fetchall()
    return [
        StoredChunk(
            source_path=row["source_path"],
            heading=row["heading"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            embedding=unpack_embedding(row["embedding"]),
        )
        for row in rows
    ]


def chunk_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "StoredChunk",
    "all_chunks",
    "chunk_count",
    "connect",
    "delete_source",
    "embedding_profile_used",
    "get_meta",
    "indexed_source_mtimes",
    "pack_embedding",
    "replace_file_chunks",
    "set_meta",
    "unpack_embedding",
]
