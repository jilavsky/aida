"""Rank a knowledge base's stored chunks against a query (Phase 8).

Brute-force cosine similarity over in-memory vectors — no vector DB (see
planning/phase08_rag.md's storage decision). Fine at the corpus sizes this
phase targets (hundreds to low thousands of chunks); loading a whole
knowledge base's chunks into memory for every query is the actual
performance ceiling here, not the similarity computation itself. A
knowledge base that grows far beyond that is a documented follow-up
(PLAN.md's own "LlamaIndex only if the minimal path proves insufficient"
escape hatch), not handled here.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from aida.knowledge.rag import index as kb_index
from aida.providers.embeddings_base import EmbeddingsProvider

DEFAULT_TOP_K = 5
DEFAULT_SCORE_THRESHOLD = 0.0


@dataclass
class RetrievedPassage:
    """One ranked chunk, with enough provenance to cite it — what
    ``ChatSession`` injects into context and what the GUI's retrieval-
    transparency row displays."""

    text: str
    source_path: str
    heading: str | None
    score: float


class EmbeddingProfileMismatchError(Exception):
    """Raised when a query is attempted with a different embedding profile
    than the one that built the index — two different models' vector
    spaces aren't comparable, so a similarity score across them would be
    meaningless, not just "a bit off". The "guard" task item:
    ``aida.cli.chat.ChatSession`` treats this as "no retrieval this turn"
    (logged, not a hard failure) rather than crashing the turn; the GUI/CLI
    surface it as "rebuild this knowledge base to use it with this profile".
    """


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class ActiveKnowledgeBase:
    """A resolved, ready-to-query knowledge base for one chat session —
    what ``aida.cli.chat.start_session`` builds once per name in a
    workspace's ``knowledge_bases`` list (open connection + built
    embeddings provider), and what ``ChatSession.send()`` queries every
    turn via ``retrieve_from_active_kb``. Owns real resources (a SQLite
    connection, an ``EmbeddingsProvider`` with its own HTTP client) that
    the session must close on ``aclose()``.
    """

    name: str
    connection: sqlite3.Connection
    embeddings_provider: EmbeddingsProvider
    embedding_profile_name: str
    top_k: int = DEFAULT_TOP_K
    score_threshold: float = DEFAULT_SCORE_THRESHOLD


async def retrieve_from_active_kb(
    kb: ActiveKnowledgeBase, query_text: str
) -> list[RetrievedPassage]:
    """Convenience wrapper over ``retrieve()`` for an already-resolved
    ``ActiveKnowledgeBase`` — what ``ChatSession.send()`` actually calls,
    once per active knowledge base, per turn."""
    return await retrieve(
        kb.connection,
        query_text,
        embeddings_provider=kb.embeddings_provider,
        embedding_profile_name=kb.embedding_profile_name,
        top_k=kb.top_k,
        score_threshold=kb.score_threshold,
    )


async def retrieve(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    embeddings_provider: EmbeddingsProvider,
    embedding_profile_name: str,
    top_k: int = DEFAULT_TOP_K,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> list[RetrievedPassage]:
    """Embed ``query_text`` and rank every chunk in ``conn``'s knowledge
    base against it, returning the top ``top_k`` scoring at or above
    ``score_threshold``, highest first. Returns an empty list for an empty
    (never-built) index rather than embedding a query for nothing.
    """
    built_with = kb_index.embedding_profile_used(conn)
    if built_with is not None and built_with != embedding_profile_name:
        raise EmbeddingProfileMismatchError(
            f"index was built with embedding profile {built_with!r}, not {embedding_profile_name!r} "
            "— rebuild the knowledge base with the current profile"
        )

    chunks = kb_index.all_chunks(conn)
    if not chunks:
        return []

    query_vector = (await embeddings_provider.embed([query_text]))[0]

    scored = [
        RetrievedPassage(
            text=chunk.text,
            source_path=chunk.source_path,
            heading=chunk.heading,
            score=_cosine(query_vector, chunk.embedding),
        )
        for chunk in chunks
    ]
    scored.sort(key=lambda passage: passage.score, reverse=True)
    return [passage for passage in scored if passage.score >= score_threshold][:top_k]


__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "DEFAULT_TOP_K",
    "ActiveKnowledgeBase",
    "EmbeddingProfileMismatchError",
    "RetrievedPassage",
    "retrieve",
    "retrieve_from_active_kb",
]
