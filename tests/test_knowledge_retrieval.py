"""Tests for aida.knowledge.rag.retrieval — ranking sanity, score
threshold, top-k, and the embedding-profile mismatch guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.chunking import Chunk
from aida.knowledge.rag.retrieval import EmbeddingProfileMismatchError, retrieve
from aida.providers.mock_embeddings import MockEmbeddings


async def _seed_async(conn, texts: list[str], *, embedding_profile: str = "mock") -> None:
    embedder = MockEmbeddings()
    vectors = await embedder.embed(texts)
    for i, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
        kb_index.replace_file_chunks(
            conn,
            source_path=f"/docs/doc_{i}.md",
            mtime=float(i),
            chunks_with_embeddings=[(Chunk(text=text, heading=f"Doc {i}", chunk_index=0), vector)],
            embedding_profile=embedding_profile,
        )


@pytest.mark.asyncio
async def test_retrieve_ranks_the_more_relevant_passage_first(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(
        conn,
        [
            "Unified Fit models a SAXS curve with multiple structural levels.",
            "The USAXS instrument uses a Bonse-Hart crystal analyzer geometry.",
        ],
    )

    results = await retrieve(
        conn, "How does Unified Fit work for multi-level curves?",
        embeddings_provider=MockEmbeddings(), embedding_profile_name="mock",
    )

    assert results[0].source_path.endswith("doc_0.md")
    assert results[0].heading == "Doc 0"
    conn.close()


@pytest.mark.asyncio
async def test_retrieve_respects_top_k(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, [f"Document number {i} about topic {i}." for i in range(10)])

    results = await retrieve(conn, "topic 3", embeddings_provider=MockEmbeddings(), embedding_profile_name="mock", top_k=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_retrieve_applies_score_threshold(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Completely unrelated content about basket weaving."])

    results = await retrieve(
        conn, "Unified Fit SAXS modeling",
        embeddings_provider=MockEmbeddings(), embedding_profile_name="mock", score_threshold=0.99,
    )
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_on_empty_index_returns_no_results_without_embedding(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    embedder = MockEmbeddings()

    results = await retrieve(conn, "anything", embeddings_provider=embedder, embedding_profile_name="mock")

    assert results == []
    assert embedder.calls == [], "no point embedding a query against an empty index"


@pytest.mark.asyncio
async def test_retrieve_raises_on_embedding_profile_mismatch(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Some content."], embedding_profile="profile-a")

    with pytest.raises(EmbeddingProfileMismatchError):
        await retrieve(conn, "query", embeddings_provider=MockEmbeddings(), embedding_profile_name="profile-b")


@pytest.mark.asyncio
async def test_retrieve_with_matching_profile_does_not_raise(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Some content."], embedding_profile="profile-a")

    results = await retrieve(conn, "query", embeddings_provider=MockEmbeddings(), embedding_profile_name="profile-a")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_retrieved_passage_carries_source_and_heading(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Content about Unified Fit."])

    results = await retrieve(conn, "Unified Fit", embeddings_provider=MockEmbeddings(), embedding_profile_name="mock")
    assert results[0].heading == "Doc 0"
    assert results[0].source_path.endswith("doc_0.md")
    assert 0.0 <= results[0].score <= 1.0
