"""Build a live ``LLMProvider`` from a stored ``ProviderProfile`` (Phase 1's
``aida.config.settings.ProviderProfile``), resolving its secret via Phase 1's
secret store, and validate profiles with a doctor-style ping.
"""

from __future__ import annotations

from dataclasses import dataclass

from aida.config.secrets import get_secret
from aida.config.settings import ProviderProfile
from aida.providers.anthropic_ import AnthropicProvider
from aida.providers.base import LLMProvider
from aida.providers.openai_compat import OpenAICompatProvider


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


@dataclass
class ProfileValidation:
    """Result of validating one profile — mirrors ``aida.cli.doctor.CheckResult``
    in shape so callers (doctor, CLI ``/profile``) can format it the same way."""

    name: str
    ok: bool
    detail: str


async def validate_profile(profile: ProviderProfile) -> ProfileValidation:
    """Doctor-style check: can we even build and reach this profile's provider?

    Never raises — construction errors (bad kind) and reachability failures
    (ping() returning False) both come back as a non-ok ``ProfileValidation``.
    """
    try:
        provider = build_provider(profile)
    except UnknownProviderKindError as exc:
        return ProfileValidation(name=profile.name, ok=False, detail=str(exc))

    reachable = await provider.ping()
    if reachable:
        return ProfileValidation(name=profile.name, ok=True, detail=f"reachable ({profile.kind})")
    return ProfileValidation(
        name=profile.name, ok=False, detail=f"not reachable ({profile.kind}, {profile.base_url})"
    )


__all__ = ["ProfileValidation", "UnknownProviderKindError", "build_provider", "validate_profile"]
