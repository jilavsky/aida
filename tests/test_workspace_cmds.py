"""Tests for aida.cli.workspace_cmds — the ``aida workspace``
list/show/new/edit subcommand (Phase 4)."""

from __future__ import annotations

from pathlib import Path

from aida.cli.workspace_cmds import _build_parser, cmd_edit, cmd_list, cmd_new, cmd_show
from aida.config.settings import (
    ProviderProfile,
    WorkspacesConfig,
    load_settings,
    load_workspaces_config,
)


def _settings_with_profile(name="p1"):
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(name=name, kind="openai_compat", model="m1")
    return settings


def _parse(*argv):
    return _build_parser().parse_args(list(argv))


def test_cmd_list_no_workspaces(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    rc = cmd_list(_parse("list"))
    assert rc == 0
    assert "No workspaces configured." in capsys.readouterr().out


def test_cmd_new_creates_and_persists_to_disk(aida_home: Path, records_home: Path, capsys, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)

    # tmp_path (not a hardcoded "/tmp") so this is reachable on every OS —
    # a literal "/tmp" doesn't exist on Windows, which is exactly what broke
    # this test in CI (windows-latest, Python 3.13).
    source_folder = str(tmp_path)
    rc = cmd_new(
        _parse(
            "new", "ws1",
            "--profile", "p1",
            "--mcp-group", "none",
            "--source-folders", source_folder,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Created workspace 'ws1'" in out
    assert "[ok] no warnings" in out  # profile p1 exists, mcp_group none, source_folder exists

    reloaded = load_workspaces_config(aida_home)
    assert "ws1" in reloaded.workspaces
    assert reloaded.workspaces["ws1"].profile == "p1"
    assert reloaded.workspaces["ws1"].source_folders == [source_folder]


def test_cmd_new_refuses_to_clobber_existing(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(_parse("new", "ws1", "--profile", "p1"))
    capsys.readouterr()

    rc = cmd_new(_parse("new", "ws1", "--profile", "p1"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "already exists" in out


def test_cmd_show_unknown_workspace(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    rc = cmd_show(_parse("show", "does-not-exist"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does-not-exist" in out


def test_cmd_show_known_workspace_prints_fields_and_validation(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(_parse("new", "ws1", "--profile", "p1", "--system-prompt", "You are helpful."))
    capsys.readouterr()

    rc = cmd_show(_parse("show", "ws1"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "name:               ws1" in out
    assert "profile:            p1" in out
    assert "You are helpful." in out


def test_cmd_edit_unknown_workspace(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    rc = cmd_edit(_parse("edit", "does-not-exist", "--profile", "p1"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does-not-exist" in out


def test_cmd_edit_only_overwrites_passed_fields(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(
        _parse(
            "new", "ws1",
            "--profile", "p1",
            "--skills", "a,b",
            "--system-prompt", "original prompt",
        )
    )
    capsys.readouterr()

    rc = cmd_edit(_parse("edit", "ws1", "--safety", "relaxed"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Updated workspace 'ws1'" in out

    reloaded = load_workspaces_config(aida_home)
    ws = reloaded.workspaces["ws1"]
    assert ws.safety == "relaxed"          # changed
    assert ws.profile == "p1"              # left alone
    assert ws.skills == ["a", "b"]         # left alone
    assert ws.system_prompt == "original prompt"  # left alone


def test_cmd_edit_updates_profile(aida_home: Path, records_home: Path, capsys, monkeypatch):
    def _settings_with_two_profiles():
        settings = load_settings()
        settings.providers.profiles["p1"] = ProviderProfile(name="p1", kind="openai_compat", model="m1")
        settings.providers.profiles["p2"] = ProviderProfile(name="p2", kind="openai_compat", model="m2")
        return settings

    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_two_profiles)
    cmd_new(_parse("new", "ws1", "--profile", "p1"))
    capsys.readouterr()

    cmd_edit(_parse("edit", "ws1", "--profile", "p2"))
    capsys.readouterr()

    reloaded = load_workspaces_config(aida_home)
    assert reloaded.workspaces["ws1"].profile == "p2"


def test_cmd_list_shows_created_workspaces(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(_parse("new", "ws1", "--profile", "p1", "--mcp-group", "analysis"))
    capsys.readouterr()

    rc = cmd_list(_parse("list"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ws1" in out
    assert "p1" in out
    assert "analysis" in out


# --- Phase 6: one-time relaxed-mode warning ----------------------------------


def test_cmd_new_with_relaxed_safety_prints_warning(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    rc = cmd_new(_parse("new", "ws1", "--profile", "p1", "--safety", "relaxed"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Relaxed mode" in out


def test_cmd_new_with_confirm_safety_prints_no_relaxed_warning(
    aida_home: Path, records_home: Path, capsys, monkeypatch
):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    rc = cmd_new(_parse("new", "ws1", "--profile", "p1", "--safety", "confirm"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Relaxed mode" not in out


def test_cmd_edit_enabling_relaxed_prints_warning(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(_parse("new", "ws1", "--profile", "p1", "--safety", "confirm"))
    capsys.readouterr()

    rc = cmd_edit(_parse("edit", "ws1", "--safety", "relaxed"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Relaxed mode" in out


def test_cmd_edit_already_relaxed_does_not_reprint_warning(aida_home: Path, records_home: Path, capsys, monkeypatch):
    """The "one-time" part: re-saving an already-relaxed workspace (e.g.
    editing an unrelated field) must not show the warning again."""
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(_parse("new", "ws1", "--profile", "p1", "--safety", "relaxed"))
    capsys.readouterr()

    rc = cmd_edit(_parse("edit", "ws1", "--system-prompt", "still relaxed"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Relaxed mode" not in out


def test_cmd_edit_switching_from_relaxed_to_confirm_prints_no_warning(
    aida_home: Path, records_home: Path, capsys, monkeypatch
):
    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_profile)
    cmd_new(_parse("new", "ws1", "--profile", "p1", "--safety", "relaxed"))
    capsys.readouterr()

    rc = cmd_edit(_parse("edit", "ws1", "--safety", "confirm"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Relaxed mode" not in out


def test_two_workspaces_resolve_to_different_environments(aida_home: Path, records_home: Path, capsys, monkeypatch):
    """Phase 4 acceptance criterion: two workspaces demonstrably load
    different provider/skills environments."""
    from aida.workspace.workspaces import resolve_workspace_environment

    def _settings_with_two_profiles():
        settings = load_settings()
        settings.providers.profiles["p1"] = ProviderProfile(name="p1", kind="openai_compat", model="m1")
        settings.providers.profiles["p2"] = ProviderProfile(name="p2", kind="openai_compat", model="m2")
        return settings

    monkeypatch.setattr("aida.cli.workspace_cmds.load_settings", _settings_with_two_profiles)
    cmd_new(_parse("new", "use-pyirena", "--profile", "p1", "--skills", "saxs-basics"))
    cmd_new(_parse("new", "plain-chat", "--profile", "p2"))
    capsys.readouterr()

    settings = _settings_with_two_profiles()
    reloaded = load_workspaces_config(aida_home)
    settings.workspaces = WorkspacesConfig(workspaces=reloaded.workspaces)

    env_a = resolve_workspace_environment(settings, settings.workspaces.workspaces["use-pyirena"])
    env_b = resolve_workspace_environment(settings, settings.workspaces.workspaces["plain-chat"])

    assert env_a.profile_name == "p1"
    assert env_b.profile_name == "p2"
    assert env_a.skill_names == ["saxs-basics"]
    assert env_b.skill_names == []
