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
import getpass
import sys
from pathlib import Path

from aida.config.paths import app_dir, ensure_records_dir
from aida.portability import CONFLICT_POLICIES


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
    set_parser.add_argument(
        "profile", help="Provider profile name — providers.yaml's secret_ref value"
    )
    set_parser.add_argument(
        "value",
        nargs="?",
        default=None,
        help=(
            "The secret itself (API key, token, username, ...). Omit it to be prompted without "
            "echo — passing it here puts the secret in your shell history and in `ps` output."
        ),
    )

    get_parser = secret_sub.add_parser(
        "get", help="Report whether a secret is set for a profile (never prints the value itself)"
    )
    get_parser.add_argument("profile")

    delete_parser = secret_sub.add_parser("delete", help="Remove a stored secret for a profile")
    delete_parser.add_argument("profile")

    export_parser = subparsers.add_parser(
        "export",
        help="Write this install's setup to a portable bundle (no secrets)",
        description=(
            "Export provider profiles, workspaces, MCP servers, knowledge bases, "
            "schedules, workflows, skills and prompts to a single zip file that can "
            "be imported on another machine. Machine-specific paths are replaced with "
            "portable tokens. No secret value is ever written to a bundle."
        ),
    )
    export_parser.add_argument("destination", help="Path of the bundle to write (a .zip file)")
    export_parser.add_argument(
        "--include-personal",
        action="store_true",
        help=(
            "Also export your personal context, user names and private workspace notes. "
            "For moving to your own second machine — not for a bundle you send to someone else."
        ),
    )

    import_parser = subparsers.add_parser(
        "import",
        help="Merge a setup bundle into this install",
        description=(
            "Merge a bundle written by `aida config export` into this install. "
            "Never destructive by default: an item whose name already exists is skipped "
            "and listed in the report."
        ),
    )
    import_parser.add_argument("source", help="Path of the bundle to import")
    import_parser.add_argument(
        "--on-conflict",
        choices=CONFLICT_POLICIES,
        default="skip",
        help=(
            "What to do when a name already exists here: skip it (default), overwrite it, "
            "or import it under a '<name> (imported)' name"
        ),
    )
    import_parser.add_argument(
        "--app-settings",
        action="store_true",
        help=(
            "Also apply the bundle's general settings (safety mode, allowed folders, "
            "iteration/context caps, records and scratch folders) to config.yaml. Off by "
            "default so importing someone else's workspaces cannot change your safety mode."
        ),
    )
    import_parser.add_argument(
        "--list",
        action="store_true",
        help="List what the bundle contains and exit, importing nothing",
    )
    import_parser.add_argument(
        "--only",
        action="append",
        metavar="KIND:NAME",
        default=[],
        help=(
            "Import just this item (repeatable, or comma-separated), e.g. "
            "--only workspace:analysis. Whatever it needs to work — its profile, skills, "
            "knowledge bases and MCP servers — comes along automatically. "
            "Run --list to see the available names."
        ),
    )
    import_parser.add_argument(
        "--no-deps",
        action="store_true",
        help=(
            "With --only, import exactly what was named and nothing else. "
            "Likely to produce a workspace that fails validation — use when you know "
            "the rest is already configured here."
        ),
    )
    import_parser.add_argument(
        "--map",
        action="append",
        metavar="FROM=TO",
        default=[],
        dest="path_map",
        help=(
            "Rewrite a path from the bundle, e.g. --map '${HOME}/Experiments=/data/usaxs'. "
            "Matches by prefix, so one mapping redirects a whole tree. Repeatable. "
            "Run --check to see which paths need one."
        ),
    )
    import_parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Show exactly what would be imported and which paths do not resolve here, "
            "then exit without writing anything"
        ),
    )

    return parser


def _secret_main(args: argparse.Namespace) -> int:
    from keyring.errors import KeyringError

    from aida.config.secrets import delete_secret, describe_keyring_error, get_secret, set_secret

    if args.secret_action == "set":
        # Taking the secret as an argv value means it lands in shell
        # history and is visible to anyone running `ps` for as long as the
        # command runs — on a shared beamline machine, both matter. The
        # argument stays supported (scripts and the existing docs use it),
        # but omitting it now prompts without echo instead of being an
        # error, and that is the form worth recommending.
        value = args.value
        if value is None:
            value = getpass.getpass(f"Secret for profile {args.profile!r} (not echoed): ")
        if not value:
            print("No secret entered — nothing stored.")
            return 1
        try:
            set_secret(args.profile, value)
        except KeyringError as exc:
            print(describe_keyring_error(exc), file=sys.stderr)
            return 1
        print(f"Stored a secret for profile {args.profile!r} in the OS keychain.")
        print(
            f"Now set providers.yaml's matching profile's secret_ref to {args.profile!r} (a reference name, not the secret itself)."
        )
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


