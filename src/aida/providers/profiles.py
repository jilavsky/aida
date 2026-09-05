"""Build a live ``LLMProvider`` from a stored ``ProviderProfile`` (Phase 1's
``aida.config.settings.ProviderProfile``), resolving its secret via Phase 1's
secret store, and validate profiles with a doctor-style ping.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from aida.config.secrets import get_secret
from aida.config.settings import EmbeddingProfile, ProviderProfile
from aida.providers.anthropic_ import AnthropicProvider
from aida.providers.base import LLMProvider
from aida.providers.embeddings_base import EmbeddingsProvider
from aida.providers.openai_compat import OpenAICompatProvider
from aida.providers.openai_compat_embeddings import OpenAICompatEmbeddings


class UnknownProviderKindError(ValueError):
    """Raised when a profile's ``kind`` isn't a provider AIDA implements."""


def build_provider(profile: ProviderProfile) -> LLMProvider:
    """Instantiate the right ``LLMProvider`` for a profile.

    The profile itself never carries a secret value — only ``secret_ref``,
    resolved here via ``aida.config.secrets`` (keyring, with env override).
    """
    api_key = get_secret(profile.secret_ref) if profile.secret_ref else None

    if profile.kind == "anthropic":
        return AnthropicProvider(model=profile.model, base_url=profile.base_url, api_key=api_key)
    if profile.kind == "openai_compat":
        return OpenAICompatProvider(model=profile.model, base_url=profile.base_url, api_key=api_key)

    raise UnknownProviderKindError(
        f"profile {profile.name!r} has unknown kind {profile.kind!r} "
        "(expected 'openai_compat' or 'anthropic')"
    )


def build_embeddings_provider(profile: EmbeddingProfile) -> EmbeddingsProvider:
    """Instantiate the right ``EmbeddingsProvider`` for an embedding
    profile — same secret-resolution pattern as ``build_provider``. Only
    one ``kind`` exists today (Anthropic has no first-party embeddings
    API), but this mirrors ``build_provider``'s shape rather than being a
    bare constructor call, so a second kind slots in the same way a second
    ``LLMProvider`` kind would."""
    api_key = get_secret(profile.secret_ref) if profile.secret_ref else None

    if profile.kind == "openai_compat":
        return OpenAICompatEmbeddings(
            model=profile.model, base_url=profile.base_url, api_key=api_key
        )

    raise UnknownProviderKindError(
        f"embedding profile {profile.name!r} has unknown kind {profile.kind!r} (expected 'openai_compat')"
    )


@dataclass
class ProfileValidation:
    """Result of validating one profile — mirrors ``aida.cli.doctor.CheckResult``
    in shape so callers (doctor, CLI ``/profile``) can format it the same way."""

    name: str
    ok: bool
    detail: str


DEFAULT_PING_TIMEOUT_SECONDS = 10.0


async def validate_profile(
    profile: ProviderProfile, *, timeout: float = DEFAULT_PING_TIMEOUT_SECONDS
) -> ProfileValidation:
    """Doctor-style check: can we even build and reach this profile's provider?

    Never raises — construction errors (bad kind), an unreachable endpoint
    (``ping()`` returning False) and a hung one (no answer within
    ``timeout``) all come back as a non-ok ``ProfileValidation``. The
    timeout matters for the endpoints this is actually pointed at: a
    not-running Ollama refuses fast, but an on-VPN-only host like the ANL
    Argo proxy black-holes the connection when you're off-site, and
    ``aida doctor`` must not hang there.

    The provider is always closed before returning — it owns an ``httpx``
    client, and leaving one open per validated profile produces spurious
    "unclosed client" noise when the event loop tears down.
    """
    try:
        provider = build_provider(profile)
    except UnknownProviderKindError as exc:
        return ProfileValidation(name=profile.name, ok=False, detail=str(exc))

    try:
        reachable = await asyncio.wait_for(provider.ping(), timeout=timeout)
    except TimeoutError:
        return ProfileValidation(
            name=profile.name,
            ok=False,
            detail=f"no response within {timeout:g}s ({profile.kind}, {profile.base_url or 'SDK default URL'})",
        )
    finally:
        with contextlib.suppress(Exception):  # closing must never mask the result
            await provider.aclose()

    if reachable:
        return ProfileValidation(
            name=profile.name,
            ok=True,
            detail=f"reachable ({profile.kind}, model={profile.model or 'unset'})",
        )
    return ProfileValidation(
        name=profile.name,
        ok=False,
        detail=f"not reachable ({profile.kind}, {profile.base_url or 'SDK default URL'})",
    )


async def validate_embedding_profile(
    profile: EmbeddingProfile, *, timeout: float = DEFAULT_PING_TIMEOUT_SECONDS
) -> ProfileValidation:
    """Same doctor-style check as ``validate_profile``, for an embedding
    profile — kept as a separate function rather than a generic helper
    since the two build different provider types via different exception
    messages, but the timeout/never-raises/always-close contract is
    identical."""
    try:
        provider = build_embeddings_provider(profile)
    except UnknownProviderKindError as exc:
        return ProfileValidation(name=profile.name, ok=False, detail=str(exc))

    try:
        reachable = await asyncio.wait_for(provider.ping(), timeout=timeout)
    except TimeoutError:
        return ProfileValidation(
            name=profile.name,
            ok=False,
            detail=f"no response within {timeout:g}s ({profile.kind}, {profile.base_url or 'SDK default URL'})",
        )
    finally:
        with contextlib.suppress(Exception):
            await provider.aclose()

    if reachable:
        return ProfileValidation(
            name=profile.name,
            ok=True,
            detail=f"reachable ({profile.kind}, model={profile.model or 'unset'})",
        )
    return ProfileValidation(
        name=profile.name,
        ok=False,
        detail=f"not reachable ({profile.kind}, {profile.base_url or 'SDK default URL'})",
    )


__all__ = [
    "DEFAULT_PING_TIMEOUT_SECONDS",
    "ProfileValidation",
    "UnknownProviderKindError",
    "build_embeddings_provider",
    "build_provider",
    "validate_embedding_profile",
    "validate_profile",
]
