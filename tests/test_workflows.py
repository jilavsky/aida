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
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    settings = _settings()
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="Do the thing.")]
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is True
    assert result.conversation_id is not None
    assert len(result.steps) == 1
    assert result.steps[0].ok is True
    assert result.steps[0].stop_reason == "stop"


@pytest.mark.asyncio
async def test_run_workflow_resolves_placeholders_from_workflow_vars(
    monkeypatch, aida_home: Path, records_home: Path
):
    provider = MockProvider([MockTurn(text="done")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings()
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        vars={"folder": "/data/default"},
        steps=[WorkflowStep(prompt="Reduce scans in {folder}.")],
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is True
    assert result.steps[0].prompt == "Reduce scans in /data/default."


@pytest.mark.asyncio
async def test_run_workflow_var_override_wins_over_workflow_default(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
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
async def test_run_workflow_missing_var_raises_before_any_session_work(
    aida_home: Path, records_home: Path
):
    settings = _settings()
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="Reduce {folder}.")]
    )

    with pytest.raises(WorkflowConfigError, match="folder"):
        await run_workflow(
            settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
        )


def test_render_step_prompt_missing_var_names_it():
    with pytest.raises(WorkflowConfigError, match="folder"):
        render_step_prompt(WorkflowStep(prompt="Reduce {folder}."), {})


def test_render_step_prompt_substitutes_present_var():
    assert (
        render_step_prompt(WorkflowStep(prompt="Reduce {folder}."), {"folder": "/x"})
        == "Reduce /x."
    )


@pytest.mark.asyncio
async def test_run_workflow_unknown_workspace_raises_config_error(
    aida_home: Path, records_home: Path
):
    settings = _settings()
    workflow = WorkflowConfig(
        name="w", workspace="does-not-exist", steps=[WorkflowStep(prompt="hi")]
    )

    with pytest.raises(WorkflowConfigError):
        await run_workflow(
            settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
        )


@pytest.mark.asyncio
async def test_run_workflow_no_workspace_configured_raises_config_error(
    aida_home: Path, records_home: Path
):
    settings = _settings()
    workflow = WorkflowConfig(name="w", workspace="", steps=[WorkflowStep(prompt="hi")])

    with pytest.raises(WorkflowConfigError, match="workspace"):
        await run_workflow(
            settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
        )


@pytest.mark.asyncio
async def test_run_workflow_stops_at_first_agent_error(
    monkeypatch, aida_home: Path, records_home: Path
):
    provider = MockProvider([MockTurn(error="boom"), MockTurn(text="should not be reached")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings()
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt="step 1"), WorkflowStep(prompt="step 2")],
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

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
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="what time is it?")]
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is True
    assert result.steps[0].tool_calls == [{"tool_name": "get_current_time", "is_error": False}]


@pytest.mark.asyncio
async def test_run_workflow_expect_files_satisfied(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    (target / "plot.png").write_bytes(b"fake")
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="plot it", expect_files=["*.png"])]
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is True
    assert result.steps[0].missing_expect_files == []


@pytest.mark.asyncio
async def test_run_workflow_expect_files_unmet_fails_the_step(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="plot it", expect_files=["*.png"])]
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert result.steps[0].missing_expect_files == ["*.png"]


@pytest.mark.asyncio
async def test_run_workflow_expect_files_with_no_target_folder_always_fails(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    settings = _settings(target_folder=None)
    workflow = WorkflowConfig(
        name="w", workspace="use-ws", steps=[WorkflowStep(prompt="plot it", expect_files=["*.png"])]
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert result.manifest_path is None  # nowhere to put it without a target folder


@pytest.mark.asyncio
async def test_run_workflow_writes_a_manifest_next_to_target_folder(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="daily-report", workspace="use-ws", steps=[WorkflowStep(prompt="go")]
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="schedule"
    )

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
async def test_run_workflow_closes_session_even_when_a_step_fails(
    monkeypatch, aida_home: Path, records_home: Path
):
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

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert closed == [True]


@pytest.mark.parametrize("prompt", ["Use {} here.", "Stray } brace.", "Stray { brace."])
def test_render_step_prompt_stray_braces_are_a_config_error(prompt):
    """``{}`` raises IndexError and a lone brace ValueError inside
    ``str.format``; both used to escape as unexpected errors and stop the
    scheduler (review B3)."""
    with pytest.raises(WorkflowConfigError, match=r"\{\{"):
        render_step_prompt(WorkflowStep(prompt=prompt), {})


# --- repeating steps (repeat_until) ---------------------------------------


def _marker_after(target: Path, passes: int, name: str = "scan.done"):
    """An ``on_event`` that creates ``target/name`` when the ``passes``-th
    turn finishes — standing in for the agent writing its finished marker."""
    from aida.core.events import MessageFinished

    finished = {"count": 0}

    def on_event(index, event):
        if isinstance(event, MessageFinished) and event.stop_reason == "stop":
            finished["count"] += 1
            if finished["count"] == passes:
                (target / name).write_text("done", encoding="utf-8")

    return on_event


def _step_requests(provider: MockProvider, prompt: str):
    """The message lists of provider calls made for ``prompt``'s turns
    (excluding any titling request)."""
    return [
        messages
        for messages, _tools, _settings in provider.calls
        if any(m.role == "user" and m.content == prompt for m in messages)
    ]


@pytest.mark.asyncio
async def test_repeating_step_runs_until_marker_with_a_clean_context_each_pass(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    provider = MockProvider([MockTurn(text=f"batch {n} done") for n in range(1, 10)])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=str(target))
    prompt = "Process the next batch from state.json."
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt=prompt, repeat_until="*.done", max_passes=10)],
    )

    result = await run_workflow(
        settings,
        workflow,
        confirm_callback=_confirm(yes_in_allowed=False),
        origin="workflow",
        on_event=_marker_after(target, 3),
    )

    assert result.ok is True, result.error
    assert result.steps[0].passes == 3
    requests = _step_requests(provider, prompt)
    assert len(requests) == 3
    for messages in requests:
        # Only the system prompt and this pass's own prompt: nothing from
        # earlier passes is sent.
        assert [m.role for m in messages if m.role != "system"] == ["user"]
        assert not any(" done" in str(m.content or "") for m in messages)


