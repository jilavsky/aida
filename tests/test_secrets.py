from __future__ import annotations

import keyring
from keyring.backend import KeyringBackend

from aida.config import secrets


class _InMemoryKeyring(KeyringBackend):
    """Minimal in-process keyring backend so tests never touch a real OS
    keychain and are reproducible in headless CI."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):  # noqa: D102
        return self._store.get((service, username))

    def set_password(self, service, username, password):  # noqa: D102
        self._store[(service, username)] = password

    def delete_password(self, service, username):  # noqa: D102
        self._store.pop((service, username), None)


def _use_memory_backend(monkeypatch):
    backend = _InMemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_set_and_get_secret(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "super-secret-value")
    assert secrets.get_secret("argo-claude") == "super-secret-value"


def test_missing_secret_returns_none(monkeypatch):
    _use_memory_backend(monkeypatch)
    assert secrets.get_secret("does-not-exist") is None


def test_env_override_wins(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "from-keyring")
    monkeypatch.setenv("AIDA_SECRET_ARGO_CLAUDE", "from-env")
    assert secrets.get_secret("argo-claude") == "from-env"


def test_delete_secret(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "value")
    secrets.delete_secret("argo-claude")
    assert secrets.get_secret("argo-claude") is None


def test_keyring_available_reports_bool(monkeypatch):
    _use_memory_backend(monkeypatch)
    assert secrets.keyring_available() is True
