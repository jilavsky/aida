"""OpenAI-compatible embeddings provider (Phase 8) — covers Ollama (an
embedding model like ``nomic-embed-text``), LM Studio, OpenAI itself, and
Argo's cloud embeddings proxy (BeamlineAdvisor precedent: ``text-embedding-
3-small`` via the same custom-``base_url`` pattern
``aida.providers.openai_compat.OpenAICompatProvider`` already uses for
chat). No new SDK — the ``openai`` package is already a dependency.

Anthropic has no first-party embeddings API (their own docs point to
Voyage AI), so there is deliberately no ``AnthropicEmbeddings`` — this one
class covers every embedding profile Phase 8 needs.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from aida.providers.embeddings_base import EmbeddingsProvider


class OpenAICompatEmbeddings(EmbeddingsProvider):
    """Any OpenAI-compatible embeddings endpoint."""

    layer_name = "embeddings"

    def __init__(
        self, *, model: str, base_url: str | None = None, api_key: str | None = None
    ) -> None:
        self.model = model
        # Same "SDK requires a non-empty api_key even for endpoints that
        # ignore it" reasoning as OpenAICompatProvider.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.embeddings.create(model=self.model, input=texts)
        # Sort by .index defensively rather than trusting response order —
        # the API contract says results come back in input order, but nothing
        # here is harmed by not assuming it.
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    async def ping(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:  # noqa: BLE001 - ping must never raise
            return False

    async def aclose(self) -> None:
        await self._client.close()


__all__ = ["OpenAICompatEmbeddings"]
