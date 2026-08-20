"""Import an external ``mcp.json``-shaped config and merge it into AIDA's
own ``McpConfig``, without clobbering existing servers by accident
(planning/phase07_mcp_management.md: "Import from existing standard
mcp.json (paste or file-pick a Claude Desktop-style config; merge without
clobbering)").

Pure functions, no file I/O and no AIDA-config persistence here — a caller
(CLI or GUI) reads the incoming JSON, calls ``merge_mcp_config``, and is
responsible for actually calling ``aida.config.settings.save_mcp_config``
on the result. Kept separate from ``aida.config.settings`` because merging
is a policy decision (what happens to a name collision) rather than a
plain load/save shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aida.config.settings import McpConfig, McpServerConfig


@dataclass
class MergeResult:
    """What happened when merging an imported config into an existing one."""

    config: McpConfig
    added: list[str]
    #: Names present in both the existing config and the import that were
    #: left untouched because they weren't in ``overwrite``.
    skipped: list[str]
    #: Names present in both that *were* replaced (were in ``overwrite``).
    overwritten: list[str]


def parse_incoming_mcp_json(data: dict[str, Any]) -> dict[str, McpServerConfig]:
    """Parse a raw ``mcp.json``-shaped dict (standard ``{"mcpServers": {...}}``
    — Claude Desktop's own shape, and the same one ``McpConfig.from_dict``
    reads) into ``{name: McpServerConfig}``, reusing
    ``McpServerConfig.from_dict`` so an imported server's unknown keys are
    preserved in ``extra`` exactly like a normal load (see that dataclass's
    docstring)."""
    raw_servers = data.get("mcpServers") or {}
    return {name: McpServerConfig.from_dict(name, sdata) for name, sdata in raw_servers.items()}


def merge_mcp_config(
    existing: McpConfig, incoming: dict[str, Any], *, overwrite: set[str] | None = None
) -> MergeResult:
    """Merge ``incoming`` (a raw dict, e.g. loaded from an imported file)
    into ``existing``, returning a *new* ``McpConfig`` — ``existing`` is not
    mutated, so a caller can inspect the result before deciding to save it.

    A server name not already in ``existing`` is always added. A name that
    collides is left alone unless it's in ``overwrite`` (default: no
    overwrites at all — the safe "merge without clobbering" default), in
    which case the imported version fully replaces the existing one.
    Malformed input (not a dict, no ``mcpServers`` key, a non-dict server
    entry) never raises — it's treated as "nothing to import" so a bad
    paste/file doesn't crash the caller; ``added``/``skipped``/``overwritten``
    all come back empty in that case.
    """
    overwrite = overwrite or set()
    try:
        incoming_servers = parse_incoming_mcp_json(incoming) if isinstance(incoming, dict) else {}
    except (AttributeError, TypeError):
        incoming_servers = {}

    merged_servers = dict(existing.servers)
    added: list[str] = []
    skipped: list[str] = []
    overwritten: list[str] = []

    for name, server in incoming_servers.items():
        if name not in existing.servers:
            merged_servers[name] = server
            added.append(name)
        elif name in overwrite:
            merged_servers[name] = server
            overwritten.append(name)
        else:
            skipped.append(name)

    merged = McpConfig(config_version=existing.config_version, servers=merged_servers)
    return MergeResult(config=merged, added=added, skipped=skipped, overwritten=overwritten)


__all__ = ["MergeResult", "merge_mcp_config", "parse_incoming_mcp_json"]
