from __future__ import annotations

import pytest

from aida.config.settings import McpConfig, McpServerConfig
from aida.mcp.groups import (
    add_group,
    delete_group,
    known_group_names,
    rename_group,
    resolve_explicit,
    resolve_group,
)


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


# --- add_group (GUI "Add Group…" — B: MCP Groups widget had no way to
# create a brand-new group short of editing a server's own form) ------------


def test_add_group_creates_a_brand_new_group_from_selected_servers():
    cfg = _config()
    updated = add_group(cfg, "brand-new", ["pyirena", "notes"])
    assert updated == 2
    assert cfg.servers["pyirena"].groups == ["analysis", "full", "brand-new"]
    assert cfg.servers["notes"].groups == ["brand-new"]
    assert cfg.servers["bait"].groups == ["full"]  # not selected, untouched
    assert "brand-new" in known_group_names(cfg)


def test_add_group_to_existing_group_adds_the_new_members_only():
    cfg = _config()
    updated = add_group(cfg, "full", ["notes"])
    assert updated == 1
    assert cfg.servers["notes"].groups == ["full"]
    assert cfg.servers["pyirena"].groups == ["analysis", "full"]  # already had it, untouched


def test_add_group_is_a_noop_for_a_server_that_already_has_it():
    cfg = _config()
    assert add_group(cfg, "full", ["pyirena"]) == 0
    assert cfg.servers["pyirena"].groups == ["analysis", "full"]


def test_add_group_skips_unknown_server_names():
    cfg = _config()
    updated = add_group(cfg, "brand-new", ["pyirena", "does-not-exist"])
    assert updated == 1
    assert cfg.servers["pyirena"].groups == ["analysis", "full", "brand-new"]


def test_add_group_empty_server_list_is_a_noop():
    cfg = _config()
    assert add_group(cfg, "brand-new", []) == 0
    assert "brand-new" not in known_group_names(cfg)


# --- rename_group / delete_group (Phase 7 groups editor) --------------------


def test_rename_group_updates_every_referencing_server():
    cfg = _config()
    updated = rename_group(cfg, "full", "everything")
    assert updated == 2
    assert cfg.servers["pyirena"].groups == ["analysis", "everything"]
    assert cfg.servers["bait"].groups == ["everything"]
    assert cfg.servers["notes"].groups == []


def test_rename_group_deduplicates_if_new_name_already_present():
    cfg = _config()
    cfg.servers["pyirena"].groups = ["analysis", "full", "analysis"]  # already has target name too
    rename_group(cfg, "full", "analysis")
    assert cfg.servers["pyirena"].groups == ["analysis"]


def test_rename_group_is_a_noop_for_an_unreferenced_name():
    cfg = _config()
    assert rename_group(cfg, "does-not-exist", "new-name") == 0
    assert cfg.servers["pyirena"].groups == ["analysis", "full"]


def test_rename_group_same_name_is_a_noop():
    cfg = _config()
    assert rename_group(cfg, "full", "full") == 0


def test_delete_group_removes_from_every_referencing_server():
    cfg = _config()
    updated = delete_group(cfg, "full")
    assert updated == 2
    assert cfg.servers["pyirena"].groups == ["analysis"]
    assert cfg.servers["bait"].groups == []
    assert known_group_names(cfg) == ["analysis"]


def test_delete_group_is_a_noop_for_an_unreferenced_name():
    cfg = _config()
    assert delete_group(cfg, "does-not-exist") == 0
