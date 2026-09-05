"""``aida-gui`` entry point (PLAN.md Phase 5): creates the ``QApplication``,
the background asyncio loop thread, the main window, and runs the Qt event
loop. Accepts the same session-selection flags as ``aida chat``
(``--profile``/``--workspace``/``--skills``/``--mcp-group``/``--mcp``) so a
user who already has a shell habit from Phase 2/3/4 can carry it over.

With no ``--workspace``/``--profile`` flag, this falls back to
``settings.app.last_workspace_name``/``last_profile_name`` (Phase 6+: saved
by ``MainWindow`` every time a session actually starts — see
``MainWindow._save_last_session_selection``), so the app reopens the same
workspace/profile it was last used with instead of landing on "No profile
given" on every launch. Only on a genuine first run (nothing saved yet, and
no flag given) does ``ChatBridge.start`` report "no profile given" via
``startup_failed``, shown as a dialog rather than the app failing to launch
outright.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from aida.config.logging_setup import configure_logging
from aida.config.settings import Settings, load_settings
from aida.ui.qt._qt import QApplication
from aida.ui.qt.bridge import AsyncLoopThread
from aida.ui.qt.icon import app_icon
from aida.ui.qt.main_window import MainWindow
from aida.ui.qt.window_state import apply_font_size


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida-gui")
    parser.add_argument("--profile", default="", help="Provider profile name from providers.yaml")
    parser.add_argument("--workspace", default="", help="Named workspace from workspaces.yaml")
    parser.add_argument("--skills", default="", help="Comma-separated skill names to load")
    parser.add_argument(
        "--mcp-group", default="", help="Named MCP server group from mcp.json to enable"
    )
    parser.add_argument(
        "--mcp", default="", help="Comma-separated MCP server names to enable directly"
    )
    return parser


def _resolve_start_kwargs(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    """Separated from ``main`` so the "fall back to last-used
    workspace/profile when no flag is given" precedence is unit-testable
    without constructing a real ``QApplication``."""
    skill_names = [s.strip() for s in args.skills.split(",") if s.strip()]
    mcp_names = [s.strip() for s in args.mcp.split(",") if s.strip()]
    return {
        "profile_name": args.profile or settings.app.last_profile_name,
        "workspace_name": args.workspace or settings.app.last_workspace_name,
        "skill_names": skill_names,
        "mcp_group": args.mcp_group,
        "mcp_names": mcp_names,
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    settings = load_settings()
    configure_logging(settings.app.log_level)

    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    apply_font_size(app, settings.app)

    loop_thread = AsyncLoopThread()
    loop_thread.start()
    loop_thread.wait_until_ready()

    start_kwargs = _resolve_start_kwargs(args, settings)
    window = MainWindow(settings, loop_thread, start_kwargs=start_kwargs)
    window.show()

    exit_code = app.exec()
    loop_thread.stop()
    return exit_code


__all__ = ["main"]
