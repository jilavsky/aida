"""Rank a knowledge base's stored chunks against a query (Phase 8).

Brute-force cosine similarity over in-memory vectors, no vector DB (see
planning/completed/phase08_rag.md's storage decision).

Measured in the November 2026 review at about 11,800 chunks of 1,024
dimensions: loading and unpacking every vector from SQLite on every turn
cost 0.9 s and the pure-Python cosine 1.45 s, all on the event loop. So:

- ``VectorCache`` keeps each knowledge base's vectors in memory for the
  session, **normalised once**, so a query is one dot product per chunk.
  It reloads only when the index changes (row count or highest row id).
- Text and provenance are fetched from SQLite for the top hits only.
- Scoring runs in a worker thread (``asyncio.to_thread``).
- NumPy is used when it is installed (most installs have it through
  pyIrena); otherwise the same scores are computed in pure Python with
  ``math.sumprod`` (3.12+) or ``sum(map(mul, ...))``. NumPy stays out of
  the core dependencies (DESIGN.md §8). Normalised dot product equals
  cosine similarity; the NumPy path accumulates in float32, the precision
  the vectors are stored in, so scores can differ from the pure-Python
  path in about the sixth decimal.
"""

from __future__ import annotations

import asyncio
import math
import operator
import sqlite3
from dataclasses import dataclass, field
from typing import Any

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


try:  # optional: used when present, never required
    import numpy as _np
except ImportError:  # pragma: no cover - depends on the environment
    _np = None

_sumprod = getattr(math, "sumprod", None)  # Python 3.12+


def _dot(a: list[float], b: list[float]) -> float:
    if _sumprod is not None:
        return _sumprod(a, b)
    return sum(map(operator.mul, a, b))


def _normalised(vector: list[float]) -> list[float]:
    """Unit-length copy of ``vector``; a zero vector stays zero, so it
    scores 0 against everything, as the plain cosine did."""
    norm = math.sqrt(_dot(vector, vector))
    if norm == 0:
        return [0.0] * len(vector)
    return [x / norm for x in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    return _dot(_normalised(a), _normalised(b))


@dataclass
class VectorCache:
    """One knowledge base's normalised vectors, kept for the session.

    ``signature`` is ``(row count, highest row id)``: any rebuild, added
    file or deleted file changes it, and the next query reloads. Loading
    happens on the caller's thread because the SQLite connection belongs
    to it; only the scoring moves to a worker thread.
    """

    signature: tuple[int, int] | None = None
    row_ids: list[int] = field(default_factory=list)
    vectors: Any = None  # numpy float32 matrix, or list[list[float]]

    def refresh(self, conn: sqlite3.Connection) -> None:
        count, max_id = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks").fetchone()
        signature = (int(count), int(max_id))
        if signature == self.signature:
            return
        rows = conn.execute("SELECT id, embedding FROM chunks ORDER BY id").fetchall()
        self.row_ids = [row[0] for row in rows]
        if _np is not None and rows:
            matrix = _np.vstack([_np.frombuffer(row[1], dtype="<f4") for row in rows])
            norms = _np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0  # zero vectors stay zero
            self.vectors = (matrix / norms).astype(_np.float32, copy=False)
        else:
            self.vectors = [_normalised(kb_index.unpack_embedding(row[1])) for row in rows]
        self.signature = signature

    def scores(self, query_vector: list[float]) -> list[float]:
        """Cosine similarity of ``query_vector`` with every cached chunk,
        in ``row_ids`` order. CPU-bound; meant for ``asyncio.to_thread``."""
        if not self.row_ids:
            return []
        if _np is not None and not isinstance(self.vectors, list):
            query = _np.asarray(query_vector, dtype=_np.float32)
            norm = float(_np.linalg.norm(query))
            if norm == 0:
                return [0.0] * len(self.row_ids)
            return (self.vectors @ (query / norm)).tolist()
        query = _normalised(query_vector)
        return [_dot(query, vector) for vector in self.vectors]


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
    #: Normalised vectors kept between turns — see ``VectorCache``.
    cache: VectorCache = field(default_factory=VectorCache, repr=False, compare=False)


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
        cache=kb.cache,
    )


async def retrieve(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    embeddings_provider: EmbeddingsProvider,
    embedding_profile_name: str,
    top_k: int = DEFAULT_TOP_K,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    cache: VectorCache | None = None,
) -> list[RetrievedPassage]:
    """Embed ``query_text`` and rank every chunk in ``conn``'s knowledge
    base against it, returning the top ``top_k`` scoring at or above
    ``score_threshold``, highest first. Returns an empty list for an empty
    (never-built) index rather than embedding a query for nothing.

    ``cache`` keeps the normalised vectors between calls (a chat session
    passes the one on its ``ActiveKnowledgeBase``); without it a fresh one
    is built for this call only, as for a one-off ``aida kb query``.
    """
    built_with = kb_index.embedding_profile_used(conn)
    if built_with is not None and built_with != embedding_profile_name:
        raise EmbeddingProfileMismatchError(
            f"index was built with embedding profile {built_with!r}, not {embedding_profile_name!r} "
            "— rebuild the knowledge base with the current profile"
        )

    cache = cache if cache is not None else VectorCache()
    cache.refresh(conn)
    if not cache.row_ids:
        return []

    query_vector = (await embeddings_provider.embed([query_text]))[0]
    scores = await asyncio.to_thread(cache.scores, query_vector)

    ranked = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
    best = [i for i in ranked if scores[i] >= score_threshold][:top_k]
    if not best:
        return []
    ids = [cache.row_ids[i] for i in best]
    placeholders = ",".join("?" * len(ids))
    rows = {
        row[0]: row
        for row in conn.execute(
            f"SELECT id, text, source_path, heading FROM chunks WHERE id IN ({placeholders})", ids
        )
    }
    passages: list[RetrievedPassage] = []
    for i, row_id in zip(best, ids, strict=True):
        row = rows.get(row_id)
        if row is None:  # removed between refresh and fetch; skip it
            continue
        passages.append(
            RetrievedPassage(
                text=row[1], source_path=row[2], heading=row[3], score=float(scores[i])
            )
        )
    return passages


__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "DEFAULT_TOP_K",
    "ActiveKnowledgeBase",
    "EmbeddingProfileMismatchError",
    "RetrievedPassage",
    "VectorCache",
    "retrieve",
    "retrieve_from_active_kb",
]
