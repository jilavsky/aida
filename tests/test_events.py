from __future__ import annotations

import json

from aida.core.events import (
    AgentError,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
    UsageInfo,
)

ALL_EVENTS = [
    TextStarted(message_id="m1"),
    TextDelta(message_id="m1", text="hi"),
    TextFinished(message_id="m1", text="hi"),
    ToolCallStarted(call_id="c1", tool_name="get_current_time", arguments={"tz": "utc"}),
    ToolCallFinished(call_id="c1", tool_name="get_current_time", result={"utc_iso": "now"}),
    ImageArtifactCreated(artifact_id="a1", call_id="c1", mime_type="image/png", path="/tmp/x.png"),
    FileArtifactCreated(artifact_id="a2", call_id="c1", path="/tmp/x.md"),
    MessageFinished(message_id="m1", stop_reason="stop"),
    UsageInfo(input_tokens=10, output_tokens=5, total_tokens=15),
    AgentError(layer="provider", message="boom", detail="details here"),
]


def test_every_event_type_json_serializable():
    for event in ALL_EVENTS:
        data = event.to_dict()
        # Round-trips through json.dumps without a custom encoder — the
        # PLAN.md §3 hard-rule-4 requirement ("plain, JSON-serializable").
        encoded = json.dumps(data)
        decoded = json.loads(encoded)
        assert decoded["type"] == type(event).__name__


def test_events_are_frozen():
    event = TextDelta(message_id="m1", text="hi")
    try:
        event.text = "changed"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised, "AgentEvent dataclasses should be immutable (frozen=True)"


def test_tool_call_finished_defaults_not_error():
    event = ToolCallFinished(call_id="c1", tool_name="x", result="ok")
    assert event.is_error is False


def test_agent_error_detail_optional():
    event = AgentError(layer="core", message="cancelled")
    assert event.detail is None
    assert event.to_dict()["detail"] is None
