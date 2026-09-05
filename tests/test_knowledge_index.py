"""Tests for aida.knowledge.rag.index — one knowledge base's SQLite
storage: create/upsert/prune, and the embedding-profile guard."""

from __future__ import annotations

from pathlib import Path

from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.chunking import Chunk


def test_pack_unpack_embedding_round_trips():
    """Storage is float32 (4 bytes/dimension) — real embedding models
    already output float32, and cosine-similarity ranking doesn't need
    float64 precision, so the round-trip is approximate, not exact."""
    import pytest

    vector = [0.1, -0.5, 3.25, 0.0]
    result = kb_index.unpack_embedding(kb_index.pack_embedding(vector))
    assert result == pytest.approx(vector, abs=1e-6)


def test_connect_creates_file_and_schema(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    assert (tmp_path / "kb.db").exists()
    assert kb_index.chunk_count(conn) == 0
    conn.close()


def test_connect_is_idempotent(tmp_path: Path):
    path = tmp_path / "kb.db"
    kb_index.connect(path).close()
    conn = kb_index.connect(path)  # must not raise on an already-migrated file
    assert kb_index.chunk_count(conn) == 0
    conn.close()


def test_replace_file_chunks_inserts_and_records_profile(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    chunks = [
        Chunk(text="alpha", heading="A", chunk_index=0),
        Chunk(text="beta", heading="A", chunk_index=1),
    ]
    embeddings = [[1.0, 0.0], [0.0, 1.0]]

    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/a.md",
        mtime=100.0,
        chunks_with_embeddings=list(zip(chunks, embeddings, strict=True)),
        embedding_profile="mock",
    )

    assert kb_index.chunk_count(conn) == 2
    assert kb_index.embedding_profile_used(conn) == "mock"
    stored = kb_index.all_chunks(conn)
    assert {c.text for c in stored} == {"alpha", "beta"}
    conn.close()


def test_replace_file_chunks_deletes_the_files_old_chunks_first(tmp_path: Path):
    """Re-ingesting a changed file must not leave its stale chunks
    alongside the new ones."""
    conn = kb_index.connect(tmp_path / "kb.db")
    old_chunk = [Chunk(text="old content", heading=None, chunk_index=0)]
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/a.md",
        mtime=100.0,
        chunks_with_embeddings=list(zip(old_chunk, [[1.0, 0.0]], strict=True)),
        embedding_profile="mock",
    )
    new_chunk = [Chunk(text="new content", heading=None, chunk_index=0)]
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/a.md",
        mtime=200.0,
        chunks_with_embeddings=list(zip(new_chunk, [[0.0, 1.0]], strict=True)),
        embedding_profile="mock",
    )

    stored = kb_index.all_chunks(conn)
    assert [c.text for c in stored] == ["new content"]
    conn.close()


def test_delete_source_removes_only_that_files_chunks(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/a.md",
        mtime=100.0,
        chunks_with_embeddings=[(Chunk(text="a", heading=None, chunk_index=0), [1.0])],
        embedding_profile="mock",
    )
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/b.md",
        mtime=100.0,
        chunks_with_embeddings=[(Chunk(text="b", heading=None, chunk_index=0), [1.0])],
        embedding_profile="mock",
    )

    kb_index.delete_source(conn, "/docs/a.md")

    stored = kb_index.all_chunks(conn)
    assert [c.source_path for c in stored] == ["/docs/b.md"]
    conn.close()


def test_indexed_source_mtimes_one_entry_per_file(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/a.md",
        mtime=111.0,
        chunks_with_embeddings=[
            (Chunk(text="a1", heading=None, chunk_index=0), [1.0]),
            (Chunk(text="a2", heading=None, chunk_index=1), [1.0]),
        ],
        embedding_profile="mock",
    )
    assert kb_index.indexed_source_mtimes(conn) == {"/docs/a.md": 111.0}
    conn.close()


def test_embedding_profile_used_none_for_empty_index(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    assert kb_index.embedding_profile_used(conn) is None
    conn.close()


def test_embedding_profile_reflects_most_recent_build(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/a.md",
        mtime=1.0,
        chunks_with_embeddings=[(Chunk(text="a", heading=None, chunk_index=0), [1.0])],
        embedding_profile="profile-1",
    )
    assert kb_index.embedding_profile_used(conn) == "profile-1"
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/b.md",
        mtime=1.0,
        chunks_with_embeddings=[(Chunk(text="b", heading=None, chunk_index=0), [1.0])],
        embedding_profile="profile-2",
    )
    assert kb_index.embedding_profile_used(conn) == "profile-2"
    conn.close()
