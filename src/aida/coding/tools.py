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

from pathlib import Path
from typing import Any

from aida.artifacts.base import new_artifact_id
from aida.coding.runner import RunResult, _BoundedCapture, run_subprocess
from aida.coding.runner import run_python_script as _run_python_script
from aida.config.settings import WorkspaceConfig
from aida.core.tools import NativeTool, ToolResult, wrap_tool_errors
from aida.providers.base import ToolSchema
from aida.workspace.command_allowlist import split_command
from aida.workspace.safety import ConfirmationDenied, SafetyGuard

_tool = wrap_tool_errors(ConfirmationDenied, OSError, TimeoutError, ValueError)

#: What one stream (stdout or stderr) is allowed to cost the model, in
#: characters. ``aida.coding.runner.MAX_CAPTURED_BYTES`` (256,000) bounds
#: *memory* while draining a process's pipes — it says nothing about what a
#: tool result should cost every subsequent round trip of the turn. Before
#: this cap, a reduction script's per-iteration fit-progress prints, or a
#: ``print(df)`` on a big frame, went to the model whole: up to ~128k tokens
#: from one `run_python_script` call, routinely blowing the context window
#: and 400ing the provider mid-turn with nothing recovering until the next
#: user message (`_trim_context` only runs at the *start* of a turn). Sized
#: like `aida.mcp.manager.MCP_RESULT_MAX_CHARS`, split per stream so a long
#: stdout can't crowd out a short-but-important stderr traceback.
RUN_OUTPUT_DISPLAY_MAX_CHARS = 8_000


def _capped_stream_text(
    text: str, *, label: str, scratch_dir: Path | None, run_id: str
) -> str:
    """One stream's text, bounded to ``RUN_OUTPUT_DISPLAY_MAX_CHARS``.

    Under the cap, returned untouched. Over it, ``_BoundedCapture`` (already
    used to bound a live process's output in memory) is reused here to keep
    head and tail of the *already-captured* text — the useful parts of a
    long log are almost always its beginning and its end — and, when a
    scratch folder is available, the untruncated text is written there with
    a pointer so the model can page through it with the existing
    ``read_file`` (the scratch folder is already in every session's
    ``global_allowed_folders``, see ``aida.core.session``)."""
    if len(text) <= RUN_OUTPUT_DISPLAY_MAX_CHARS:
        return text
    capture = _BoundedCapture(limit=RUN_OUTPUT_DISPLAY_MAX_CHARS)
    capture.feed(text.encode("utf-8"))
    capped = capture.text()
    if scratch_dir is None:
        return capped
    results_dir = scratch_dir / "tool-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{label}-{run_id}.txt"
    path.write_text(text, encoding="utf-8")
    return f"{capped}\n[full {label} saved to {path}; use read_file to see it]"


def _format_run_result(result: RunResult, *, scratch_dir: Path | None = None) -> str:
    lines = [f"exit code: {result.returncode}", f"duration: {result.duration_seconds:.2f}s"]
    if result.timed_out:
        lines.append("TIMED OUT — process was killed")
    run_id = new_artifact_id()
    if result.stdout:
        stdout = _capped_stream_text(
            result.stdout, label="stdout", scratch_dir=scratch_dir, run_id=run_id
        )
        lines.append(f"stdout:\n{stdout}")
    if result.stderr:
        stderr = _capped_stream_text(
            result.stderr, label="stderr", scratch_dir=scratch_dir, run_id=run_id
        )
        lines.append(f"stderr:\n{stderr}")
    return "\n".join(lines)


def _default_cwd(workspace: WorkspaceConfig) -> str | None:
    if workspace.target_folder:
        return workspace.target_folder
    if workspace.source_folders:
        return workspace.source_folders[0]
    return None


def _effective_timeout(arguments: dict[str, Any], workspace: WorkspaceConfig) -> float:
    """B5: the model may pass a per-call ``timeout`` (a long-running
    reduction script asking for more than the default 30s), but it's
    capped at ``workspace.script_timeout_seconds`` — a per-workspace
    ceiling the user configured, not something a tool call gets to raise
    unbounded. A missing/non-positive request just falls back to the
    workspace's own default."""
    requested = arguments.get("timeout")
    if requested is None:
        return workspace.script_timeout_seconds
    try:
        requested = float(requested)
    except (TypeError, ValueError):
        return workspace.script_timeout_seconds
    if requested <= 0:
        return workspace.script_timeout_seconds
    return min(requested, workspace.script_timeout_seconds)


def default_coding_tools(
    guard: SafetyGuard,
    *,
    workspace: WorkspaceConfig | None,
    scratch_dir: Path | None = None,
) -> dict[str, NativeTool]:
    """Empty for ``workspace=None`` (no folders configured, nothing to run
    in) or ``workspace.scripting_enabled=False`` — same "lazy, only if
    configured" philosophy as MCP servers and knowledge bases.

    ``scratch_dir`` (optional — omitted in most existing tests, which don't
    exercise output past ``RUN_OUTPUT_DISPLAY_MAX_CHARS``) is where an
    oversized stdout/stderr gets spilled; without it, an oversized stream is
    hard-truncated instead, same fallback ``aida.mcp.manager.McpManager``
    uses when it has no scratch dir either."""
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
        timeout = _effective_timeout(arguments, workspace)
        result = await _run_python_script(
            candidate,
            args,
            interpreter=workspace.python_interpreter,
            cwd=candidate.parent,
            timeout=timeout,
        )
        return ToolResult(
            content=_format_run_result(result, scratch_dir=scratch_dir),
            is_error=result.timed_out or result.returncode != 0,
        )

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
            argv = split_command(command)
        except ValueError as exc:
            return ToolResult(content=f"Could not parse command {command!r}: {exc}", is_error=True)
        timeout = _effective_timeout(arguments, workspace)
        result = await run_subprocess(argv, cwd=cwd, timeout=timeout)
        return ToolResult(
            content=_format_run_result(result, scratch_dir=scratch_dir),
            is_error=result.timed_out or result.returncode != 0,
        )

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
                        "timeout": {
                            "type": "number",
                            "description": (
                                "Seconds to allow before killing the script, for scripts that "
                                "legitimately run long. Capped by the workspace's configured "
                                "script_timeout_seconds; omit to use that default."
                            ),
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
                        "command": {
                            "type": "string",
                            "description": "The full command line to run.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Working directory (defaults to the workspace's target/source folder).",
                        },
                        "timeout": {
                            "type": "number",
                            "description": (
                                "Seconds to allow before killing the command, for commands that "
                                "legitimately run long. Capped by the workspace's configured "
                                "script_timeout_seconds; omit to use that default."
                            ),
                        },
                    },
                    "required": ["command"],
                },
            ),
            func=run_command,
        ),
    ]
    return {t.schema.name: t for t in tools}


__all__ = ["RUN_OUTPUT_DISPLAY_MAX_CHARS", "default_coding_tools"]
