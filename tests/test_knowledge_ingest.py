"""Tests for aida.knowledge.rag.ingest — a tiny fixture corpus + the
deterministic MockEmbeddings fake embedder, no network."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from aida.config.settings import KnowledgeBaseConfig
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.ingest import INGESTIBLE_SUFFIXES, rebuild, update
from aida.providers.mock_embeddings import MockEmbeddings


def _kb(source_folder: Path, **overrides) -> KnowledgeBaseConfig:
    defaults = dict(name="test-kb", source_folders=[str(source_folder)], embedding_profile="mock")
    defaults.update(overrides)
    return KnowledgeBaseConfig(**defaults)


@pytest.mark.asyncio
async def test_rebuild_ingests_every_discovered_file(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nContent A.")
    (corpus / "b.txt").write_text("Content B.")
    (corpus / "ignored.png").write_bytes(b"\x89PNG")  # not an ingestible suffix

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())

    assert len(result.added_files) == 2
    assert result.chunk_count == 2
    assert kb_index.chunk_count(conn) == 2
    conn.close()


@pytest.mark.asyncio
async def test_rebuild_prunes_files_no_longer_present(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    doomed = corpus / "will_be_deleted.md"
    doomed.write_text("# Doomed\n\nThis file will be removed before the next rebuild.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())
    assert kb_index.chunk_count(conn) == 1

    doomed.unlink()
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())

    assert str(doomed) in result.removed_files
    assert kb_index.chunk_count(conn) == 0
    conn.close()


@pytest.mark.asyncio
async def test_update_skips_unchanged_files_no_reembedding(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nContent A.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())

    embedder = MockEmbeddings()
    result = await update(conn, _kb(corpus), embedder)

    assert result.added_files == []
    assert result.updated_files == []
    assert embedder.calls == [], "an unchanged file must not be re-embedded"
    conn.close()


@pytest.mark.asyncio
async def test_update_reembeds_only_the_touched_file(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    a = corpus / "a.md"
    a.write_text("# A\n\nOriginal content A.")
    b = corpus / "b.md"
    b.write_text("# B\n\nContent B, never touched.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())

    # Ensure a strictly later mtime than the first write.
    time.sleep(0.05)
    a.write_text("# A\n\nUpdated content A, now different.")

    embedder = MockEmbeddings()
    result = await update(conn, _kb(corpus), embedder)

    assert result.updated_files == [str(a)]
    assert result.added_files == []
    assert embedder.calls == [["Updated content A, now different."]] or all(
        "Updated content" in text for call in embedder.calls for text in call
    )
    stored = kb_index.all_chunks(conn)
    assert any("Updated content A" in c.text for c in stored)
    assert any("Content B" in c.text for c in stored), "the untouched file's chunks must survive"
    conn.close()


@pytest.mark.asyncio
async def test_update_adds_a_new_file_and_removes_a_deleted_one(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    a = corpus / "a.md"
    a.write_text("# A\n\nContent A.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())

    a.unlink()
    b = corpus / "b.md"
    b.write_text("# B\n\nContent B.")

    result = await update(conn, _kb(corpus), MockEmbeddings())

    assert result.added_files == [str(b)]
    assert result.removed_files == [str(a)]
    conn.close()


@pytest.mark.asyncio
async def test_ingest_skips_unreadable_file_without_aborting_the_pass(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "good.md").write_text("# Good\n\nReadable content.")
    bad = corpus / "bad.pdf"
    bad.write_bytes(b"not actually a pdf")  # will fail to parse

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())

    assert any("bad.pdf" in entry for entry in result.skipped_files)
    assert any("good.md" in entry for entry in result.added_files)
    conn.close()


def test_ingestible_suffixes_cover_planned_formats():
    for suffix in [".md", ".txt", ".rst", ".py", ".pdf", ".docx"]:
        assert suffix in INGESTIBLE_SUFFIXES
