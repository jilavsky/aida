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
        conn,
        "How does Unified Fit work for multi-level curves?",
        embeddings_provider=MockEmbeddings(),
        embedding_profile_name="mock",
    )

    assert results[0].source_path.endswith("doc_0.md")
    assert results[0].heading == "Doc 0"
    conn.close()


@pytest.mark.asyncio
async def test_retrieve_respects_top_k(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, [f"Document number {i} about topic {i}." for i in range(10)])

    results = await retrieve(
        conn,
        "topic 3",
        embeddings_provider=MockEmbeddings(),
        embedding_profile_name="mock",
        top_k=3,
    )
    assert len(results) == 3


@pytest.mark.asyncio
async def test_retrieve_applies_score_threshold(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Completely unrelated content about basket weaving."])

    results = await retrieve(
        conn,
        "Unified Fit SAXS modeling",
        embeddings_provider=MockEmbeddings(),
        embedding_profile_name="mock",
        score_threshold=0.99,
    )
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_on_empty_index_returns_no_results_without_embedding(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    embedder = MockEmbeddings()

    results = await retrieve(
        conn, "anything", embeddings_provider=embedder, embedding_profile_name="mock"
    )

    assert results == []
    assert embedder.calls == [], "no point embedding a query against an empty index"


@pytest.mark.asyncio
async def test_retrieve_raises_on_embedding_profile_mismatch(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Some content."], embedding_profile="profile-a")

    with pytest.raises(EmbeddingProfileMismatchError):
        await retrieve(
            conn, "query", embeddings_provider=MockEmbeddings(), embedding_profile_name="profile-b"
        )


@pytest.mark.asyncio
async def test_retrieve_with_matching_profile_does_not_raise(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Some content."], embedding_profile="profile-a")

    results = await retrieve(
        conn, "query", embeddings_provider=MockEmbeddings(), embedding_profile_name="profile-a"
    )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_retrieved_passage_carries_source_and_heading(tmp_path: Path):
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["Content about Unified Fit."])

    results = await retrieve(
        conn, "Unified Fit", embeddings_provider=MockEmbeddings(), embedding_profile_name="mock"
    )
    assert results[0].heading == "Doc 0"
    assert results[0].source_path.endswith("doc_0.md")
    assert 0.0 <= results[0].score <= 1.0


# --- VectorCache (review P1) -------------------------------------------------


@pytest.mark.asyncio
async def test_cached_retrieval_matches_plain_cosine_over_every_chunk(tmp_path: Path):
    """The cached, normalised dot product must rank and score exactly like
    the cosine over freshly loaded chunks it replaced (to float32 precision
    on the NumPy path)."""
    import math

    from aida.knowledge.rag.retrieval import VectorCache

    texts = [f"Passage {i} about SAXS level {i % 4} and Porod slope {i % 7}." for i in range(40)]
    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, texts)
    query = "Porod slope of level 2"
    query_vector = (await MockEmbeddings().embed([query]))[0]

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
        return 0.0 if na == 0 or nb == 0 else dot / (na * nb)

    expected = sorted(
        ((cosine(query_vector, c.embedding), c.source_path) for c in kb_index.all_chunks(conn)),
        reverse=True,
    )[:5]

    results = await retrieve(
        conn,
        query,
        embeddings_provider=MockEmbeddings(),
        embedding_profile_name="mock",
        top_k=5,
        cache=VectorCache(),
    )
    # Mock embeddings produce exact ties, so compare the scores in rank
    # order, and each passage's score against its own chunk's cosine.
    assert [r.score for r in results] == pytest.approx([s for s, _ in expected], abs=1e-5)
    by_path = {c.source_path: c.embedding for c in kb_index.all_chunks(conn)}
    for result in results:
        assert result.score == pytest.approx(
            cosine(query_vector, by_path[result.source_path]), abs=1e-5
        )
    conn.close()


@pytest.mark.asyncio
async def test_vector_cache_is_reused_and_reloads_when_the_index_changes(tmp_path: Path):
    from aida.knowledge.rag.retrieval import VectorCache

    conn = kb_index.connect(tmp_path / "kb.db")
    await _seed_async(conn, ["alpha beta", "gamma delta"])
    cache = VectorCache()
    kwargs = dict(embeddings_provider=MockEmbeddings(), embedding_profile_name="mock", cache=cache)

    await retrieve(conn, "alpha", **kwargs)
    first_vectors = cache.vectors
    await retrieve(conn, "gamma", **kwargs)
    assert cache.vectors is first_vectors  # unchanged index: not reloaded

    vector = (await MockEmbeddings().embed(["epsilon zeta"]))[0]
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/new.md",
        mtime=9.0,
        chunks_with_embeddings=[(Chunk(text="epsilon zeta", heading=None, chunk_index=0), vector)],
        embedding_profile="mock",
    )
    results = await retrieve(conn, "epsilon zeta", **kwargs)
    assert cache.vectors is not first_vectors
    assert len(cache.row_ids) == 3
    assert results[0].source_path == "/docs/new.md"
    conn.close()
