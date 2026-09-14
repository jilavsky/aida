"""Tests for aida.cli.mcp_cmds — the ``aida mcp`` server/group/import/test
subcommands (Phase 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.mcp_cmds import main
from aida.config.settings import load_mcp_config

# --- server list/show ---------------------------------------------------


def test_server_list_empty(aida_home: Path, capsys):
    assert main(["server", "list"]) == 0
    assert "No MCP servers configured." in capsys.readouterr().out


def test_server_show_unknown(aida_home: Path, capsys):
    assert main(["server", "show", "nope"]) == 1
    assert "Unknown MCP server" in capsys.readouterr().out


# --- server add ----------------------------------------------------------


def test_server_add_persists_to_disk(aida_home: Path, capsys):
    rc = main(
        [
            "server",
            "add",
            "pyirena",
            "--command",
            "/opt/pyirena-mcp",
            "--arg=--stdio",
            "--env",
            "FOO=bar",
            "--groups",
            "analysis,full",
            "--skills",
            "saxs-basics",
        ]
    )
    assert rc == 0
    assert "Added" in capsys.readouterr().out

    loaded = load_mcp_config(aida_home)
    server = loaded.servers["pyirena"]
    assert server.command == "/opt/pyirena-mcp"
    assert server.args == ["--stdio"]
    assert server.env == {"FOO": "bar"}
    assert server.groups == ["analysis", "full"]
    assert server.skills == ["saxs-basics"]


def test_server_add_refuses_to_clobber_existing(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/a"])
    rc = main(["server", "add", "pyirena", "--command", "/b"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().out
    assert load_mcp_config(aida_home).servers["pyirena"].command == "/a"


def test_server_add_rejects_malformed_env(aida_home: Path, capsys):
    rc = main(["server", "add", "pyirena", "--env", "NOT_KEY_VALUE"])
    assert rc == 1
    assert "KEY=VALUE" in capsys.readouterr().out
    assert "pyirena" not in load_mcp_config(aida_home).servers


def test_server_add_http_type_persists_url_and_headers(aida_home: Path, capsys):
    rc = main(
        [
            "server",
            "add",
            "remote-instrument",
            "--type",
            "http",
            "--url",
            "https://usaxscontrol.example:8765/mcp",
            "--header",
            "Authorization=Bearer keyring:my_token",
            "--groups",
            "instrument-status",
        ]
    )
    assert rc == 0
    assert "Added" in capsys.readouterr().out

    server = load_mcp_config(aida_home).servers["remote-instrument"]
    assert server.type == "http"
    assert server.url == "https://usaxscontrol.example:8765/mcp"
    assert server.headers == {"Authorization": "Bearer keyring:my_token"}


def test_server_add_rejects_malformed_header(aida_home: Path, capsys):
    rc = main(["server", "add", "remote", "--type", "http", "--header", "NOT_KEY_VALUE"])
    assert rc == 1
    assert "KEY=VALUE" in capsys.readouterr().out
    assert "remote" not in load_mcp_config(aida_home).servers


# --- server show (populated) ---------------------------------------------


def test_server_show_known(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/opt/pyirena-mcp", "--groups", "analysis"])
    rc = main(["server", "show", "pyirena"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "type:           stdio" in out
    assert "command:        /opt/pyirena-mcp" in out
    assert "groups:         analysis" in out


def test_server_show_http_type(aida_home: Path, capsys):
    main(
        [
            "server",
            "add",
            "remote-instrument",
            "--type",
            "http",
            "--url",
            "https://usaxscontrol.example:8765/mcp",
            "--header",
            "Authorization=Bearer secret-token",
        ]
    )
    rc = main(["server", "show", "remote-instrument"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "type:           http" in out
    assert "url:            https://usaxscontrol.example:8765/mcp" in out
    assert "headers:        Authorization=***" in out
    assert "command:" not in out, "an http server has no command to show"


# --- server edit -----------------------------------------------------------


def test_server_edit_unknown(aida_home: Path, capsys):
    rc = main(["server", "edit", "nope", "--command", "/x"])
    assert rc == 1
    assert "Unknown MCP server" in capsys.readouterr().out


def test_server_edit_only_overwrites_passed_fields(aida_home: Path):
    main(
        [
            "server",
            "add",
            "pyirena",
            "--command",
            "/old",
            "--groups",
            "analysis",
            "--skills",
            "saxs",
        ]
    )
    main(["server", "edit", "pyirena", "--command", "/new"])

    server = load_mcp_config(aida_home).servers["pyirena"]
    assert server.command == "/new"
    assert server.groups == ["analysis"], "unset flags must leave existing fields untouched"
    assert server.skills == ["saxs"]


def test_server_edit_leaves_type_unchanged_when_flag_omitted(aida_home: Path):
    main(
        [
            "server",
            "add",
            "remote-instrument",
            "--type",
            "http",
            "--url",
            "https://old.example/mcp",
        ]
    )
    main(["server", "edit", "remote-instrument", "--url", "https://new.example/mcp"])

    server = load_mcp_config(aida_home).servers["remote-instrument"]
    assert server.type == "http", "unset --type must leave the transport untouched"
    assert server.url == "https://new.example/mcp"


def test_server_edit_preserves_disabled_and_confirm_tools(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x"])
    main(["server", "disable-tool", "pyirena", "plot_saxs"])
    main(["server", "edit", "pyirena", "--command", "/y"])

    server = load_mcp_config(aida_home).servers["pyirena"]
    assert server.disabled_tools == ["plot_saxs"], (
        "editing an unrelated field must not drop tool permissions"
    )


# --- server remove -----------------------------------------------------------


def test_server_remove_unknown(aida_home: Path, capsys):
    rc = main(["server", "remove", "nope", "--yes"])
    assert rc == 1


def test_server_remove_with_yes_flag_skips_prompt(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x"])
    rc = main(["server", "remove", "pyirena", "--yes"])
    assert rc == 0
    assert "pyirena" not in load_mcp_config(aida_home).servers


def test_server_remove_without_yes_prompts_and_respects_no(aida_home: Path, monkeypatch):
    main(["server", "add", "pyirena", "--command", "/x"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    rc = main(["server", "remove", "pyirena"])
    assert rc == 1
    assert "pyirena" in load_mcp_config(aida_home).servers


# --- per-tool disable/confirm toggles ----------------------------------------


def test_disable_then_enable_tool_round_trips(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x"])
    main(["server", "disable-tool", "pyirena", "plot_saxs"])
    assert load_mcp_config(aida_home).servers["pyirena"].disabled_tools == ["plot_saxs"]

    main(["server", "enable-tool", "pyirena", "plot_saxs"])
    assert load_mcp_config(aida_home).servers["pyirena"].disabled_tools == []


def test_confirm_then_unconfirm_tool_round_trips(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x"])
    main(["server", "confirm-tool", "pyirena", "move_stage"])
    assert load_mcp_config(aida_home).servers["pyirena"].confirm_tools == ["move_stage"]

    main(["server", "unconfirm-tool", "pyirena", "move_stage"])
    assert load_mcp_config(aida_home).servers["pyirena"].confirm_tools == []


def test_disable_tool_on_unknown_server(aida_home: Path, capsys):
    rc = main(["server", "disable-tool", "nope", "x"])
    assert rc == 1
    assert "Unknown MCP server" in capsys.readouterr().out


def test_disable_tool_is_idempotent(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x"])
    main(["server", "disable-tool", "pyirena", "plot_saxs"])
    main(["server", "disable-tool", "pyirena", "plot_saxs"])
    assert load_mcp_config(aida_home).servers["pyirena"].disabled_tools == ["plot_saxs"]


# --- groups ------------------------------------------------------------------


def test_group_list_empty(aida_home: Path, capsys):
    assert main(["group", "list"]) == 0
    assert "No groups" in capsys.readouterr().out


def test_group_list_shows_members(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/x", "--groups", "analysis"])
    main(["server", "add", "bait", "--command", "/y", "--groups", "analysis"])
    rc = main(["group", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "analysis" in out
    assert "bait" in out and "pyirena" in out


def test_group_add_creates_a_brand_new_group(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/x"])
    main(["server", "add", "bait", "--command", "/y"])
    rc = main(["group", "add", "everything", "--servers", "pyirena,bait"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Added group 'everything' to 2 of 2 requested server(s)." in out

    loaded = load_mcp_config(aida_home)
    assert loaded.servers["pyirena"].groups == ["everything"]
    assert loaded.servers["bait"].groups == ["everything"]


def test_group_add_to_existing_group_only_updates_new_members(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/x", "--groups", "analysis"])
    main(["server", "add", "bait", "--command", "/y"])
    rc = main(["group", "add", "analysis", "--servers", "pyirena,bait"])
    assert rc == 0
    assert "Added group 'analysis' to 1 of 2 requested server(s)." in capsys.readouterr().out
    assert load_mcp_config(aida_home).servers["bait"].groups == ["analysis"]


def test_group_add_already_present_on_all_named_servers(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/x", "--groups", "analysis"])
    rc = main(["group", "add", "analysis", "--servers", "pyirena"])
    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out


def test_group_add_unknown_server_name(aida_home: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/x"])
    rc = main(["group", "add", "everything", "--servers", "pyirena,typo-name"])
    assert rc == 1
    assert "Unknown server name(s): typo-name" in capsys.readouterr().out
    assert load_mcp_config(aida_home).servers["pyirena"].groups == []


def test_group_add_requires_at_least_one_server(aida_home: Path, capsys):
    rc = main(["group", "add", "everything", "--servers", ""])
    assert rc == 1
    assert "zero members" in capsys.readouterr().out


def test_group_rename_updates_every_server(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x", "--groups", "analysis"])
    main(["server", "add", "bait", "--command", "/y", "--groups", "analysis"])
    rc = main(["group", "rename", "analysis", "full"])
    assert rc == 0

    loaded = load_mcp_config(aida_home)
    assert loaded.servers["pyirena"].groups == ["full"]
    assert loaded.servers["bait"].groups == ["full"]


def test_group_rename_unknown_group(aida_home: Path, capsys):
    rc = main(["group", "rename", "does-not-exist", "x"])
    assert rc == 1
    assert "nothing to rename" in capsys.readouterr().out


def test_group_delete_removes_from_every_server(aida_home: Path):
    main(["server", "add", "pyirena", "--command", "/x", "--groups", "analysis,full"])
    rc = main(["group", "delete", "analysis"])
    assert rc == 0
    assert load_mcp_config(aida_home).servers["pyirena"].groups == ["full"]


def test_group_delete_unknown_group(aida_home: Path, capsys):
    rc = main(["group", "delete", "does-not-exist"])
    assert rc == 1


# --- import --------------------------------------------------------------


def test_import_adds_new_servers(aida_home: Path, tmp_path: Path, capsys):
    config_file = tmp_path / "claude_desktop.json"
    config_file.write_text(
        '{"mcpServers": {"bait": {"command": "/opt/bait-mcp", "disabled": false}}}'
    )

    rc = main(["import", str(config_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "added:       bait" in out

    server = load_mcp_config(aida_home).servers["bait"]
    assert server.command == "/opt/bait-mcp"
    assert server.extra == {"disabled": False}, "unknown keys from the imported file must survive"


def test_import_skips_conflicts_by_default(aida_home: Path, tmp_path: Path, capsys):
    main(["server", "add", "pyirena", "--command", "/existing"])
    config_file = tmp_path / "import.json"
    config_file.write_text('{"mcpServers": {"pyirena": {"command": "/imported"}}}')

    rc = main(["import", str(config_file)])
    assert rc == 0
    assert "skipped" in capsys.readouterr().out
    assert load_mcp_config(aida_home).servers["pyirena"].command == "/existing"


def test_import_with_explicit_overwrite(aida_home: Path, tmp_path: Path):
    main(["server", "add", "pyirena", "--command", "/existing"])
    config_file = tmp_path / "import.json"
    config_file.write_text('{"mcpServers": {"pyirena": {"command": "/imported"}}}')

    rc = main(["import", str(config_file), "--overwrite", "pyirena"])
    assert rc == 0
    assert load_mcp_config(aida_home).servers["pyirena"].command == "/imported"


def test_import_missing_file(aida_home: Path, capsys):
    rc = main(["import", "/does/not/exist.json"])
    assert rc == 1
    assert "Could not read" in capsys.readouterr().out


def test_import_invalid_json(aida_home: Path, tmp_path: Path, capsys):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json")
    rc = main(["import", str(bad_file)])
    assert rc == 1
    assert "not valid JSON" in capsys.readouterr().out


# --- test connection -------------------------------------------------------


def test_test_unknown_server(aida_home: Path, capsys):
    rc = main(["test", "nope"])
    assert rc == 1
    assert "Unknown MCP server" in capsys.readouterr().out


def test_test_against_a_broken_command(aida_home: Path, capsys):
    main(["server", "add", "broken", "--command", "definitely-not-a-real-executable"])
    rc = main(["test", "broken"])
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out


def test_test_against_a_working_server(aida_home: Path, capsys):
    import sys

    mock_server_path = Path(__file__).parent / "mock_mcp_server.py"
    main(["server", "add", "mock-mcp", "--command", sys.executable, "--arg", str(mock_server_path)])
    rc = main(["test", "mock-mcp"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


# --- required subcommand -----------------------------------------------------


def test_bare_mcp_requires_a_subcommand():
    with pytest.raises(SystemExit):
        main([])


# --- add-pyirena / find-pyirena -------------------------------------------


def _fake_candidate(command: str = "/opt/envs/pyirena/bin/pyirena-mcp"):
    from aida.mcp.pyirena_setup import PyirenaMcpCandidate

    return PyirenaMcpCandidate(command=command, source="conda env 'pyirena'")


def test_find_pyirena_reports_nothing_found(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.mcp_cmds.find_pyirena_mcp", list)
    assert main(["find-pyirena"]) == 1
    assert 'pip install "pyirena[mcp]"' in capsys.readouterr().out


def test_add_pyirena_configures_the_server_group_and_skills(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.mcp_cmds.find_pyirena_mcp", lambda: [_fake_candidate()])
    monkeypatch.setattr("aida.cli.mcp_cmds.pyirena_version", lambda _c: "1.1.0")

    assert main(["add-pyirena"]) == 0

    saved = load_mcp_config().servers["pyirena"]
    assert saved.command == "/opt/envs/pyirena/bin/pyirena-mcp"
    assert saved.groups == ["pyirena-analysis"]
    assert "pyirena-usage" in saved.skills
    out = capsys.readouterr().out
    assert "pyIrena 1.1.0" in out
    # The tip is the whole point of the command: a configured server nobody
    # points a workspace at is invisible in the chat window.
    assert "--mcp-group pyirena-analysis" in out


def test_add_pyirena_sets_the_data_root_env_var(aida_home: Path, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aida.cli.mcp_cmds.find_pyirena_mcp", lambda: [_fake_candidate()])
    monkeypatch.setattr("aida.cli.mcp_cmds.pyirena_version", lambda _c: None)

    assert main(["add-pyirena", "--data-root", str(tmp_path)]) == 0

    assert load_mcp_config().servers["pyirena"].env["PYIRENA_DATA_ROOT"] == str(tmp_path)


def test_add_pyirena_asks_which_one_when_several_are_found(aida_home: Path, capsys, monkeypatch):
    """Two candidates from genuinely different environments is a real
    choice — silently taking one could point AIDA at a pyIrena the user
    never meant to use."""
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_pyirena_mcp",
        lambda: [_fake_candidate("/a/pyirena-mcp"), _fake_candidate("/b/pyirena-mcp")],
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.pyirena_version", lambda _c: None)

    assert main(["add-pyirena"]) == 1
    assert "--first" in capsys.readouterr().out
    assert "pyirena" not in load_mcp_config().servers

    assert main(["add-pyirena", "--first"]) == 0
    assert load_mcp_config().servers["pyirena"].command == "/a/pyirena-mcp"


def test_add_pyirena_refuses_to_clobber_without_force(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.mcp_cmds.find_pyirena_mcp", lambda: [_fake_candidate()])
    monkeypatch.setattr("aida.cli.mcp_cmds.pyirena_version", lambda _c: None)
    assert main(["add-pyirena"]) == 0

    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_pyirena_mcp", lambda: [_fake_candidate("/other/pyirena-mcp")]
    )
    assert main(["add-pyirena"]) == 1
    assert load_mcp_config().servers["pyirena"].command == "/opt/envs/pyirena/bin/pyirena-mcp"

    assert main(["add-pyirena", "--force"]) == 0
    assert load_mcp_config().servers["pyirena"].command == "/other/pyirena-mcp"


def test_add_pyirena_accepts_an_explicit_command_without_detection(aida_home: Path, monkeypatch):
    def _must_not_be_called():
        raise AssertionError("--command must skip detection entirely")

    monkeypatch.setattr("aida.cli.mcp_cmds.find_pyirena_mcp", _must_not_be_called)
    monkeypatch.setattr("aida.cli.mcp_cmds.pyirena_version", lambda _c: None)

    assert main(["add-pyirena", "--command", "/custom/pyirena-mcp"]) == 0
    assert load_mcp_config().servers["pyirena"].command == "/custom/pyirena-mcp"


# --- add-aievaluator / find-aievaluator -----------------------------------


def _fake_aievaluator_candidate(command: str = "/opt/envs/aievaluator/bin/aievaluator-mcp"):
    from aida.mcp.aievaluator_setup import AievaluatorMcpCandidate

    return AievaluatorMcpCandidate(command=command, source="conda env 'aievaluator'")


def test_find_aievaluator_reports_nothing_found(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_mcp", list)
    assert main(["find-aievaluator"]) == 1
    assert 'pip install "aievaluator[mcp]"' in capsys.readouterr().out


def test_add_aievaluator_configures_the_server_and_groups(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_mcp", lambda: [_fake_aievaluator_candidate()]
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_skills_dir", lambda _c: None)

    assert main(["add-aievaluator", "--epics-addr", "10.0.0.1:5064"]) == 0

    saved = load_mcp_config().servers["aievaluator-mcp"]
    assert saved.command == "/opt/envs/aievaluator/bin/aievaluator-mcp"
    assert saved.groups == ["instrument-status", "instrument-staff"]
    assert saved.confirm_tools == ["fitness_report"]
    assert saved.env["EPICS_CA_ADDR_LIST"] == "10.0.0.1:5064"


def test_add_aievaluator_installs_skills_when_found(
    aida_home: Path, capsys, monkeypatch, tmp_path: Path
):
    skills_src = tmp_path / "aievaluator-checkout" / "skills"
    skills_src.mkdir(parents=True)
    (skills_src / "aievaluator.md").write_text("# aievaluator\n", encoding="utf-8")
    (skills_src / "usaxs-instrument.md").write_text("# usaxs\n", encoding="utf-8")

    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_mcp", lambda: [_fake_aievaluator_candidate()]
    )
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_skills_dir", lambda _c: skills_src
    )

    assert main(["add-aievaluator"]) == 0

    out = capsys.readouterr().out
    assert "installed" in out
    assert (aida_home / "skills" / "aievaluator.md").is_file()
    assert (aida_home / "skills" / "usaxs-instrument.md").is_file()


def test_add_aievaluator_skills_dir_override_wins_over_auto_detect(
    aida_home: Path, monkeypatch, tmp_path: Path
):
    skills_src = tmp_path / "override-skills"
    skills_src.mkdir()
    (skills_src / "aievaluator.md").write_text("# aievaluator\n", encoding="utf-8")

    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_mcp", lambda: [_fake_aievaluator_candidate()]
    )

    def _must_not_be_called(_c):
        raise AssertionError("--skills-dir must skip auto-detection")

    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_skills_dir", _must_not_be_called)

    assert main(["add-aievaluator", "--skills-dir", str(skills_src)]) == 0
    assert (aida_home / "skills" / "aievaluator.md").is_file()


def test_add_aievaluator_asks_which_one_when_several_are_found(
    aida_home: Path, capsys, monkeypatch
):
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_mcp",
        lambda: [
            _fake_aievaluator_candidate("/a/aievaluator-mcp"),
            _fake_aievaluator_candidate("/b/aievaluator-mcp"),
        ],
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_skills_dir", lambda _c: None)

    assert main(["add-aievaluator"]) == 1
    assert "--first" in capsys.readouterr().out
    assert "aievaluator-mcp" not in load_mcp_config().servers

    assert main(["add-aievaluator", "--first"]) == 0
    assert load_mcp_config().servers["aievaluator-mcp"].command == "/a/aievaluator-mcp"


def test_add_aievaluator_refuses_to_clobber_without_force(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_mcp", lambda: [_fake_aievaluator_candidate()]
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_skills_dir", lambda _c: None)
    assert main(["add-aievaluator"]) == 0

    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_aievaluator_mcp",
        lambda: [_fake_aievaluator_candidate("/other/aievaluator-mcp")],
    )
    assert main(["add-aievaluator"]) == 1
    assert (
        load_mcp_config().servers["aievaluator-mcp"].command
        == "/opt/envs/aievaluator/bin/aievaluator-mcp"
    )

    assert main(["add-aievaluator", "--force"]) == 0
    assert load_mcp_config().servers["aievaluator-mcp"].command == "/other/aievaluator-mcp"


def test_add_aievaluator_accepts_an_explicit_command_without_detection(
    aida_home: Path, monkeypatch
):
    def _must_not_be_called():
        raise AssertionError("--command must skip detection entirely")

    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_mcp", _must_not_be_called)
    monkeypatch.setattr("aida.cli.mcp_cmds.find_aievaluator_skills_dir", lambda _c: None)

    assert main(["add-aievaluator", "--command", "/custom/aievaluator-mcp"]) == 0
    assert load_mcp_config().servers["aievaluator-mcp"].command == "/custom/aievaluator-mcp"


# --- add-epics-mcp / find-epics-mcp ---------------------------------------


def _fake_epics_mcp_candidate(command: str = "/opt/envs/epics-mcp/bin/epics-mcp"):
    from aida.mcp.env_discovery import ScriptCandidate

    return ScriptCandidate(command=command, source="conda env 'epics-mcp'")


def test_find_epics_mcp_reports_nothing_found(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.mcp_cmds.find_epics_mcp", list)
    assert main(["find-epics-mcp"]) == 1


def test_add_epics_mcp_defaults_to_read_only_only(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_epics_mcp", lambda: [_fake_epics_mcp_candidate()]
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.find_epics_mcp_examples_dir", lambda _c: None)

    assert main(["add-epics-mcp", "--epics-addr", "10.0.0.1:5064"]) == 0

    servers = load_mcp_config().servers
    assert "epics-mcp-user" in servers
    assert "epics-mcp-staff" not in servers
    assert servers["epics-mcp-user"].args == ["--policy", "usaxs-user"]
    assert servers["epics-mcp-user"].env["EPICS_CA_ADDR_LIST"] == "10.0.0.1:5064"
    out = capsys.readouterr().out
    assert "Read-only only" in out


def test_add_epics_mcp_staff_flag_adds_the_write_capable_server(
    aida_home: Path, capsys, monkeypatch
):
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_epics_mcp", lambda: [_fake_epics_mcp_candidate()]
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.find_epics_mcp_examples_dir", lambda _c: None)

    assert main(["add-epics-mcp", "--staff"]) == 0

    servers = load_mcp_config().servers
    assert "epics-mcp-staff" in servers
    assert servers["epics-mcp-staff"].args == ["--policy", "usaxs-staff"]
    assert servers["epics-mcp-staff"].confirm_tools == ["epics_pv_put"]


def test_add_epics_mcp_installs_policies_when_examples_dir_found(
    aida_home: Path, monkeypatch, tmp_path: Path
):
    examples = tmp_path / "epics-mcp-checkout" / "examples"
    examples.mkdir(parents=True)
    (examples / "policy_usaxs_readonly.yaml").write_text("mode: read-only\n", encoding="utf-8")
    (examples / "policy_usaxs_staff.yaml").write_text("mode: read-write\n", encoding="utf-8")
    (examples / "pv_catalog_usaxs.txt").write_text("usxLAX:m1\n", encoding="utf-8")

    policy_dir = tmp_path / "policies"
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_epics_mcp", lambda: [_fake_epics_mcp_candidate()]
    )
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_epics_mcp_examples_dir", lambda _c: examples
    )
    monkeypatch.setattr(
        "aida.mcp.epics_mcp_setup.default_policy_dir", lambda: policy_dir
    )

    assert main(["add-epics-mcp"]) == 0
    assert (policy_dir / "usaxs-user.yaml").is_file()
    assert (policy_dir / "pv_catalog_usaxs.txt").is_file()
    assert not (policy_dir / "usaxs-staff.yaml").exists()


def test_add_epics_mcp_refuses_to_clobber_without_force(aida_home: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_epics_mcp", lambda: [_fake_epics_mcp_candidate()]
    )
    monkeypatch.setattr("aida.cli.mcp_cmds.find_epics_mcp_examples_dir", lambda _c: None)
    assert main(["add-epics-mcp"]) == 0

    monkeypatch.setattr(
        "aida.cli.mcp_cmds.find_epics_mcp",
        lambda: [_fake_epics_mcp_candidate("/other/epics-mcp")],
    )
    assert main(["add-epics-mcp"]) == 1
    assert load_mcp_config().servers["epics-mcp-user"].command == "/opt/envs/epics-mcp/bin/epics-mcp"

    assert main(["add-epics-mcp", "--force"]) == 0
    assert load_mcp_config().servers["epics-mcp-user"].command == "/other/epics-mcp"


def test_add_epics_mcp_accepts_an_explicit_command_without_detection(aida_home: Path, monkeypatch):
    def _must_not_be_called():
        raise AssertionError("--command must skip detection entirely")

    monkeypatch.setattr("aida.cli.mcp_cmds.find_epics_mcp", _must_not_be_called)
    monkeypatch.setattr("aida.cli.mcp_cmds.find_epics_mcp_examples_dir", lambda _c: None)

    assert main(["add-epics-mcp", "--command", "/custom/epics-mcp"]) == 0
    assert load_mcp_config().servers["epics-mcp-user"].command == "/custom/epics-mcp"
