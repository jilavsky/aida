"""Workspace files, search, and the allowed-folders safety model. Never
imports Qt.

Phase 4: ``aida.workspace.workspaces`` — named-workspace validation and
resolution (profile + MCP group + skills + system prompt). The
allowed-folders safety *enforcement* arrives in Phase 6.
"""

from aida.workspace.workspaces import (
    WorkspaceEnvironment,
    WorkspaceValidation,
    delete_workspace,
    get_workspace,
    list_workspace_names,
    resolve_workspace_environment,
    save_workspace,
    validate_workspace,
)

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
