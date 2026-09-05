"""Tests for aida.mcp.config_io.merge_mcp_config — the "import an existing
Claude Desktop mcp.json; merge without clobbering" logic (Phase 7)."""

from __future__ import annotations

from aida.config.settings import McpConfig, McpServerConfig
from aida.mcp.config_io import merge_mcp_config


def _config(**servers: McpServerConfig) -> McpConfig:
    return McpConfig(servers=servers)


def test_new_server_is_added():
    existing = _config()
    incoming = {"mcpServers": {"pyirena": {"command": "/opt/pyirena-mcp", "args": ["--stdio"]}}}

    result = merge_mcp_config(existing, incoming)

    assert result.added == ["pyirena"]
    assert result.skipped == []
    assert result.config.servers["pyirena"].command == "/opt/pyirena-mcp"


def test_conflicting_name_is_skipped_by_default():
    existing = _config(pyirena=McpServerConfig(name="pyirena", command="/existing/path"))
    incoming = {"mcpServers": {"pyirena": {"command": "/imported/path"}}}

    result = merge_mcp_config(existing, incoming)

    assert result.skipped == ["pyirena"]
    assert result.added == []
    assert result.config.servers["pyirena"].command == "/existing/path", "not clobbered"


def test_explicit_overwrite_replaces_the_existing_server():
    existing = _config(pyirena=McpServerConfig(name="pyirena", command="/existing/path"))
    incoming = {"mcpServers": {"pyirena": {"command": "/imported/path"}}}

    result = merge_mcp_config(existing, incoming, overwrite={"pyirena"})

    assert result.overwritten == ["pyirena"]
    assert result.skipped == []
    assert result.config.servers["pyirena"].command == "/imported/path"


def test_existing_config_object_is_not_mutated():
    existing = _config(pyirena=McpServerConfig(name="pyirena", command="/existing/path"))
    incoming = {
        "mcpServers": {"pyirena": {"command": "/imported/path"}, "bait": {"command": "/bait"}}
    }

    merge_mcp_config(existing, incoming, overwrite={"pyirena"})

    assert existing.servers["pyirena"].command == "/existing/path"
    assert "bait" not in existing.servers


def test_a_mix_of_new_and_conflicting_names_in_one_import():
    existing = _config(pyirena=McpServerConfig(name="pyirena", command="/existing"))
    incoming = {
        "mcpServers": {
            "pyirena": {"command": "/imported"},  # conflict, not overwritten
            "bait_mcp": {"command": "/bait"},  # new
        }
    }

    result = merge_mcp_config(existing, incoming)

    assert result.added == ["bait_mcp"]
    assert result.skipped == ["pyirena"]
    assert set(result.config.servers) == {"pyirena", "bait_mcp"}


def test_unknown_keys_on_an_imported_server_survive_via_extra():
    """A real Claude-Desktop export carries keys AIDA doesn't model —
    importing must not silently drop them (aida.config.settings.
    McpServerConfig.extra is what carries them through)."""
    existing = _config()
    incoming = {"mcpServers": {"pyirena": {"command": "/x", "disabled": False, "type": "stdio"}}}

    result = merge_mcp_config(existing, incoming)

    assert result.config.servers["pyirena"].extra == {"disabled": False, "type": "stdio"}


def test_malformed_input_imports_nothing_without_raising():
    existing = _config(pyirena=McpServerConfig(name="pyirena", command="/existing"))

    result = merge_mcp_config(existing, {"not_mcp_servers_at_all": True})
    assert result.added == result.skipped == result.overwritten == []
    assert result.config.servers == existing.servers

    result2 = merge_mcp_config(existing, {})
    assert result2.added == []

    result3 = merge_mcp_config(existing, "this is not even a dict")  # type: ignore[arg-type]
    assert result3.added == []
