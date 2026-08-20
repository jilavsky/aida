from __future__ import annotations

import json
from pathlib import Path

import yaml

from aida.config.settings import (
    AppConfig,
    McpConfig,
    ProvidersConfig,
    WorkspacesConfig,
    load_app_config,
    load_mcp_config,
    load_providers_config,
    load_settings,
    load_workspaces_config,
    save_app_config,
    save_mcp_config,
    save_providers_config,
    save_workspaces_config,
)


def test_load_settings_first_run_writes_defaults(aida_home: Path):
    settings = load_settings()

    assert (aida_home / "config.yaml").exists()
    assert (aida_home / "providers.yaml").exists()
    assert (aida_home / "workspaces.yaml").exists()
    assert (aida_home / "mcp.json").exists()

    assert settings.app.config_version == 1
    assert settings.providers.profiles == {}
    assert settings.workspaces.workspaces == {}
    assert settings.mcp.servers == {}


def test_app_config_roundtrip(aida_home: Path):
    cfg = AppConfig(log_level="DEBUG", default_safety_mode="relaxed")
    path = save_app_config(cfg, aida_home)
    assert path.exists()

    loaded = load_app_config(aida_home)
    assert loaded.log_level == "DEBUG"
    assert loaded.default_safety_mode == "relaxed"
    assert loaded.config_version == 1


def test_app_config_allowed_folders_roundtrip(aida_home: Path):
    cfg = AppConfig(allowed_folders=["/tmp/shared-a", "/tmp/shared-b"])
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.allowed_folders == ["/tmp/shared-a", "/tmp/shared-b"]


def test_app_config_last_workspace_and_profile_roundtrip(aida_home: Path):
    cfg = AppConfig(last_workspace_name="use-pyirena", last_profile_name="local-lmstudio")
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.last_workspace_name == "use-pyirena"
    assert loaded.last_profile_name == "local-lmstudio"


def test_app_config_last_workspace_defaults_to_none(aida_home: Path):
    cfg = AppConfig()
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.last_workspace_name is None
    assert loaded.last_profile_name is None


def test_old_config_missing_fields_gets_defaults(aida_home: Path):
    """pyIrena rule: old configs must always load."""
    partial = {"config_version": 1}  # no log_level, no records_dir, ...
    path = aida_home / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(partial, fh)

    loaded = load_app_config(aida_home)
    assert loaded.log_level == "INFO"  # default, not a crash
    assert loaded.theme == "system"


def test_unknown_future_field_ignored(aida_home: Path):
    """A config written by a *future* AIDA version with an extra field must
    still load under this version rather than raising."""
    data = {"config_version": 1, "log_level": "WARNING", "some_future_field": 42}
    path = aida_home / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(data, fh)

    loaded = load_app_config(aida_home)
    assert loaded.log_level == "WARNING"


def test_providers_config_roundtrip(aida_home: Path):
    from aida.config.settings import ProviderProfile

    cfg = ProvidersConfig(
        profiles={
            "argo-claude": ProviderProfile(
                name="argo-claude",
                kind="anthropic",
                base_url="https://apps.inside.anl.gov/argoapi/",
                model="claude-sonnet",
                secret_ref="argo-claude",
            )
        }
    )
    save_providers_config(cfg, aida_home)
    loaded = load_providers_config(aida_home)
    assert "argo-claude" in loaded.profiles
    assert loaded.profiles["argo-claude"].kind == "anthropic"

    # No secret value anywhere in the file on disk.
    raw = (aida_home / "providers.yaml").read_text()
    assert "secret_ref" in raw
    assert "sk-" not in raw


def test_workspaces_config_roundtrip(aida_home: Path):
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="argo-claude",
                source_folders=["/data/USAXS"],
                target_folder="~/Documents/Aida/analysis",
                mcp_group="pyirena-analysis",
                safety="relaxed",
            )
        }
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home)
    ws = loaded.workspaces["use-pyirena"]
    assert ws.profile == "argo-claude"
    assert ws.safety == "relaxed"
    assert ws.source_folders == ["/data/USAXS"]


