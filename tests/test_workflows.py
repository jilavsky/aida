"""Tests for aida.core.workflows.run_workflow — the multi-step-in-one-
session engine behind ``aida workflow run`` and the scheduler.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aida.config.settings import (
    ProviderProfile,
    Settings,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.core.headless import build_headless_confirm_callback
from aida.core.workflows import (
    WorkflowConfigError,
    render_step_prompt,
    run_workflow,
)
from aida.providers.mock import MockProvider, MockToolCall, MockTurn


def _settings(*, target_folder: str | None = None) -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-ws": WorkspaceConfig(
                name="use-ws",
                profile="mock-profile",
                target_folder=target_folder,
                safety="relaxed",
            )
        }
    )
    return settings


def _confirm(**kwargs):
    return build_headless_confirm_callback(**kwargs)


@pytest.mark.asyncio
async def test_run_workflow_single_step_succeeds(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="use-ws", steps=[WorkflowStep(prompt="Do the thing.")])

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is True
    assert result.conversation_id is not None
    assert len(result.steps) == 1
    assert result.steps[0].ok is True
    assert result.steps[0].stop_reason == "stop"


@pytest.mark.asyncio
async def test_run_workflow_resolves_placeholders_from_workflow_vars(monkeypatch, aida_home: Path, records_home: Path):
    provider = MockProvider([MockTurn(text="done")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings()
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        vars={"folder": "/data/default"},
        steps=[WorkflowStep(prompt="Reduce scans in {folder}.")],
    )

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is True
    assert result.steps[0].prompt == "Reduce scans in /data/default."


@pytest.mark.asyncio
async def test_run_workflow_var_override_wins_over_workflow_default(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    settings = _settings()
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        vars={"folder": "/data/default"},
        steps=[WorkflowStep(prompt="Reduce scans in {folder}.")],
    )

    result = await run_workflow(
        settings,
        workflow,
        var_overrides={"folder": "/data/override"},
        confirm_callback=_confirm(yes_in_allowed=False),
        origin="workflow",
    )

    assert result.steps[0].prompt == "Reduce scans in /data/override."


@pytest.mark.asyncio
async def test_run_workflow_missing_var_raises_before_any_session_work(aida_home: Path, records_home: Path):
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="use-ws", steps=[WorkflowStep(prompt="Reduce {folder}.")])

    with pytest.raises(WorkflowConfigError, match="folder"):
        await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")


def test_render_step_prompt_missing_var_names_it():
    with pytest.raises(WorkflowConfigError, match="folder"):
        render_step_prompt(WorkflowStep(prompt="Reduce {folder}."), {})


def test_render_step_prompt_substitutes_present_var():
    assert render_step_prompt(WorkflowStep(prompt="Reduce {folder}."), {"folder": "/x"}) == "Reduce /x."


@pytest.mark.asyncio
async def test_run_workflow_unknown_workspace_raises_config_error(aida_home: Path, records_home: Path):
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="does-not-exist", steps=[WorkflowStep(prompt="hi")])

    with pytest.raises(WorkflowConfigError):
        await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")


@pytest.mark.asyncio
async def test_run_workflow_no_workspace_configured_raises_config_error(aida_home: Path, records_home: Path):
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="", steps=[WorkflowStep(prompt="hi")])

    with pytest.raises(WorkflowConfigError, match="workspace"):
        await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")


@pytest.mark.asyncio
async def test_run_workflow_stops_at_first_agent_error(monkeypatch, aida_home: Path, records_home: Path):
    provider = MockProvider([MockTurn(error="boom"), MockTurn(text="should not be reached")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings()
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt="step 1"), WorkflowStep(prompt="step 2")],
    )

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is False
    assert len(result.steps) == 1  # step 2 never ran
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_run_workflow_records_tool_calls(monkeypatch, aida_home: Path, records_home: Path):
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_current_time", id="call_1")]),
            MockTurn(text="it is now"),
        ]
    )
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="use-ws", steps=[WorkflowStep(prompt="what time is it?")])

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is True
    assert result.steps[0].tool_calls == [{"tool_name": "get_current_time", "is_error": False}]


@pytest.mark.asyncio
async def test_run_workflow_expect_files_satisfied(monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "plot.png").write_bytes(b"fake")
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="plot it", expect_files=["*.png"])]
    )

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is True
    assert result.steps[0].missing_expect_files == []


@pytest.mark.asyncio
async def test_run_workflow_expect_files_unmet_fails_the_step(monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="plot it", expect_files=["*.png"])]
    )

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is False
    assert result.steps[0].missing_expect_files == ["*.png"]


@pytest.mark.asyncio
async def test_run_workflow_expect_files_with_no_target_folder_always_fails(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    settings = _settings(target_folder=None)
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="plot it", expect_files=["*.png"])]
    )

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is False
    assert result.manifest_path is None  # nowhere to put it without a target folder


@pytest.mark.asyncio
async def test_run_workflow_writes_a_manifest_next_to_target_folder(monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(name="daily-report", workspace="use-ws", steps=[WorkflowStep(prompt="go")])

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="schedule")

    assert result.manifest_path is not None
    manifest_path = Path(result.manifest_path)
    assert manifest_path.parent == target
    assert manifest_path.name.startswith("run-daily-report-")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["workflow"] == "daily-report"
    assert manifest["ok"] is True
    assert manifest["origin"] == "schedule"
    assert manifest["conversation_id"] == result.conversation_id
    assert len(manifest["steps"]) == 1


@pytest.mark.asyncio
async def test_run_workflow_closes_session_even_when_a_step_fails(monkeypatch, aida_home: Path, records_home: Path):
    """Regression guard for the exact class of bug the external review
    flagged elsewhere (P1 findings, 59a4b92): a failure mid-run must not
    leave the session/MCP manager open."""
    closed = []

    class _TrackingProvider(MockProvider):
        async def aclose(self):
            closed.append(True)

    provider = _TrackingProvider([MockTurn(error="boom")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="use-ws", steps=[WorkflowStep(prompt="go")])

    result = await run_workflow(settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow")

    assert result.ok is False
    assert closed == [True]
