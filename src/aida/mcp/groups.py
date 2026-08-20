"""Resolve which MCP servers should be active for a session.

Two selection modes, both from planning/phase03_mcp.md:

- **Group**: a named server set, resolved from each server's own
  ``groups: [...]`` config key (``aida.config.settings.McpServerConfig``).
  Rationale (PLAN.md): pyIrena's full tool list overloads small local
  models, so a workspace enables only the group of servers/tools it needs.
- **Explicit**: ``--mcp server1,server2`` picks servers by name directly,
  bypassing groups entirely.

Either way, only the *resolved* servers are ever launched — lazy start,
no resource waste for configured-but-unused servers.
"""

from __future__ import annotations

from aida.config.settings import McpConfig, McpServerConfig

NO_GROUP = "none"


def resolve_group(mcp_config: McpConfig, group: str) -> list[McpServerConfig]:
    """Servers whose ``groups`` list contains ``group``. The sentinel
    ``"none"`` (``WorkspaceConfig.mcp_group``'s default) always resolves to
    no servers — that is the "MCP off" state, not a group named "none"."""
    if not group or group == NO_GROUP:
        return []
    return [server for server in mcp_config.servers.values() if group in server.groups]


def resolve_explicit(mcp_config: McpConfig, names: list[str]) -> list[McpServerConfig]:
    """Servers picked by exact name. Raises ``ValueError`` naming any
    unknown server rather than silently offering fewer tools than the user
    asked for — diagnostics-as-a-feature applies to config typos too."""
    servers: list[McpServerConfig] = []
    unknown: list[str] = []
    for name in names:
        config = mcp_config.servers.get(name)
        if config is None:
            unknown.append(name)
        else:
            servers.append(config)
    if unknown:
        raise ValueError(f"unknown mcp server name(s): {', '.join(unknown)}")
    return servers


def known_group_names(mcp_config: McpConfig) -> list[str]:
    """Every group name referenced by at least one configured server, in a
    stable (sorted) order — useful for CLI help text / error messages."""
    names: set[str] = set()
    for server in mcp_config.servers.values():
        names.update(server.groups)
    return sorted(names)


def rename_group(mcp_config: McpConfig, old: str, new: str) -> int:
    """Renames a group in-place across every server's ``groups`` list
    (there is no separate group registry to rename — see this module's
    docstring: a group is purely derived from who references it). Returns
    the number of servers updated. De-duplicates: a server already
    referencing both ``old`` and ``new`` ends up with a single ``new``
    entry rather than two. A no-op (returns 0) if ``old`` isn't referenced
    or if ``old == new``.
    """
    if not old or not new or old == new:
        return 0
    updated = 0
    for server in mcp_config.servers.values():
        if old not in server.groups:
            continue
        server.groups = list(dict.fromkeys(new if g == old else g for g in server.groups))
        updated += 1
    return updated


def delete_group(mcp_config: McpConfig, name: str) -> int:
    """Removes ``name`` from every server's ``groups`` list. Returns the
    number of servers updated. A workspace whose ``mcp_group`` still names
    a now-deleted group simply resolves to zero servers (the existing
    ``resolve_group`` behavior for any unreferenced group name) rather than
    erroring — deleting a group doesn't touch ``workspaces.yaml``.
    """
    updated = 0
    for server in mcp_config.servers.values():
        if name not in server.groups:
            continue
        server.groups = [g for g in server.groups if g != name]
        updated += 1
    return updated


__all__ = [
    "NO_GROUP",
    "delete_group",
    "known_group_names",
    "rename_group",
    "resolve_explicit",
    "resolve_group",
]