def test_mcp_config_roundtrip(aida_home: Path):
    from aida.config.settings import McpServerConfig

    cfg = McpConfig(
        servers={
            "pyirena-mcp": McpServerConfig(
                name="pyirena-mcp",
                command="/opt/conda/envs/pyirena/bin/pyirena-mcp",
                groups=["pyirena-analysis"],
                skills=["pyirena-usage"],
            )
        }
    )
    save_mcp_config(cfg, aida_home)
    loaded = load_mcp_config(aida_home)
    assert "pyirena-mcp" in loaded.servers
    assert loaded.servers["pyirena-mcp"].groups == ["pyirena-analysis"]


def test_existing_claude_desktop_mcp_json_loads_unmodified(aida_home: Path):
    """Phase 3 acceptance criterion: "An existing Claude Desktop mcp.json
    entry for pyirena works unmodified." Claude Desktop's config has no
    ``groups``/``skills``/``config_version`` keys (those are AIDA-only
    extras) and may carry extra keys AIDA doesn't know about (e.g.
    ``disabled``) — none of that should prevent loading, and AIDA's extra
    fields should simply default rather than erroring."""
    raw_claude_desktop_config = {
        "mcpServers": {
            "pyirena": {
                "command": "/opt/conda/envs/pyirena/bin/pyirena-mcp",
                "args": ["--stdio"],
                "env": {"PYIRENA_DATA_ROOT": "/data/USAXS"},
                "disabled": False,
            }
        }
    }
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "mcp.json").write_text(json.dumps(raw_claude_desktop_config), encoding="utf-8")

    loaded = load_mcp_config(aida_home)

    server = loaded.servers["pyirena"]
    assert server.command == "/opt/conda/envs/pyirena/bin/pyirena-mcp"
    assert server.args == ["--stdio"]
    assert server.env == {"PYIRENA_DATA_ROOT": "/data/USAXS"}
    # AIDA-only extras simply default — no error, no data loss.
    assert server.groups == []
    assert server.skills == []


def test_unknown_mcp_server_keys_survive_a_save_and_reload(aida_home: Path):
    """Regression (Phase 7): loading an unknown key like ``disabled`` never
    errored (see the test above), but nothing previously proved it
    *survived a save* — before ``McpServerConfig.extra`` existed, the very
    first GUI/CLI edit that re-saved mcp.json silently deleted every key
    AIDA didn't model. A real Claude-Desktop export carries exactly these:
    ``disabled``, ``autoApprove``, ``type``, ``cwd``."""

    raw = {
        "mcpServers": {
            "pyirena": {
                "command": "/opt/pyirena-mcp",
                "disabled": False,
                "autoApprove": ["plot_saxs"],
                "type": "stdio",
                "cwd": "/data",
            }
        }
    }
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "mcp.json").write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_mcp_config(aida_home)
    assert loaded.servers["pyirena"].extra == {
        "disabled": False,
        "autoApprove": ["plot_saxs"],
        "type": "stdio",
        "cwd": "/data",
    }

    # A round-trip through AIDA (e.g. an edit made from the GUI/CLI that
    # re-saves the whole file) must not lose any of those unknown keys.
    save_mcp_config(loaded, aida_home)
    reloaded = load_mcp_config(aida_home)
    assert reloaded.servers["pyirena"].extra == loaded.servers["pyirena"].extra
    assert reloaded.servers["pyirena"].command == "/opt/pyirena-mcp"


def test_disabled_and_confirm_tools_roundtrip(aida_home: Path):
    from aida.config.settings import McpServerConfig

    cfg = McpConfig(
        servers={
            "bait": McpServerConfig(
                name="bait",
                command="/opt/bait-mcp",
                disabled_tools=["dangerous_tool"],
                confirm_tools=["move_stage"],
            )
        }
    )
    save_mcp_config(cfg, aida_home)
    loaded = load_mcp_config(aida_home)
    assert loaded.servers["bait"].disabled_tools == ["dangerous_tool"]
    assert loaded.servers["bait"].confirm_tools == ["move_stage"]


def test_a_real_edit_to_a_known_field_wins_over_stale_extra_data(aida_home: Path):
    """If ``extra`` ever contained a value for a key AIDA *does* model
    (shouldn't normally happen since ``from_dict`` excludes known keys from
    ``extra`` in the first place, but ``to_dict`` must be robust either
    way), the dataclass's own field always wins on save."""
    from aida.config.settings import McpServerConfig

    server = McpServerConfig(name="x", command="/real/command", extra={"command": "/stale/command"})
    assert server.to_dict()["command"] == "/real/command"