def _export_main(args: argparse.Namespace) -> int:
    from aida.portability import export_bundle, format_export

    destination = Path(args.destination).expanduser()
    if destination.is_dir():
        print(
            f"{destination} is a directory — give the bundle a file name, "
            f"e.g. {destination / 'aida-setup.zip'}",
            file=sys.stderr,
        )
        return 1
    try:
        result = export_bundle(destination, include_personal=args.include_personal)
    except OSError as exc:
        print(f"Could not write {destination}: {exc}", file=sys.stderr)
        return 1
    print(format_export(result))
    return 0


def _split_selectors(values: list[str]) -> list[str]:
    """``--only a:b --only c:d`` and ``--only a:b,c:d`` mean the same thing.
    Nobody should have to remember which form this particular command
    wanted."""
    out: list[str] = []
    for value in values:
        out.extend(part for part in (p.strip() for p in value.split(",")) if part)
    return out


def _import_main(args: argparse.Namespace) -> int:
    from aida.portability import (
        BundleError,
        expand_selection,
        format_contents,
        format_import,
        format_path_issues,
        import_bundle,
        inspect_paths,
        parse_overrides,
        parse_selection,
        read_contents,
    )
    from aida.portability.paths_map import PathMapper

    try:
        contents = read_contents(args.source)
    except BundleError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not read {args.source}: {exc}", file=sys.stderr)
        return 1

    if args.list:
        print(format_contents(contents))
        return 0

    selectors = _split_selectors(args.only)
    select = None
    if selectors:
        select, problems = parse_selection(selectors, contents)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            print("Run with --list to see what this bundle contains.", file=sys.stderr)
            return 1

    overrides, map_problems = parse_overrides(args.path_map)
    if map_problems:
        for problem in map_problems:
            print(problem, file=sys.stderr)
        return 1

    follow = not args.no_deps
    if args.check:
        chosen = expand_selection(contents, select, follow_dependencies=follow)
        issues = inspect_paths(
            contents,
            PathMapper(overrides=overrides),
            chosen,
            include_app=args.app_settings,
        )
        report = import_bundle(
            args.source,
            conflict=args.on_conflict,
            apply_app_settings=args.app_settings,
            select=select,
            follow_dependencies=follow,
            path_overrides=overrides,
            dry_run=True,
        )
        print(format_import(report))
        print()
        print(format_path_issues(issues))
        return 0

    try:
        report = import_bundle(
            args.source,
            conflict=args.on_conflict,
            apply_app_settings=args.app_settings,
            select=select,
            follow_dependencies=follow,
            path_overrides=overrides,
        )
    except OSError as exc:
        print(f"Could not import {args.source}: {exc}", file=sys.stderr)
        return 1
    print(format_import(report))
    # An unresolved executable is the one outcome that leaves the install in a
    # state where something will fail later, at MCP-server-start time, far from
    # this command — so it is worth a non-zero exit for anyone scripting a
    # machine setup, even though the import itself succeeded.
    return 2 if report.unresolved_commands else 0


def _effective_records_dir() -> Path:
    """The records dir the app will actually use, honoring ``config.yaml``'s
    ``records_dir`` override.

    Bare ``aida config`` used to print — and, via ``ensure_records_dir()``,
    *create* — the default ``~/Documents/Aida`` regardless of what the
    user's config said, so it reported a directory their session never
    touches and left an empty one behind. ``aida doctor`` was explicitly
    fixed for this (its own ``_effective_records_dir``); this command was
    missed. A config that fails to load at all falls back to the default,
    since printing where things live must not be the thing that crashes."""
    try:
        from aida.config.settings import load_settings

        configured = load_settings().app.records_dir
    except Exception:  # noqa: BLE001 - a broken config must not break `aida config`
        configured = None
    return ensure_records_dir(Path(configured) if configured else None)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else []

    if not argv:
        print(f"AIDA config directory: {app_dir()}")
        print(f"AIDA records directory: {_effective_records_dir()}")
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "secret":
        return _secret_main(args)
    if args.subcommand == "export":
        return _export_main(args)
    if args.subcommand == "import":
        return _import_main(args)

    parser.print_help()
    return 1
