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

from aida.config.paths import skills_dir
from aida.config.settings import McpServerConfig, Settings, WorkspaceConfig, save_workspaces_config
from aida.core.context import skill_exists
from aida.mcp.groups import known_group_names, resolve_group


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
        warnings.append(f"skill file(s) not found (will be skipped): {', '.join(missing_skills)}")

    for folder in workspace.source_folders:
        if not Path(folder).expanduser().exists():
            warnings.append(f"source folder not currently reachable: {folder}")

    if workspace.target_folder and not Path(workspace.target_folder).expanduser().exists():
        warnings.append(f"target folder doesn't exist yet (created on first write): {workspace.target_folder}")

    if workspace.safety not in ("relaxed", "confirm"):
        warnings.append(f"unknown safety mode {workspace.safety!r} (expected 'relaxed' or 'confirm')")

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


def resolve_workspace_environment(settings: Settings, workspace: WorkspaceConfig) -> WorkspaceEnvironment:
    """Turn a ``WorkspaceConfig`` into the concrete pieces a chat session
    needs. Does not validate — call ``validate_workspace`` first if you want
    to warn the user about problems before acting on them."""
    mcp_servers = resolve_group(settings.mcp, workspace.mcp_group)
    return WorkspaceEnvironment(
        workspace_name=workspace.name,
        profile_name=workspace.profile,
        mcp_servers=mcp_servers,
        skill_names=list(workspace.skills),
        system_prompt=workspace.system_prompt,
        sidecar_folder_name=workspace.sidecar_folder_name,
        safety=workspace.safety,
    )


def get_workspace(settings: Settings, name: str) -> WorkspaceConfig | None:
    return settings.workspaces.workspaces.get(name)


def list_workspace_names(settings: Settings) -> list[str]:
    return sorted(settings.workspaces.workspaces)


def save_workspace(settings: Settings, workspace: WorkspaceConfig, *, base_dir: Path | None = None) -> None:
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
