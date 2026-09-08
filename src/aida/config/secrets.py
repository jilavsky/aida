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


def env_var_name(profile: str) -> str:
    """The environment variable name checked for ``profile``'s secret
    before the OS keychain — public (not just an internal helper) so
    ``aida doctor``'s non-interactive-reachability check
    (Phase 10) can name it in a diagnostic without duplicating the
    ``AIDA_SECRET_<PROFILE>`` naming convention."""
    return ENV_PREFIX + profile.upper().replace("-", "_")


def get_secret(profile: str) -> str | None:
    """Return the secret for ``profile``, or None if not set anywhere.

    Lookup order: environment variable override, then OS keychain.
    """
    import os

    env_val = os.environ.get(env_var_name(profile))
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


def describe_keyring_error(exc: KeyringError) -> str:
    """Turn a ``set_secret`` failure into guidance, shared by the GUI
    Providers dialog and ``aida config secret set`` so both give identical
    advice.

    ``KeyringLocked`` is the common case on an APS beamline control machine:
    the Linux Secret Service backend needs a running, *unlocked* keyring
    daemon (gnome-keyring or kwallet) behind a D-Bus session, and a bare
    console/SSH login typically has neither — there is no desktop session to
    prompt for an unlock, and no system package fixes a daemon that was
    never started. The supported escape hatch, already documented in
    ``docs/providers-and-secrets.md``, is the ``AIDA_SECRET_<PROFILE>``
    environment variable: it is checked before the keychain and needs no
    keyring daemon at all.
    """
    detail = str(exc) or type(exc).__name__
    return (
        f"Could not store the secret in the OS keychain ({detail}). This is "
        "common on a headless/console Linux login, where the Secret Service "
        "backend needs a running, unlocked keyring daemon (gnome-keyring or "
        "kwallet) that isn't available. Set the secret via the "
        "AIDA_SECRET_<PROFILE> environment variable instead — see "
        "docs/providers-and-secrets.md#secrets — no keychain needed."
    )
