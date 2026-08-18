"""Entry point for the ``aida`` console script (and ``aida-gui`` stub).

Dispatch is deliberately not one shared ``argparse`` parser: each subcommand
(``doctor``, ``chat``, ``run``, ``config``) owns its own argument parsing so
it stays independently testable and ``aida chat --profile foo --skills a,b``
can grow chat-specific flags without touching this file.
"""

from __future__ import annotations

import sys

from aida.config.logging_setup import configure_logging
from aida.config.settings import load_settings

_COMMANDS = {
    "doctor": "Report environment/config diagnostics",
    "chat": "Interactive chat (Phase 2)",
    "run": "Run a stored workflow headlessly (Phase 10)",
    "config": "Show on-device config locations",
}


def _print_top_level_help() -> None:
    print("usage: aida <command> [options]")
    print()
    print("commands:")
    for name, help_text in _COMMANDS.items():
        print(f"  {name:<8} {help_text}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv or argv[0] in ("-h", "--help"):
        _print_top_level_help()
        return 0

    command, rest = argv[0], argv[1:]
    if command not in _COMMANDS:
        print(f"Unknown command: {command!r}")
        _print_top_level_help()
        return 1

    settings = load_settings()
    configure_logging(settings.app.log_level)

    if command == "doctor":
        from aida.cli.doctor import main as doctor_main

        return doctor_main()
    if command == "chat":
        from aida.cli.chat import main as chat_main

        return chat_main(rest)
    if command == "run":
        from aida.cli.run import main as run_main

        return run_main(rest)
    # command == "config"
    from aida.cli.config_cmds import main as config_main

    return config_main(rest)


def main_gui() -> int:
    """Stub for the ``aida-gui`` entry point; the real GUI arrives in Phase 5."""
    print("aida-gui: not yet implemented (arrives in Phase 5 — see PLAN.md §10).")
    print("Use `aida doctor` or `aida chat` (Phase 2) for now.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
