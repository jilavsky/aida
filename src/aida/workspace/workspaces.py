"""Named workspaces: validate a ``WorkspaceConfig`` and resolve it into the
concrete environment a chat session needs (profile, MCP servers, skills,
system prompt).

The allowed-folders *safety* model (enforcing what a workspace's
source/target folders may be read/written) is Phase 6 — this module only
validates that a workspace's config is internally sane and that its folders
are currently reachable, warning rather than crashing when they aren't
(PLAN.md: network mounts may be slow/absent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aida.config.paths import config_dir, skills_dir
from aida.config.settings import McpServerConfig, Settings, WorkspaceConfig, save_workspaces_config
from aida.core.context import skill_exists
from aida.mcp.groups import known_group_names, resolve_group
from aida.workspace.safety import SAFETY_MODES


@dataclass
class WorkspaceValidation:
    """Mirrors ``aida.providers.profiles.ProfileValidation`` in shape:
    ``ok`` is only ``False`` for something that would actually break the
    workspace (an undefined profile); everything else that's merely
    *questionable* (an empty MCP group, a missing skill, an unreachable
    folder) is a warning, not a failure — "warn, don't crash"."""

    name: str
    ok: bool
    detail: str
    warnings: list[str] = field(default_factory=list)


_PROMPT_FILE_EXTENSIONS = (".md", ".txt")


def _looks_like_system_prompt_file_reference(value: str) -> bool:
    """Heuristic for "this ``system_prompt`` value was probably meant to be
    a file reference, not literal prompt text": PLAN.md's own illustrative
    ``workspaces.yaml`` example uses ``system_prompt: prompts/pyirena.md`` —
    a relative path with a path separator and a text-file extension — and a
    hand-edited config is likely to follow that example literally. Plain
    literal prompt text ("You are a USAXS expert.") won't match this
    shape, so the false-positive rate should be low."""
    return value.endswith(_PROMPT_FILE_EXTENSIONS) and ("/" in value or "\\" in value)


def _system_prompt_file_path(value: str) -> Path:
    """Where a ``system_prompt`` file reference resolves to: absolute/``~``
    paths are used as-is, anything else is resolved relative to
    ``~/.aida/`` (``config_dir()``) — the same directory ``workspaces.yaml``
    itself lives in, matching PLAN.md's example layout."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir() / candidate
    return candidate


def resolve_system_prompt(system_prompt: str | None) -> str | None:
    """A workspace's ``system_prompt`` can be either literal text (what
    ``aida workspace new --system-prompt`` documents: "Extra system prompt
    text for this workspace") or a reference to a file — PLAN.md's own
    illustrative ``workspaces.yaml`` uses ``system_prompt: prompts/pyirena.md``,
    a file reference, and a hand-edited config is likely to follow that
    example. Previously this was always treated as literal text, silently:
    a workspace with ``system_prompt: prompts/pyirena.md`` and no such file
    got exactly that string, verbatim, as its system prompt — not an error,
    just quietly wrong. If the value resolves to an existing file (see
    ``_system_prompt_file_path``), its contents are used as the prompt;
    otherwise the value itself is used verbatim, unchanged from before."""
    if not system_prompt:
        return system_prompt
    path = _system_prompt_file_path(system_prompt)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return system_prompt


