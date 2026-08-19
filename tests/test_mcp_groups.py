from __future__ import annotations

import pytest

from aida.config.settings import McpConfig, McpServerConfig
from aida.mcp.groups import known_group_names, resolve_explicit, resolve_group


def _config() -> McpConfig:
    return McpConfig(
        servers={
            "pyirena": McpServerConfig(
                name="pyirena", command="pyirena-mcp", groups=["analysis", "full"]
            ),
            "bait": McpServerConfig(name="bait", command="bait-mcp", groups=["full"]),
            "notes": McpServerConfig(name="notes", command="notes-mcp", groups=[]),
        }
    )


def test_resolve_group_returns_matching_servers():
    servers = resolve_group(_config(), "analysis")
    assert {s.name for s in servers} == {"pyirena"}


def test_resolve_group_full_returns_multiple_servers():
    servers = resolve_group(_config(), "full")
    assert {s.name for s in servers} == {"pyirena", "bait"}


def test_resolve_group_none_sentinel_returns_empty():
    assert resolve_group(_config(), "none") == []


def test_resolve_group_empty_string_returns_empty():
    assert resolve_group(_config(), "") == []


def test_resolve_group_unknown_group_returns_empty():
    # An unknown group name is not an error — it's simply "no servers in
    # this group", same as any group with zero members.
    assert resolve_group(_config(), "does-not-exist") == []


def test_resolve_explicit_picks_named_servers():
    servers = resolve_explicit(_config(), ["pyirena", "notes"])
    assert {s.name for s in servers} == {"pyirena", "notes"}


def test_resolve_explicit_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown mcp server"):
        resolve_explicit(_config(), ["pyirena", "typo-name"])


def test_resolve_explicit_empty_list_returns_empty():
    assert resolve_explicit(_config(), []) == []


def test_known_group_names_sorted_and_deduplicated():
    assert known_group_names(_config()) == ["analysis", "full"]
