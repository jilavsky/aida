from __future__ import annotations

import pytest

from aida.config.settings import EmbeddingProfile, ProviderProfile
from aida.providers.anthropic_ import AnthropicProvider
from aida.providers.openai_compat import OpenAICompatProvider
from aida.providers.openai_compat_embeddings import OpenAICompatEmbeddings
from aida.providers.profiles import (
    ProfileValidation,
    UnknownProviderKindError,
    build_embeddings_provider,
    build_provider,
    validate_embedding_profile,
    validate_profile,
)


def test_build_provider_openai_compat(monkeypatch):
    monkeypatch.setattr("aida.providers.profiles.get_secret", lambda name: None)
    profile = ProviderProfile(name="ollama-local", kind="openai_compat", base_url="http://x", model="m")
    provider = build_provider(profile)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.model == "m"


def test_build_provider_anthropic(monkeypatch):
    monkeypatch.setattr("aida.providers.profiles.get_secret", lambda name: "fake-secret")
    profile = ProviderProfile(name="argo-claude", kind="anthropic", base_url="https://x", model="claude-x", secret_ref="argo-claude")
    provider = build_provider(profile)
    assert isinstance(provider, AnthropicProvider)


def test_build_provider_unknown_kind_raises():
    profile = ProviderProfile(name="weird", kind="not-a-real-kind", model="m")
    with pytest.raises(UnknownProviderKindError):
        build_provider(profile)


def test_build_provider_resolves_secret_via_get_secret(monkeypatch):
    seen = {}

    def fake_get_secret(name):
        seen["name"] = name
        return "resolved-secret"

    monkeypatch.setattr("aida.providers.profiles.get_secret", fake_get_secret)
    profile = ProviderProfile(name="argo-claude", kind="anthropic", model="claude-x", secret_ref="argo-claude")
    build_provider(profile)
    assert seen["name"] == "argo-claude"


@pytest.mark.asyncio
async def test_validate_profile_ok(monkeypatch):
    class _FakeProvider:
        async def ping(self):
            return True

    monkeypatch.setattr("aida.providers.profiles.build_provider", lambda profile: _FakeProvider())
    profile = ProviderProfile(name="x", kind="openai_compat", model="m")

    result = await validate_profile(profile)
    assert isinstance(result, ProfileValidation)
    assert result.ok is True
    assert result.name == "x"


@pytest.mark.asyncio
async def test_validate_profile_unreachable(monkeypatch):
    class _FakeProvider:
        async def ping(self):
            return False

    monkeypatch.setattr("aida.providers.profiles.build_provider", lambda profile: _FakeProvider())
    profile = ProviderProfile(name="x", kind="openai_compat", model="m", base_url="http://nowhere")

    result = await validate_profile(profile)
    assert result.ok is False
    assert "not reachable" in result.detail


@pytest.mark.asyncio
async def test_validate_profile_bad_kind_reports_not_raises():
    profile = ProviderProfile(name="x", kind="bogus", model="m")
    result = await validate_profile(profile)
    assert result.ok is False
    assert "bogus" in result.detail


# --- embedding profiles (Phase 8) -------------------------------------------


def test_build_embeddings_provider_openai_compat(monkeypatch):
    monkeypatch.setattr("aida.providers.profiles.get_secret", lambda name: None)
    profile = EmbeddingProfile(name="argo-embed", base_url="http://x", model="text-embedding-3-small")
    provider = build_embeddings_provider(profile)
    assert isinstance(provider, OpenAICompatEmbeddings)
    assert provider.model == "text-embedding-3-small"


def test_build_embeddings_provider_unknown_kind_raises():
    profile = EmbeddingProfile(name="weird", kind="not-a-real-kind", model="m")
    with pytest.raises(UnknownProviderKindError):
        build_embeddings_provider(profile)


def test_build_embeddings_provider_resolves_secret_via_get_secret(monkeypatch):
    seen = {}

    def fake_get_secret(name):
        seen["name"] = name
        return "resolved-secret"

    monkeypatch.setattr("aida.providers.profiles.get_secret", fake_get_secret)
    profile = EmbeddingProfile(name="argo-embed", model="text-embedding-3-small", secret_ref="argo-claude")
    build_embeddings_provider(profile)
    assert seen["name"] == "argo-claude"


@pytest.mark.asyncio
async def test_validate_embedding_profile_ok(monkeypatch):
    class _FakeProvider:
        async def ping(self):
            return True

        async def aclose(self):
            pass

    monkeypatch.setattr("aida.providers.profiles.build_embeddings_provider", lambda profile: _FakeProvider())
    profile = EmbeddingProfile(name="x", model="m")

    result = await validate_embedding_profile(profile)
    assert isinstance(result, ProfileValidation)
    assert result.ok is True


@pytest.mark.asyncio
async def test_validate_embedding_profile_unreachable(monkeypatch):
    class _FakeProvider:
        async def ping(self):
            return False

        async def aclose(self):
            pass

    monkeypatch.setattr("aida.providers.profiles.build_embeddings_provider", lambda profile: _FakeProvider())
    profile = EmbeddingProfile(name="x", model="m", base_url="http://nowhere")

    result = await validate_embedding_profile(profile)
    assert result.ok is False
    assert "not reachable" in result.detail


@pytest.mark.asyncio
async def test_validate_embedding_profile_bad_kind_reports_not_raises():
    profile = EmbeddingProfile(name="x", kind="bogus", model="m")
    result = await validate_embedding_profile(profile)
    assert result.ok is False
    assert "bogus" in result.detail
