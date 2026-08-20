"""Tests for aida.cli.config_cmds — bare ``aida config`` (Phase 1, unchanged)
and ``aida config secret set/get/delete`` (new: the previously-missing way
to actually get a secret into the OS keychain — see the module docstring
for why this was a real gap, found while reviewing a user's real
providers.yaml, which had a raw-looking API key pasted directly into
secret_ref because there was no supported command to do it properly)."""

from __future__ import annotations

import keyring
from keyring.backend import KeyringBackend

from aida.cli.config_cmds import main
from aida.config import secrets


class _InMemoryKeyring(KeyringBackend):
    """Same in-process fake as test_secrets.py — never touch a real OS
    keychain in tests, reproducible in headless CI."""

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


def test_bare_config_prints_directories(aida_home, records_home, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "AIDA config directory:" in out
    assert "AIDA records directory:" in out


def test_secret_set_stores_in_keychain_not_printed(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    exit_code = main(["secret", "set", "argo-claude", "super-secret-value"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "super-secret-value" not in out  # never echoed back
    assert secrets.get_secret("argo-claude") == "super-secret-value"


def test_secret_get_reports_set_without_printing_value(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "super-secret-value")

    exit_code = main(["secret", "get", "argo-claude"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "set" in out
    assert "super-secret-value" not in out


def test_secret_get_reports_not_set_for_unknown_profile(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    exit_code = main(["secret", "get", "does-not-exist"])
    assert exit_code == 0
    assert "not set" in capsys.readouterr().out


def test_secret_delete_removes_it(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "value")

    exit_code = main(["secret", "delete", "argo-claude"])
    assert exit_code == 0
    assert secrets.get_secret("argo-claude") is None


def test_secret_delete_of_unset_profile_is_a_noop(monkeypatch):
    _use_memory_backend(monkeypatch)
    assert main(["secret", "delete", "does-not-exist"]) == 0


def test_secret_with_no_action_prints_usage(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    exit_code = main(["secret"])
    assert exit_code == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_secret_value_with_spaces_survives_as_a_single_argv_token(monkeypatch):
    # Mirrors what shell quoting (`aida config secret set p "a value"`)
    # produces once the shell has already split argv — argparse just sees
    # one token containing a space, same as any other value.
    _use_memory_backend(monkeypatch)
    main(["secret", "set", "argo-claude", "a value with spaces"])
    assert secrets.get_secret("argo-claude") == "a value with spaces"
