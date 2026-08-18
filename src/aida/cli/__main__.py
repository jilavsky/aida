"""Entry point for the ``aida`` console script (and ``aida-gui`` stub)."""

from __future__ import annotations

import argparse
import sys

from aida.config.logging_setup import configure_logging
from aida.config.settings import load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida", description="AIDA — AI Data Assistant")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Report environment/config diagnostics")
    sub.add_parser("chat", help="Interactive chat (Phase 2)")
    sub.add_parser("run", help="Run a stored workflow headlessly (Phase 10)")
    sub.add_parser("config", help="Show on-device config locations")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.app.log_level)

    if args.command == "doctor":
        from aida.cli.doctor import main as doctor_main

        return doctor_main()
    if args.command == "chat":
        from aida.cli.chat import main as chat_main

        return chat_main()
    if args.command == "run":
        from aida.cli.run import main as run_main

        return run_main()
    if args.command == "config":
        from aida.cli.config_cmds import main as config_main

        return config_main()

    parser.print_help()
    return 0


def main_gui() -> int:
    """Stub for the ``aida-gui`` entry point; the real GUI arrives in Phase 5."""
    print("aida-gui: not yet implemented (arrives in Phase 5 — see PLAN.md §10).")
    print("Use `aida doctor` or `aida chat` (Phase 2) for now.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