@pytest.mark.asyncio
async def test_repeating_step_stays_in_one_conversation_and_later_steps_run(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    from aida.persistence.store import ConversationStore

    target = tmp_path / "target"
    target.mkdir()
    provider = MockProvider([MockTurn(text="ok") for _ in range(10)])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[
            WorkflowStep(prompt="batch", repeat_until="scan.done", max_passes=5),
            WorkflowStep(prompt="write the report"),
        ],
    )

    result = await run_workflow(
        settings,
        workflow,
        confirm_callback=_confirm(yes_in_allowed=False),
        origin="workflow",
        on_event=_marker_after(target, 2),
    )

    assert result.ok is True, result.error
    assert [s.passes for s in result.steps] == [2, 1]
    with ConversationStore() as store:
        stored = store.load_messages(result.conversation_id)
    # Every pass is kept in the stored conversation, though the context
    # sent to the model was cleared between them.
    assert [m.content for m in stored if m.role == "user"] == [
        "batch",
        "batch",
        "write the report",
    ]


@pytest.mark.asyncio
async def test_repeating_step_fails_when_max_passes_reached(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    provider = MockProvider([MockTurn(text="ok") for _ in range(10)])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[
            WorkflowStep(prompt="batch", repeat_until="scan.done", max_passes=3),
            WorkflowStep(prompt="never reached"),
        ],
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert len(result.steps) == 1
    assert result.steps[0].passes == 3
    assert "after 3 pass" in result.error
    assert len(_step_requests(provider, "batch")) == 3


@pytest.mark.asyncio
async def test_repeating_step_stops_at_a_failed_pass(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    provider = MockProvider([MockTurn(text="ok"), MockTurn(error="boom"), MockTurn(text="ok")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=str(target))
    settings.app.auto_title_conversations = False  # keep the script one turn per pass
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt="batch", repeat_until="scan.done", max_passes=5)],
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert result.steps[0].passes == 2
    assert "pass 2" in result.error and "boom" in result.error


@pytest.mark.asyncio
async def test_repeating_step_refuses_a_leftover_marker(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    (target / "scan.done").write_text("from yesterday", encoding="utf-8")
    provider = MockProvider([MockTurn(text="ok")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt="batch", repeat_until="scan.done")],
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert result.steps[0].passes == 0
    assert "already matches" in result.error
    assert _step_requests(provider, "batch") == []


@pytest.mark.asyncio
async def test_repeating_step_needs_a_target_folder(
    monkeypatch, aida_home: Path, records_home: Path
):
    provider = MockProvider([MockTurn(text="ok")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=None)
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt="batch", repeat_until="scan.done")],
    )

    result = await run_workflow(
        settings, workflow, confirm_callback=_confirm(yes_in_allowed=False), origin="workflow"
    )

    assert result.ok is False
    assert "target folder" in result.error
    assert _step_requests(provider, "batch") == []


@pytest.mark.asyncio
async def test_repeating_step_passes_are_in_the_manifest(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    provider = MockProvider([MockTurn(text="ok") for _ in range(5)])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings(target_folder=str(target))
    workflow = WorkflowConfig(
        name="w",
        workspace="use-ws",
        steps=[WorkflowStep(prompt="batch", repeat_until="scan.done", max_passes=5)],
    )

    result = await run_workflow(
        settings,
        workflow,
        confirm_callback=_confirm(yes_in_allowed=False),
        origin="workflow",
        on_event=_marker_after(target, 2),
    )

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["steps"][0]["passes"] == 2
