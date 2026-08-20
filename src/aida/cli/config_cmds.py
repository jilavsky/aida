"""``aida config`` — inspect/edit on-device configuration.

Phase 1 provided just enough to show where things live (bare ``aida
config`` still does exactly that, unchanged). ``aida config secret ...``
fills a real gap found while reviewing a user's real ``~/.aida``: PLAN.md's
hard rule is "secrets never touch ``~/.aida/*.yaml`` or ``*.json`` — the
actual API key / ANL username lives in the OS keychain", and
``aida.config.secrets`` (``get_secret``/``set_secret``/``delete_secret``)
has existed since Phase 1 to do exactly that — but nothing ever actually
called ``set_secret``/``delete_secret``. There was no supported way to get
a secret *into* the keychain at all, so a ``providers.yaml``'s comment like
``# `aida config secret set argo-claude <ANL username>``` referenced a
command that didn't exist, and the only way forward was pasting the raw
secret directly into ``secret_ref`` in plaintext YAML — exactly the thing
PLAN.md's hard rule says must never happen.
"""

from __future__ import annotations

import argparse

from aida.config.paths import app_dir, ensure_records_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida config")
    subparsers = parser.add_subparsers(dest="subcommand")

    secret_parser = subparsers.add_parser(
        "secret", help="Manage a provider profile's secret in the OS keychain"
    )
    secret_sub = secret_parser.add_subparsers(dest="secret_action")

    set_parser = secret_sub.add_parser(
        "set", help="Store a secret for a provider profile (matches providers.yaml's secret_ref)"
    )
    set_parser.add_argument("profile", help="Provider profile name — providers.yaml's secret_ref value")
    set_parser.add_argument("value", help="The secret itself (API key, token, username, ...)")

    get_parser = secret_sub.add_parser(
        "get", help="Report whether a secret is set for a profile (never prints the value itself)"
    )
    get_parser.add_argument("profile")

    delete_parser = secret_sub.add_parser("delete", help="Remove a stored secret for a profile")
    delete_parser.add_argument("profile")

    return parser


def _secret_main(args: argparse.Namespace) -> int:
    from aida.config.secrets import delete_secret, get_secret, set_secret

    if args.secret_action == "set":
        set_secret(args.profile, args.value)
        print(f"Stored a secret for profile {args.profile!r} in the OS keychain.")
        print(f"Now set providers.yaml's matching profile's secret_ref to {args.profile!r} (a reference name, not the secret itself).")
        return 0
    if args.secret_action == "get":
        # Deliberately doesn't print the value — this module's own
        # docstring rule ("never logs a secret value") extends to "never
        # echoes one back to a terminal that might be recorded/shared
        # either" by the same logic. Use `keyring get aida <profile>`
        # directly (same OS keychain, same entry) if you genuinely need
        # to see the raw value.
        value = get_secret(args.profile)
        print(f"profile {args.profile!r}: {'set' if value else 'not set'}")
        return 0
    if args.secret_action == "delete":
        delete_secret(args.profile)
        print(f"Removed any stored secret for profile {args.profile!r}.")
        return 0

    print("usage: aida config secret {set,get,delete} <profile> [value]")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else []

    if not argv:
        print(f"AIDA config directory: {app_dir()}")
        print(f"AIDA records directory: {ensure_records_dir()}")
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "secret":
        return _secret_main(args)

    parser.print_help()
    return 1
