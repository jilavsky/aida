"""Tests for ``aida workflow`` (aida.cli.workflow_cmds)."""

from __future__ import annotations

import json
from pathlib import Path

from aida.cli.workflow_cmds import EXIT_CONFIG_ERROR, EXIT_OK, EXIT_STEP_FAILED, main
from aida.config.settings import (
    ProviderProfile,
    Settings,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
    save_workflow,
)
from aida.providers.mock import MockProvider, MockTurn


def _settings() -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", safety="relaxed")}
    )
    return settings


def test_list_reports_no_workflows(aida_home: Path, records_home: Path, capsys):
    code = main(["list"])
    assert code == EXIT_OK
    assert "No workflows stored." in capsys.readouterr().out


def test_list_shows_stored_workflow(aida_home: Path, records_home: Path, capsys):
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    code = main(["list"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "daily" in out
    assert "use-ws" in out


def test_show_unknown_workflow(aida_home: Path, records_home: Path, capsys):
    code = main(["show", "does-not-exist"])
    assert code == EXIT_CONFIG_ERROR
    assert "does-not-exist" in capsys.readouterr().err


def test_show_known_workflow_prints_steps(aida_home: Path, records_home: Path, capsys):
    save_workflow(
        WorkflowConfig(
            name="daily",
            workspace="use-ws",
            description="a test workflow",
            steps=[WorkflowStep(prompt="step one"), WorkflowStep(prompt="step two", expect_files=["*.png"])],
        )
    )
    code = main(["show", "daily"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "a test workflow" in out
    assert "step one" in out
    assert "step two" in out
    assert "*.png" in out


def test_validate_ok(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))

    code = main(["validate", "daily"])

    assert code == EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_validate_reports_unknown_workspace(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    save_workflow(WorkflowConfig(name="daily", workspace="does-not-exist", steps=[WorkflowStep(prompt="go")]))

    code = main(["validate", "daily"])

    assert code == EXIT_CONFIG_ERROR
    assert "unknown workspace" in capsys.readouterr().out


def test_validate_reports_missing_var(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go to {folder}")]))

    code = main(["validate", "daily"])

    assert code == EXIT_CONFIG_ERROR
    assert "folder" in capsys.readouterr().out


def test_validate_accepts_var_override(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go to {folder}")]))

    code = main(["validate", "daily", "--var", "folder=/data"])

    assert code == EXIT_OK


def test_run_reports_success(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))

    code = main(["run", "daily", "--json"])

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow"] == "daily"


def test_run_reports_failure_exit_code(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(error="boom")]))
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))

    code = main(["run", "daily", "--json"])

    assert code == EXIT_STEP_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_run_missing_workflow_is_config_error(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)

    code = main(["run", "does-not-exist"])

    assert code == EXIT_CONFIG_ERROR


def test_run_bad_var_syntax_is_config_error(aida_home: Path, records_home: Path, capsys, monkeypatch):
    monkeypatch.setattr("aida.cli.workflow_cmds.load_settings", _settings)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))

    code = main(["run", "daily", "--var", "not-a-kv-pair"])

    assert code == EXIT_CONFIG_ERROR
