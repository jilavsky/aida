"""Entry point for the ``aida`` console script and the ``aida-gui`` script.

Dispatch is deliberately not one shared ``argparse`` parser: each subcommand
(``doctor``, ``chat``, ``run``, ``config``) owns its own argument parsing so
it stays independently testable and ``aida chat --profile foo --skills a,b``
can grow chat-specific flags without touching this file.

``main_gui`` imports ``aida.ui.qt.app`` lazily, inside the function body —
PySide6 is an optional dependency (the ``gui`` extra); nothing under
``aida.cli``/``aida.core``/etc. may require it just to be imported, so this
module stays importable (and every other console script keeps working)
even when it isn't installed.
"""

from __future__ import annotations

import sys

from aida.config.logging_setup import configure_logging
from aida.config.settings import load_settings

_COMMANDS = {
    "doctor": "Report environment/config diagnostics",
    "chat": "Interactive chat (Phase 2)",
    "conversations": "List/resume/delete/export persisted conversations (Phase 4)",
    "workspace": "List/show/create/edit named workspaces (Phase 4)",
    "mcp": "Manage MCP servers, groups, and per-tool permissions (Phase 7)",
    "kb": "Manage RAG knowledge bases: config, build/update indexes, query (Phase 8)",
    "run": "Non-interactive single turn: `aida run --workspace W \"prompt\"` (Phase 10)",
    "workflow": "Run/list/show/validate stored named workflows (Phase 10)",
    "schedule": "Manage and run scheduled workflows (Phase 10)",
    "documents": "Inspect document extraction: `documents figures FILE`, `documents verify-ocr`",
    "config": "Show on-device config locations; `config secret set/get/delete` manages OS-keychain secrets",
}


def _print_top_level_help() -> None:
    print("usage: aida <command> [options]")
    print()
    print("commands:")
    width = max(len(name) for name in _COMMANDS) + 1
    for name, help_text in _COMMANDS.items():
        print(f"  {name:<{width}} {help_text}")


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
    if command == "conversations":
        from aida.cli.conversations import main as conversations_main

        return conversations_main(rest)
    if command == "workspace":
        from aida.cli.workspace_cmds import main as workspace_main

        return workspace_main(rest)
    if command == "mcp":
        from aida.cli.mcp_cmds import main as mcp_main

        return mcp_main(rest)
    if command == "kb":
        from aida.cli.kb_cmds import main as kb_main

        return kb_main(rest)
    if command == "run":
        from aida.cli.run import main as run_main

        return run_main(rest)
    if command == "workflow":
        from aida.cli.workflow_cmds import main as workflow_main

        return workflow_main(rest)
    if command == "schedule":
        from aida.cli.schedule_cmds import main as schedule_main

        return schedule_main(rest)
    if command == "documents":
        from aida.cli.documents_cmds import main as documents_main

        return documents_main(rest)
    # command == "config"
    from aida.cli.config_cmds import main as config_main

    return config_main(rest)


def main_gui() -> int:
    """The ``aida-gui`` entry point (Phase 5). ``argv`` isn't threaded
    through explicitly — ``aida.ui.qt.app.main()`` reads ``sys.argv``
    itself, same as every other console-script entry point in this file."""
    try:
        from aida.ui.qt.app import main as gui_main
    except ImportError:
        print("aida-gui: PySide6 isn't installed. Run `pip install -e '.[gui]'` (or `pip install aida-workbench[gui]`).")
        return 1
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
