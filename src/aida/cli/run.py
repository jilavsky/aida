"""``aida run`` — headless execution of a single agent turn (Phase 10,
planning/phase10_scheduling_design.md §3): the layer everything else in
this phase is built on. No stored workflow, no schedule — one prompt, one
workspace, one turn, an exit code a shell script can branch on.

Never prompts: the headless confirm callback (``aida.core.headless``)
always resolves immediately, so a filesystem/MCP safety check it doesn't
explicitly approve is simply declined rather than blocking — a scheduled or
piped invocation with nobody watching a terminal must never hang waiting
for an answer that will never come.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from aida.config.settings import Settings, load_settings
from aida.core.events import AgentError, MessageFinished, TextFinished, ToolCallFinished
from aida.core.headless import build_headless_confirm_callback
from aida.core.session import (
    UnknownMcpServerError,
    UnknownProfileError,
    UnknownWorkspaceError,
    start_session,
)
from aida.providers.base import ImageRef

#: Exit codes, distinct so a pipeline can tell "the turn errored" from "the
#: workspace/profile/MCP config was wrong" rather than everything collapsing
#: to a bare nonzero. There is deliberately no separate "confirmation
#: required and refused" code: a denied confirmation never raises past
#: ``session.send()`` — ``AgentLoop`` already turns it into an ordinary
#: ``ToolCallFinished(is_error=True)`` the model can see and react to (same
#: as any other tool error, see ``aida.core.confirmation.ConfirmationDenied``'s
#: docstring) — so it is visible in ``--json``'s ``tool_calls`` list rather
#: than collapsed into its own exit code.
EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_CONFIG_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida run")
    parser.add_argument("prompt", nargs="?", default=None, help="The prompt to run (reads stdin if omitted)")
    parser.add_argument("--workspace", required=True, help="Named workspace from workspaces.yaml")
    parser.add_argument("--profile", default="", help="Provider profile name (default: the workspace's own)")
    parser.add_argument("--skills", default="", help="Comma-separated skill names to load into the system context")
    parser.add_argument(
        "--mcp-group", default="", help="Named MCP server group (default: the workspace's own)"
    )
    parser.add_argument("--mcp", default="", help="Comma-separated MCP server names, bypassing groups")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="FILE",
        dest="inputs",
        help="An image file to attach as vision input (repeatable)",
    )
    parser.add_argument(
        "--yes-in-allowed",
        action="store_true",
        help="Auto-approve writes/deletes inside the workspace's own allowed folders. "
        "Never approves anything outside them, and never approves an MCP tool's "
        "'confirm before run' flag — see --preapprove-tool for that.",
    )
    parser.add_argument(
        "--preapprove-tool",
        action="append",
        default=[],
        metavar="SERVER__TOOL",
        dest="preapproved_tools",
        help="A namespaced MCP tool name to approve despite its 'confirm before run' flag (repeatable)",
    )
    parser.add_argument(
        "--user",
        default="",
        help="Organization label for the conversation this run creates (default: $AIDA_USER, else "
        "config.yaml's active_user). Labels and filters only — not authentication.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON result summary")
    return parser


def _read_prompt(args: argparse.Namespace) -> str | None:
    if args.prompt is not None:
        return args.prompt
    if sys.stdin.isatty():
        return None
    text = sys.stdin.read().strip()
    return text or None


async def _async_main(
    settings: Settings,
    *,
    prompt: str,
    workspace_name: str,
    profile_name: str | None,
    skill_names: list[str],
    mcp_group: str,
    mcp_names: list[str],
    images: list[ImageRef],
    yes_in_allowed: bool,
    preapproved_tools: set[str],
    as_json: bool,
    user: str | None = None,
) -> int:
    confirm_callback = build_headless_confirm_callback(
        yes_in_allowed=yes_in_allowed, preapproved_tools=preapproved_tools
    )
    try:
        session, mcp_manager = await start_session(
            settings,
            profile_name=profile_name,
            workspace_name=workspace_name,
            skill_names=skill_names,
            mcp_group=mcp_group,
            mcp_names=mcp_names,
            confirm_callback=confirm_callback,
            user=user,
        )
    except (UnknownProfileError, UnknownWorkspaceError, UnknownMcpServerError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    stop_reason: str | None = None
    error: str | None = None
    exit_code = EXIT_OK
    try:
        async for event in session.send(prompt, images=images):
            if isinstance(event, ToolCallFinished):
                tool_calls.append({"tool_name": event.tool_name, "is_error": event.is_error})
            elif isinstance(event, MessageFinished):
                stop_reason = event.stop_reason
            elif isinstance(event, AgentError):
                error = f"{event.layer}: {event.message}"
                exit_code = EXIT_STEP_FAILED
            elif isinstance(event, TextFinished):
                text_parts.append(event.text)
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()

    reply = text_parts[-1] if text_parts else ""
    if as_json:
        print(
            json.dumps(
                {
                    "ok": exit_code == EXIT_OK,
                    "reply": reply,
                    "stop_reason": stop_reason,
                    "tool_calls": tool_calls,
                    "error": error,
                    "conversation_id": session.recorder.conversation_id if session.recorder else None,
                },
                indent=2,
            )
        )
    else:
        if reply:
            print(reply)
        if error:
            print(f"[error] {error}", file=sys.stderr)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    prompt = _read_prompt(args)
    if not prompt:
        print("aida run: no prompt given (pass it as an argument or pipe it on stdin)", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    skill_names = [s.strip() for s in args.skills.split(",") if s.strip()]
    mcp_names = [s.strip() for s in args.mcp.split(",") if s.strip()]
    images = [ImageRef(path=path) for path in args.inputs]

    settings = load_settings()
    return asyncio.run(
        _async_main(
            settings,
            prompt=prompt,
            workspace_name=args.workspace,
            profile_name=args.profile or None,
            skill_names=skill_names,
            mcp_group=args.mcp_group,
            mcp_names=mcp_names,
            images=images,
            yes_in_allowed=args.yes_in_allowed,
            preapproved_tools=set(args.preapproved_tools),
            as_json=args.json,
            user=args.user or None,
        )
    )
