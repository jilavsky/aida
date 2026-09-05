"""Runs a stored named workflow (``aida.config.settings.WorkflowConfig``) as
a sequence of turns in one shared ``ChatSession`` — the layer both
``aida workflow run`` and the scheduler drive
(planning/phase10_scheduling_design.md §4).

Design choices worth stating up front, since they aren't obvious from the
config shape alone:

- **One session for every step**, not one per step — so a later step's
  prompt ("write a summary of what you just did") can actually refer to
  earlier steps' work, and so MCP servers start once per run, not once per
  step.
- **A step fails only for an ``AgentError`` or an unmet ``expect_files``
  assertion** — not merely because ``ToolCallFinished.is_error`` appeared
  mid-turn. ``AgentLoop`` already lets the model see a tool error and try
  something else within the same turn (the same behavior an interactive
  chat gets); treating every recoverable tool hiccup as a workflow-ending
  failure would be far stricter than the interactive experience this reuses
  the engine from. ``stop_reason == "length"`` is recorded but likewise not
  a hard failure, matching ``aida.cli.chat.print_event``'s own treatment of
  it as a notice, not an error.
- **The reproducibility manifest is written unconditionally** (success or
  failure) next to the workspace's target folder — "what exactly produced
  this" matters most for a run a user is looking at because something went
  wrong. No manifest is written if the workspace has no ``target_folder``
  (nowhere sensible to put it).
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aida
from aida.config.settings import Settings, WorkflowConfig, WorkflowStep
from aida.core.confirmation import ConfirmCallback
from aida.core.events import (
    AgentError,
    AgentEvent,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    ToolCallFinished,
)
from aida.core.session import (
    UnknownMcpServerError,
    UnknownProfileError,
    UnknownWorkspaceError,
    start_session,
)
from aida.workspace.workspaces import get_workspace

OnEvent = Callable[[int, AgentEvent], None]


class WorkflowConfigError(Exception):
    """Raised before any session work happens: an unusable workflow
    (missing workspace, a step referencing a ``{placeholder}`` with no
    value) or a startup failure (unknown workspace/profile/MCP server).
    The CLI maps this to exit code 2 — "config/validation error," distinct
    from a step actually failing once the run is underway (exit code 1)."""


@dataclass
class StepResult:
    """What happened running one step, kept detailed enough to serialize
    straight into the reproducibility manifest."""

    index: int
    prompt: str
    ok: bool
    stop_reason: str | None = None
    error: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    missing_expect_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt": self.prompt,
            "ok": self.ok,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "tool_calls": self.tool_calls,
            "artifacts": self.artifacts,
            "missing_expect_files": self.missing_expect_files,
        }


@dataclass
class WorkflowResult:
    workflow_name: str
    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    conversation_id: str | None = None
    error: str | None = None
    manifest_path: str | None = None


def render_step_prompt(step: WorkflowStep, resolved_vars: dict[str, str]) -> str:
    """Resolve ``{placeholder}`` names in a step's prompt. Raises
    ``WorkflowConfigError`` naming the missing variable — used both here and
    by ``aida workflow validate`` so a typo'd placeholder is caught before
    any session/provider work happens, not mid-run."""
    try:
        return step.prompt.format(**resolved_vars)
    except KeyError as exc:
        missing = exc.args[0]
        raise WorkflowConfigError(
            f"step references {{{missing}}}, which has no value "
            f"(pass --var {missing}=... or add it to the workflow's own vars:)"
        ) from exc


def _check_expect_files(patterns: list[str], target_folder: str | None) -> list[str]:
    """Returns the patterns that matched nothing — an empty list means the
    assertion is satisfied. Every pattern in ``patterns`` must match at
    least one file under ``target_folder``; a workflow with no
    ``target_folder`` configured can never satisfy a non-empty
    ``expect_files`` list, since there is nowhere to check."""
    if not patterns:
        return []
    if not target_folder:
        return list(patterns)
    folder = Path(target_folder).expanduser()
    if not folder.is_dir():
        return list(patterns)
    existing = [p.name for p in folder.iterdir()]
    return [pattern for pattern in patterns if not fnmatch.filter(existing, pattern)]


def _manifest_dict(
    workflow: WorkflowConfig,
    *,
    resolved_vars: dict[str, str],
    origin: str,
    started_at: str,
    finished_at: str,
    result: WorkflowResult,
) -> dict[str, Any]:
    return {
        "aida_version": aida.__version__,
        "workflow": workflow.name,
        "description": workflow.description,
        "workspace": workflow.workspace,
        "profile": workflow.profile,
        "mcp_group": workflow.mcp_group,
        "origin": origin,
        "vars": resolved_vars,
        "started_at": started_at,
        "finished_at": finished_at,
        "conversation_id": result.conversation_id,
        "ok": result.ok,
        "error": result.error,
        "steps": [step.to_dict() for step in result.steps],
    }


def _write_manifest(
    workflow: WorkflowConfig,
    target_folder: str | None,
    manifest: dict[str, Any],
    *,
    finished_at: str,
) -> str | None:
    if not target_folder:
        return None
    folder = Path(target_folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    timestamp = finished_at.replace(":", "").replace("-", "").split(".")[0]
    path = folder / f"run-{workflow.name}-{timestamp}.aida.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return str(path)


async def run_workflow(
    settings: Settings,
    workflow: WorkflowConfig,
    *,
    var_overrides: dict[str, str] | None = None,
    confirm_callback: ConfirmCallback,
    origin: str,
    on_event: OnEvent | None = None,
) -> WorkflowResult:
    """Runs every step of ``workflow`` in one new ``ChatSession``, stopping
    at the first step that fails (see this module's docstring for exactly
    what "fails" means). Always closes the session/MCP manager it opened,
    and always writes the reproducibility manifest when there's a target
    folder to put it in, regardless of whether the run succeeded."""
    if not workflow.workspace:
        raise WorkflowConfigError(f"workflow {workflow.name!r} has no workspace configured")

    resolved_vars = {**workflow.vars, **(var_overrides or {})}
    # Render every step up front: a missing {placeholder} is a config
    # error, not a mid-run surprise on step 3 after steps 1-2 already ran.
    rendered_prompts = [render_step_prompt(step, resolved_vars) for step in workflow.steps]

    try:
        session, mcp_manager = await start_session(
            settings,
            profile_name=workflow.profile,
            workspace_name=workflow.workspace,
            mcp_group=workflow.mcp_group or "",
            confirm_callback=confirm_callback,
            origin=origin,
        )
    except (UnknownWorkspaceError, UnknownProfileError, UnknownMcpServerError) as exc:
        raise WorkflowConfigError(str(exc)) from exc

    started_at = datetime.now(UTC).isoformat()
    workspace_cfg = get_workspace(settings, workflow.workspace)
    target_folder = workspace_cfg.target_folder if workspace_cfg else None
    conversation_id = session.recorder.conversation_id if session.recorder else None

    result = WorkflowResult(workflow_name=workflow.name, ok=True, conversation_id=conversation_id)
    try:
        for index, (step, prompt) in enumerate(zip(workflow.steps, rendered_prompts, strict=True)):
            step_result = StepResult(index=index, prompt=prompt, ok=True)
            async for event in session.send(prompt):
                if on_event is not None:
                    on_event(index, event)
                if isinstance(event, ToolCallFinished):
                    step_result.tool_calls.append(
                        {"tool_name": event.tool_name, "is_error": event.is_error}
                    )
                elif isinstance(event, (ImageArtifactCreated, FileArtifactCreated)):
                    step_result.artifacts.append({"path": event.path, "mime_type": event.mime_type})
                elif isinstance(event, MessageFinished):
                    step_result.stop_reason = event.stop_reason
                elif isinstance(event, AgentError):
                    step_result.ok = False
                    step_result.error = f"{event.layer}: {event.message}"

            if step_result.ok and step.expect_files:
                missing = _check_expect_files(step.expect_files, target_folder)
                if missing:
                    step_result.ok = False
                    step_result.missing_expect_files = missing
                    step_result.error = f"expected file(s) not found: {', '.join(missing)}"

            result.steps.append(step_result)
            if not step_result.ok:
                result.ok = False
                result.error = f"step {index}: {step_result.error}"
                break
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()

    finished_at = datetime.now(UTC).isoformat()
    manifest = _manifest_dict(
        workflow,
        resolved_vars=resolved_vars,
        origin=origin,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
    )
    result.manifest_path = _write_manifest(
        workflow, target_folder, manifest, finished_at=finished_at
    )
    return result


__all__ = [
    "OnEvent",
    "StepResult",
    "WorkflowConfigError",
    "WorkflowResult",
    "render_step_prompt",
    "run_workflow",
]
