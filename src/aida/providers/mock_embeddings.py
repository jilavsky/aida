"""``MockEmbeddings`` — a deterministic, network-free ``EmbeddingsProvider``
for tests (PLAN.md Phase 8: "Tests with a tiny fixture corpus + deterministic
fake embedder"). Sibling to ``aida.providers.mock.MockProvider`` (the chat
equivalent), in its own file since the two are unrelated interfaces.

Uses the hashing trick (feature hashing): each token hashes to a fixed
vector index with a hash-derived sign, so two texts sharing vocabulary end
up with correlated vectors and two texts sharing *no* vocabulary end up
close to orthogonal — real bag-of-words behavior, not random noise, which
is what makes ranking-sanity tests ("a query about X scores the X chunk
higher than the Y chunk") meaningful without any ML model or network call.
"""

from __future__ import annotations

import hashlib
import math
import re

from aida.providers.embeddings_base import EmbeddingsProvider

DEFAULT_DIM = 64

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def hash_embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """One deterministic, L2-normalized vector for ``text``. Exposed as a
    plain function (not just a method) so a test can compute an expected
    embedding directly for an assertion without going through the async
    provider interface."""
    vector = [0.0] * dim
    for token in _TOKEN_RE.findall(text.lower()):
        digest = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        index = digest % dim
        sign = 1.0 if (digest // dim) % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm > 0 else vector


class MockEmbeddings(EmbeddingsProvider):
    """Records every batch it was asked to embed (``self.calls``), same
    "scripted but observable" spirit as ``MockProvider.calls``."""

    layer_name = "embeddings"

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [hash_embed(text, self.dim) for text in texts]

    async def ping(self) -> bool:
        return True


__all__ = ["DEFAULT_DIM", "MockEmbeddings", "hash_embed"]
