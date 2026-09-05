"""Tests for aida.providers.mock_embeddings — the deterministic fake
embedder used throughout Phase 8's RAG tests (chunking/index/ingest/
retrieval), so those test files don't need to re-verify the embedder's own
ranking behavior each time."""

from __future__ import annotations

import pytest

from aida.providers.mock_embeddings import DEFAULT_DIM, MockEmbeddings, hash_embed


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hash_embed_is_deterministic():
    assert hash_embed("Unified Fit models a SAXS curve") == hash_embed(
        "Unified Fit models a SAXS curve"
    )


def test_hash_embed_is_unit_length():
    vector = hash_embed("some text with several different words in it")
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_hash_embed_empty_text_is_zero_vector():
    assert hash_embed("") == [0.0] * DEFAULT_DIM


def test_hash_embed_similar_texts_score_higher_than_unrelated():
    """The whole point of a "deterministic fake embedder" for RAG tests:
    ranking has to behave sensibly, not just be reproducible."""
    a = hash_embed("Unified Fit models the SAXS curve with multiple structural levels")
    b = hash_embed("Unified Fit is used to model multi-level SAXS scattering curves")
    c = hash_embed("The USAXS instrument uses a Bonse-Hart crystal analyzer geometry")

    assert _cosine(a, b) > _cosine(a, c)


def test_hash_embed_respects_custom_dimension():
    assert len(hash_embed("x", dim=16)) == 16


@pytest.mark.asyncio
async def test_mock_embeddings_returns_one_vector_per_text():
    embedder = MockEmbeddings()
    vectors = await embedder.embed(["first text", "second text", "third text"])
    assert len(vectors) == 3
    assert all(len(v) == DEFAULT_DIM for v in vectors)


@pytest.mark.asyncio
async def test_mock_embeddings_empty_batch():
    embedder = MockEmbeddings()
    assert await embedder.embed([]) == []


@pytest.mark.asyncio
async def test_mock_embeddings_records_calls():
    embedder = MockEmbeddings()
    await embedder.embed(["a", "b"])
    await embedder.embed(["c"])
    assert embedder.calls == [["a", "b"], ["c"]]


@pytest.mark.asyncio
async def test_mock_embeddings_ping_always_true():
    assert await MockEmbeddings().ping() is True
