"""Tests for ``aida schedule`` (aida.cli.schedule_cmds)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.schedule_cmds import EXIT_CONFIG_ERROR, EXIT_OK, main
from aida.config.settings import (
    ProviderProfile,
    Settings,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    load_schedules_config,
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


def _workflow(name: str = "daily") -> None:
    save_workflow(WorkflowConfig(name=name, workspace="use-ws", steps=[WorkflowStep(prompt="go")]))


def test_list_reports_no_schedules(aida_home: Path, records_home: Path, capsys):
    code = main(["list"])
    assert code == EXIT_OK
    assert "No schedules configured." in capsys.readouterr().out


def test_add_persists_to_disk(aida_home: Path, records_home: Path, capsys):
    code = main(["add", "nightly", "--workflow", "daily", "--at", "07:00"])

    assert code == EXIT_OK
    config = load_schedules_config()
    entry = config.schedules["nightly"]
    assert entry.workflow == "daily"
    assert entry.at == "07:00"
    assert entry.enabled is True


def test_add_rejects_both_at_and_every(aida_home: Path, records_home: Path, capsys):
    # argparse's mutually-exclusive group rejects this before cmd_add even
    # runs, via sys.exit rather than a returned code.
    with pytest.raises(SystemExit):
        main(["add", "nightly", "--workflow", "daily", "--at", "07:00", "--every", "4h"])


def test_add_rejects_invalid_at(aida_home: Path, records_home: Path, capsys):
    code = main(["add", "nightly", "--workflow", "daily", "--at", "not-a-time"])
    assert code == EXIT_CONFIG_ERROR


def test_add_refuses_to_clobber_existing(aida_home: Path, records_home: Path, capsys):
    main(["add", "nightly", "--workflow", "daily", "--at", "07:00"])
    code = main(["add", "nightly", "--workflow", "daily", "--every", "4h"])
    assert code == EXIT_CONFIG_ERROR


def test_enable_disable_roundtrip(aida_home: Path, records_home: Path, capsys):
    main(["add", "nightly", "--workflow", "daily", "--at", "07:00"])

    main(["disable", "nightly"])
    assert load_schedules_config().schedules["nightly"].enabled is False

    main(["enable", "nightly"])
    assert load_schedules_config().schedules["nightly"].enabled is True


def test_disable_unknown_schedule_is_config_error(aida_home: Path, records_home: Path, capsys):
    code = main(["disable", "does-not-exist"])
    assert code == EXIT_CONFIG_ERROR


def test_remove_deletes_the_entry(aida_home: Path, records_home: Path, capsys):
    main(["add", "nightly", "--workflow", "daily", "--at", "07:00"])
    code = main(["remove", "nightly"])
    assert code == EXIT_OK
    assert "nightly" not in load_schedules_config().schedules


def test_remove_unknown_schedule_is_config_error(aida_home: Path, records_home: Path, capsys):
    code = main(["remove", "does-not-exist"])
    assert code == EXIT_CONFIG_ERROR


def test_run_fires_regardless_of_due_state(monkeypatch, aida_home: Path, records_home: Path, capsys):
    """A schedule set for "every 24h" that already fired seconds ago is
    not due — 'aida schedule run NAME' must still fire it, since its whole
    point is testing a schedule without waiting for its next slot."""
    monkeypatch.setattr("aida.cli.schedule_cmds.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")] * 5))
    _workflow()
    main(["add", "nightly", "--workflow", "daily", "--every", "24h"])

    first = main(["run", "nightly"])
    assert first == EXIT_OK
    assert "[ok] nightly" in capsys.readouterr().out

    second = main(["run", "nightly"])  # not due by the clock, but forced
    assert second == EXIT_OK
    assert "[ok] nightly" in capsys.readouterr().out


def test_run_unknown_schedule_is_config_error(aida_home: Path, records_home: Path, capsys):
    code = main(["run", "does-not-exist"])
    assert code == EXIT_CONFIG_ERROR