def validate_workspace(settings: Settings, workspace: WorkspaceConfig) -> WorkspaceValidation:
    warnings: list[str] = []

    if workspace.profile is not None and workspace.profile not in settings.providers.profiles:
        return WorkspaceValidation(
            name=workspace.name,
            ok=False,
            detail=f"unknown profile {workspace.profile!r} — see `aida config` / providers.yaml",
        )

    if workspace.mcp_group and workspace.mcp_group != "none":
        known = known_group_names(settings.mcp)
        if workspace.mcp_group not in known:
            warnings.append(
                f"mcp_group {workspace.mcp_group!r} isn't referenced by any server's "
                f"'groups' in mcp.json — this workspace will have no MCP tools"
            )

    missing_skills = [s for s in workspace.skills if not skill_exists(skills_dir(), s)]
    if missing_skills:
        # Actionable, not just "not found" (bug report: "may be related to
        # the fact the skill folder does not exist?" — the *directory*
        # always exists, ``skills_dir()`` self-creates it; what's actually
        # missing is the specific skill file, so spell out exactly where
        # each one is expected so the user can drop it in and move on).
        expected = ", ".join(
            f"{s} (expected {skills_dir() / f'{s}.md'} or {skills_dir() / s / 'SKILL.md'})"
            for s in missing_skills
        )
        warnings.append(f"skill file(s) not found (will be skipped): {expected}")

    for folder in workspace.source_folders:
        if not Path(folder).expanduser().exists():
            warnings.append(f"source folder not currently reachable: {folder}")

    if workspace.target_folder and not Path(workspace.target_folder).expanduser().exists():
        warnings.append(
            f"target folder doesn't exist yet (created on first write): {workspace.target_folder}"
        )

    if workspace.safety not in SAFETY_MODES:
        # Says what will actually happen, not just that the value is odd.
        # This used to read as a cosmetic complaint while the unknown value
        # was passed straight to SafetyGuard, where it matched neither the
        # "relaxed" nor the "confirm" branch and so skipped confirmation
        # altogether — the warning described a typo and the runtime quietly
        # applied the weakest setting. SafetyGuard now fails closed, so the
        # honest wording is "treated as 'confirm'".
        warnings.append(
            f"unknown safety mode {workspace.safety!r} (expected "
            f"{' or '.join(repr(m) for m in SAFETY_MODES)}) — treated as 'confirm'"
        )

    if workspace.system_prompt and _looks_like_system_prompt_file_reference(
        workspace.system_prompt
    ):
        prompt_path = _system_prompt_file_path(workspace.system_prompt)
        if not prompt_path.is_file():
            warnings.append(
                f"system_prompt {workspace.system_prompt!r} looks like a file reference but no "
                f"such file was found (expected {prompt_path}) — its literal text will be used as "
                f"the prompt instead, which is likely not what you want"
            )

    detail = "ok" if not warnings else f"ok, {len(warnings)} warning(s)"
    return WorkspaceValidation(name=workspace.name, ok=True, detail=detail, warnings=warnings)


@dataclass
class WorkspaceEnvironment:
    """What a workspace resolves to, ready to hand to
    ``aida.cli.chat``'s session-startup code: which profile, which MCP
    servers to launch, which skills to load, and what system prompt to use."""

    workspace_name: str
    profile_name: str | None
    mcp_servers: list[McpServerConfig]
    skill_names: list[str]
    system_prompt: str | None
    sidecar_folder_name: str
    safety: str


def resolve_workspace_environment(
    settings: Settings, workspace: WorkspaceConfig
) -> WorkspaceEnvironment:
    """Turn a ``WorkspaceConfig`` into the concrete pieces a chat session
    needs. Does not validate — call ``validate_workspace`` first if you want
    to warn the user about problems before acting on them."""
    mcp_servers = resolve_group(settings.mcp, workspace.mcp_group)
    return WorkspaceEnvironment(
        workspace_name=workspace.name,
        profile_name=workspace.profile,
        mcp_servers=mcp_servers,
        skill_names=list(workspace.skills),
        system_prompt=resolve_system_prompt(workspace.system_prompt),
        sidecar_folder_name=workspace.sidecar_folder_name,
        safety=workspace.safety,
    )


def get_workspace(settings: Settings, name: str) -> WorkspaceConfig | None:
    return settings.workspaces.workspaces.get(name)


def list_workspace_names(settings: Settings) -> list[str]:
    return sorted(settings.workspaces.workspaces)


def save_workspace(
    settings: Settings, workspace: WorkspaceConfig, *, base_dir: Path | None = None
) -> None:
    """Create or overwrite (``aida workspace new``/``edit`` are the same
    operation here — a name that already exists is simply replaced)."""
    settings.workspaces.workspaces[workspace.name] = workspace
    save_workspaces_config(settings.workspaces, base_dir)


def delete_workspace(settings: Settings, name: str, *, base_dir: Path | None = None) -> bool:
    """Returns ``False`` (no-op) if ``name`` wasn't a configured workspace."""
    if name not in settings.workspaces.workspaces:
        return False
    del settings.workspaces.workspaces[name]
    save_workspaces_config(settings.workspaces, base_dir)
    return True


__all__ = [
    "WorkspaceEnvironment",
    "WorkspaceValidation",
    "delete_workspace",
    "get_workspace",
    "list_workspace_names",
    "resolve_workspace_environment",
    "save_workspace",
    "validate_workspace",
]
