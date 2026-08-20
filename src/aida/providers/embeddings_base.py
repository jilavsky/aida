"""Provider-agnostic embeddings interface (Phase 8) — mirrors
``aida.providers.base.LLMProvider``'s shape exactly (an ABC every concrete
embeddings backend implements, a cheap ``ping()`` for ``aida doctor``-style
validation, an ``aclose()`` for connection cleanup) so
``aida.providers.profiles`` and ``aida.knowledge.rag`` can treat embedding
profiles the same way the rest of AIDA already treats chat profiles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingsProvider(ABC):
    """Common interface every embeddings backend implements.

    ``embed()`` takes a batch of texts and returns one vector per text, in
    the same order — batching matters for real usage (ingesting a folder of
    documents means embedding hundreds of chunks, not one call per chunk).
    """

    #: Set by subclasses; used to tag diagnostics the same way
    #: ``LLMProvider.layer_name`` does.
    layer_name: str = "embeddings"

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per text in order.

        Raises on failure — unlike ``LLMProvider.complete()`` (which yields
        an ``AgentError`` and never raises), there is no event stream here
        for a caller to report a failure through; ``aida.knowledge.rag``
        callers catch and report exceptions themselves (ingestion/retrieval
        are synchronous request/response operations, not a streamed turn).
        """
        raise NotImplementedError

    @abstractmethod
    async def ping(self) -> bool:
        """Best-effort, cheap reachability check. Must never raise — return
        False on failure, same contract as ``LLMProvider.ping()``."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any underlying connections. Default no-op; real
        SDK-backed providers override this — same reasoning as
        ``LLMProvider.aclose()``'s docstring."""
        return None


__all__ = ["EmbeddingsProvider"]
