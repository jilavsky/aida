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


__all__ = ["NO_GROUP", "known_group_names", "resolve_explicit", "resolve_group"]
