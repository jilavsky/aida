"""Native coding tools (Phase 9): ``run_python_script``/``run_command`` —
structurally identical to ``aida.workspace.files``'s ``default_file_tools``
(closures capturing a ``SafetyGuard`` at build time, ``@_tool``-wrapped so
expected failures come back as a normal ``ToolResult`` instead of raising).

Safety split (see ``aida.workspace.safety.SafetyGuard``'s two Phase 9
methods): ``run_python_script`` runs a file already sitting in an allowed
folder — mode-governed like any other write/delete, via
``authorize_run_script``. ``run_command`` runs an arbitrary shell command —
additionally gated by the command allowlist, via ``authorize_execute``. Both
are a per-workspace on/off switch away from being registered at all
(``workspace.scripting_enabled``).
"""

from __future__ import annotations

import shlex
from typing import Any

from aida.coding.runner import RunResult, run_subprocess
from aida.coding.runner import run_python_script as _run_python_script
from aida.config.settings import WorkspaceConfig
from aida.core.tools import NativeTool, ToolResult, wrap_tool_errors
from aida.providers.base import ToolSchema
from aida.workspace.safety import ConfirmationDenied, SafetyGuard

_tool = wrap_tool_errors(ConfirmationDenied, OSError, TimeoutError, ValueError)


def _format_run_result(result: RunResult) -> str:
    lines = [f"exit code: {result.returncode}", f"duration: {result.duration_seconds:.2f}s"]
    if result.timed_out:
        lines.append("TIMED OUT — process was killed")
    if result.stdout:
        lines.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        lines.append(f"stderr:\n{result.stderr}")
    return "\n".join(lines)


def _default_cwd(workspace: WorkspaceConfig) -> str | None:
    if workspace.target_folder:
        return workspace.target_folder
    if workspace.source_folders:
        return workspace.source_folders[0]
    return None


def default_coding_tools(guard: SafetyGuard, *, workspace: WorkspaceConfig | None) -> dict[str, NativeTool]:
    """Empty for ``workspace=None`` (no folders configured, nothing to run
    in) or ``workspace.scripting_enabled=False`` — same "lazy, only if
    configured" philosophy as MCP servers and knowledge bases."""
    if workspace is None or not workspace.scripting_enabled:
        return {}

    @_tool
    async def run_python_script(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        args = list(arguments.get("args", []))
        candidate = await guard.authorize_read(path)
        if not candidate.is_file():
            return ToolResult(content=f"Not a file: {candidate}", is_error=True)
        await guard.authorize_run_script(candidate)
        result = await _run_python_script(candidate, args, interpreter=workspace.python_interpreter, cwd=candidate.parent)
        return ToolResult(content=_format_run_result(result), is_error=result.timed_out or result.returncode != 0)

    @_tool
    async def run_command(arguments: dict[str, Any]) -> ToolResult:
        command = arguments["command"]
        cwd_arg = arguments.get("cwd") or _default_cwd(workspace)
        if cwd_arg is None:
            return ToolResult(
                content="No cwd given and this workspace has no target/source folder to default to.",
                is_error=True,
            )
        cwd = await guard.authorize_execute(command, cwd_arg)
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return ToolResult(content=f"Could not parse command {command!r}: {exc}", is_error=True)
        result = await run_subprocess(argv, cwd=cwd)
        return ToolResult(content=_format_run_result(result), is_error=result.timed_out or result.returncode != 0)

    tools = [
        NativeTool(
            schema=ToolSchema(
                name="run_python_script",
                description="Run a Python script that already lives in an allowed folder, with optional arguments.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Script to run."},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command-line arguments to pass to the script.",
                        },
                    },
                    "required": ["path"],
                },
            ),
            func=run_python_script,
        ),
        NativeTool(
            schema=ToolSchema(
                name="run_command",
                description=(
                    "Run a shell command from the workspace's command allowlist. Anything not on the "
                    "allowlist requires confirmation."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The full command line to run."},
                        "cwd": {
                            "type": "string",
                            "description": "Working directory (defaults to the workspace's target/source folder).",
                        },
                    },
                    "required": ["command"],
                },
            ),
            func=run_command,
        ),
    ]
    return {t.schema.name: t for t in tools}


__all__ = ["default_coding_tools"]
