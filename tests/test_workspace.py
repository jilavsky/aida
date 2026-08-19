from __future__ import annotations

from pathlib import Path

from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    ProvidersConfig,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.workspace.workspaces import (
    delete_workspace,
    get_workspace,
    list_workspace_names,
    resolve_workspace_environment,
    save_workspace,
    validate_workspace,
)


def _settings(**overrides) -> Settings:
    settings = load_settings()
    settings.providers = ProvidersConfig(
        profiles={"argo-claude": ProviderProfile(name="argo-claude", kind="anthropic", model="claude")}
    )
    settings.mcp = McpConfig(
        servers={
            "pyirena": McpServerConfig(name="pyirena", command="pyirena-mcp", groups=["pyirena-analysis"]),
        }
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _workspace(**overrides) -> WorkspaceConfig:
    defaults = dict(
        name="use-pyirena",
        profile="argo-claude",
        mcp_group="pyirena-analysis",
        skills=[],
        source_folders=[],
        target_folder=None,
        safety="confirm",
    )
    defaults.update(overrides)
    return WorkspaceConfig(**defaults)


def test_validate_workspace_ok_with_no_warnings(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace())
    assert result.ok is True
    assert result.warnings == []


def test_validate_workspace_unknown_profile_is_fatal(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace(profile="does-not-exist"))
    assert result.ok is False
    assert "does-not-exist" in result.detail


def test_validate_workspace_none_profile_is_allowed(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace(profile=None))
    assert result.ok is True


def test_validate_workspace_unknown_mcp_group_warns_not_fatal(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace(mcp_group="not-a-real-group"))
    assert result.ok is True
    assert any("not-a-real-group" in w for w in result.warnings)


def test_validate_workspace_none_mcp_group_no_warning(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace(mcp_group="none"))
    assert result.warnings == []


def test_validate_workspace_missing_skill_warns(aida_home: Path, records_home: Path, monkeypatch):
    from aida.config import paths as paths_module

    skills_dir = aida_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths_module, "skills_dir", lambda: skills_dir)
    monkeypatch.setattr("aida.workspace.workspaces.skills_dir", lambda: skills_dir)

    settings = _settings()
    result = validate_workspace(settings, _workspace(skills=["does-not-exist"]))
    assert result.ok is True
    assert any("does-not-exist" in w for w in result.warnings)


def test_validate_workspace_unreachable_source_folder_warns(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace(source_folders=["/no/such/folder/anywhere"]))
    assert result.ok is True
    assert any("/no/such/folder/anywhere" in w for w in result.warnings)


def test_validate_workspace_unknown_safety_mode_warns(aida_home: Path, records_home: Path):
    settings = _settings()
    result = validate_workspace(settings, _workspace(safety="yolo"))
    assert any("yolo" in w for w in result.warnings)


def test_resolve_workspace_environment_pulls_mcp_servers_from_group(aida_home: Path, records_home: Path):
    settings = _settings()
    env = resolve_workspace_environment(settings, _workspace())
    assert [s.name for s in env.mcp_servers] == ["pyirena"]
    assert env.profile_name == "argo-claude"


def test_resolve_workspace_environment_none_group_gives_no_servers(aida_home: Path, records_home: Path):
    settings = _settings()
    env = resolve_workspace_environment(settings, _workspace(mcp_group="none"))
    assert env.mcp_servers == []


def test_resolve_workspace_environment_carries_skills_and_prompt(aida_home: Path, records_home: Path):
    settings = _settings()
    ws = _workspace(skills=["saxs-basics"], system_prompt="You are a USAXS expert.")
    env = resolve_workspace_environment(settings, ws)
    assert env.skill_names == ["saxs-basics"]
    assert env.system_prompt == "You are a USAXS expert."


def test_save_and_get_and_list_and_delete_workspace(aida_home: Path, records_home: Path):
    settings = _settings(workspaces=WorkspacesConfig())
    ws = _workspace()

    save_workspace(settings, ws, base_dir=aida_home)
    assert get_workspace(settings, "use-pyirena") is not None
    assert list_workspace_names(settings) == ["use-pyirena"]

    deleted = delete_workspace(settings, "use-pyirena", base_dir=aida_home)
    assert deleted is True
    assert get_workspace(settings, "use-pyirena") is None
    assert list_workspace_names(settings) == []


def test_delete_unknown_workspace_returns_false(aida_home: Path, records_home: Path):
    settings = _settings(workspaces=WorkspacesConfig())
    assert delete_workspace(settings, "nope", base_dir=aida_home) is False


def test_save_workspace_persists_to_disk(aida_home: Path, records_home: Path):
    from aida.config.settings import load_workspaces_config

    settings = _settings(workspaces=WorkspacesConfig())
    save_workspace(settings, _workspace(), base_dir=aida_home)

    reloaded = load_workspaces_config(aida_home)
    assert "use-pyirena" in reloaded.workspaces
    assert reloaded.workspaces["use-pyirena"].profile == "argo-claude"
