"""Secret storage: OS keychain via ``keyring``, with env-var override.

Hard rule (PLAN.md §4): secrets never touch ``~/.aida/*.yaml`` or ``*.json``.
``providers.yaml`` stores only a ``secret_ref`` (a profile name); the actual
API key / ANL username lives in the OS keychain, or — for headless / CI /
pipeline use — in an ``AIDA_SECRET_<PROFILE>`` environment variable, checked
first so it always wins in non-interactive contexts.

This module never logs a secret value, and its own returned values must
never be handed to any of the YAML/JSON writers in ``aida.config.settings``.
"""

from __future__ import annotations

import contextlib

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "aida"
ENV_PREFIX = "AIDA_SECRET_"


def _env_var_name(profile: str) -> str:
    return ENV_PREFIX + profile.upper().replace("-", "_")


def get_secret(profile: str) -> str | None:
    """Return the secret for ``profile``, or None if not set anywhere.

    Lookup order: environment variable override, then OS keychain.
    """
    import os

    env_val = os.environ.get(_env_var_name(profile))
    if env_val is not None:
        return env_val

    try:
        return keyring.get_password(SERVICE_NAME, profile)
    except KeyringError:
        return None


def set_secret(profile: str, value: str) -> None:
    """Store a secret for ``profile`` in the OS keychain.

    Does not touch environment variables — an env override, if set, will
    keep taking precedence over whatever is stored here until unset.
    """
    keyring.set_password(SERVICE_NAME, profile, value)


def delete_secret(profile: str) -> None:
    """Remove a stored secret for ``profile`` from the OS keychain, if present."""
    with contextlib.suppress(KeyringError):
        keyring.delete_password(SERVICE_NAME, profile)


def keyring_available() -> bool:
    """Best-effort check that a usable keyring backend is configured.

    Used by ``aida doctor`` — a missing/broken backend (common on fresh
    Linux CI images) is reported, not raised.
    """
    try:
        backend = keyring.get_keyring()
        return backend is not None and "fail" not in type(backend).__name__.lower()
    except Exception:
        return False
