from __future__ import annotations

from pathlib import Path

from aida.artifacts.base import ImageArtifact, TextArtifact
from aida.persistence.store import ConversationStore, ScheduleRunStore
from aida.providers.base import Message, ToolCall

T0 = "2026-08-19T00:00:00"
T1 = "2026-08-19T00:00:01"


def _store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "aida.db")


def test_create_conversation_returns_id_and_is_retrievable(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0, workspace_name="use-pyirena", profile_name="argo-claude")

    summary = store.get_conversation(conv_id)
    assert summary is not None
    assert summary.id == conv_id
    assert summary.workspace_name == "use-pyirena"
    assert summary.profile_name == "argo-claude"
    assert summary.message_count == 0


def test_create_conversation_defaults_origin_to_none(tmp_path: Path):
    """Every existing interactive-chat caller omits ``origin`` — must keep
    behaving exactly as before migration 3 added the column."""
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    assert store.get_conversation(conv_id).origin is None


def test_create_conversation_records_origin(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0, origin="workflow")
    summary = store.get_conversation(conv_id)
    assert summary.origin == "workflow"
    # list_conversations goes through a different query (a join for
    # message_count) — must not silently drop the column either.
    assert store.list_conversations()[0].origin == "workflow"


def test_get_conversation_missing_returns_none(tmp_path: Path):
    store = _store(tmp_path)
    assert store.get_conversation("does-not-exist") is None


def test_append_message_assigns_increasing_seq(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)

    seq0 = store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T0)
    seq1 = store.append_message(conv_id, Message(role="assistant", content="hello"), timestamp=T1)

    assert seq0 == 0
    assert seq1 == 1


def test_append_message_updates_conversation_updated_at(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T1)
    summary = store.get_conversation(conv_id)
    assert summary.updated_at == T1


def test_load_messages_round_trips_plain_messages(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T0)
    store.append_message(conv_id, Message(role="assistant", content="hello there"), timestamp=T1)

    loaded = store.load_messages(conv_id)

    assert [m.role for m in loaded] == ["user", "assistant"]
    assert [m.content for m in loaded] == ["hi", "hello there"]


def test_load_messages_round_trips_tool_calls(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    original = Message(
        role="assistant",
        content="checking",
        tool_calls=[ToolCall(id="call_1", name="get_current_time", arguments={"tz": "utc"})],
    )
    store.append_message(conv_id, original, timestamp=T0)

    loaded = store.load_messages(conv_id)

    assert len(loaded) == 1
    assert len(loaded[0].tool_calls) == 1
    tc = loaded[0].tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "get_current_time"
    assert tc.arguments == {"tz": "utc"}


def test_load_messages_round_trips_tool_result_message(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    original = Message(role="tool", content="the time is now", tool_call_id="call_1", name="get_current_time")
    store.append_message(conv_id, original, timestamp=T0)

    loaded = store.load_messages(conv_id)

    assert loaded[0].role == "tool"
    assert loaded[0].tool_call_id == "call_1"
    assert loaded[0].name == "get_current_time"


def test_load_messages_preserves_order(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    for i in range(5):
        store.append_message(conv_id, Message(role="user", content=f"msg-{i}"), timestamp=T0)

    loaded = store.load_messages(conv_id)
    assert [m.content for m in loaded] == [f"msg-{i}" for i in range(5)]


def test_list_conversations_orders_by_updated_at_desc(tmp_path: Path):
    store = _store(tmp_path)
    older = store.create_conversation(timestamp="2026-08-01T00:00:00")
    newer = store.create_conversation(timestamp="2026-08-15T00:00:00")

    summaries = store.list_conversations()
    assert [s.id for s in summaries] == [newer, older]


def test_set_title_and_record_path(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    store.set_title(conv_id, "My analysis", timestamp=T1)
    store.set_record_path(conv_id, "/tmp/transcript.md", timestamp=T1)

    summary = store.get_conversation(conv_id)
    assert summary.title == "My analysis"
    assert summary.record_path == "/tmp/transcript.md"


def test_append_and_load_artifacts(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    art = ImageArtifact(data=b"pngbytes", mime_type="image/png", path="/tmp/x.png")
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T0)

    loaded = store.load_artifacts(conv_id)
    assert len(loaded) == 1
    assert loaded[0].id == art.id
    assert loaded[0].kind == "ImageArtifact"
    assert loaded[0].path == "/tmp/x.png"
    assert loaded[0].mime_type == "image/png"
    assert loaded[0].call_id == "call_1"


def test_text_artifact_has_no_path(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    art = TextArtifact(text="hello")
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T0)

    loaded = store.load_artifacts(conv_id)
    assert loaded[0].path is None
    assert loaded[0].kind == "TextArtifact"


def test_delete_conversation_removes_messages_and_artifacts(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T0)
    store.append_artifact_from_object(conv_id, ImageArtifact(data=b"x", mime_type="image/png"), call_id="c1", timestamp=T0)

    msg_count, artifact_count = store.delete_conversation(conv_id)

    assert msg_count == 1
    assert artifact_count == 1
    assert store.get_conversation(conv_id) is None
    assert store.load_messages(conv_id) == []
    assert store.load_artifacts(conv_id) == []


def test_next_seq_reflects_already_persisted_messages(tmp_path: Path):
    """U6(b): ConversationStore.next_seq is the public wrapper an artifact
    event handler uses to learn a not-yet-persisted message's future seq —
    same value append_message would assign next."""
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    assert store.next_seq(conv_id) == 0

    store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T0)
    assert store.next_seq(conv_id) == 1

    store.append_message(conv_id, Message(role="assistant", content="hello"), timestamp=T1)
    assert store.next_seq(conv_id) == 2


def test_load_messages_with_seq_pairs_each_message_with_its_seq(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T0)
    store.append_message(conv_id, Message(role="assistant", content="hello"), timestamp=T1)

    rows = store.load_messages_with_seq(conv_id)

    assert [seq for seq, _ in rows] == [0, 1]
    assert [m.content for _, m in rows] == ["hi", "hello"]


def test_append_artifact_seq_round_trips(tmp_path: Path):
    """U6(b): the seq an artifact is recorded with (the tool-result message
    it belongs to) survives the round trip so the GUI resume path can
    interleave it back at that position."""
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    art = ImageArtifact(data=b"pngbytes", mime_type="image/png", path="/tmp/x.png")
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T0, seq=3)

    loaded = store.load_artifacts(conv_id)
    assert loaded[0].seq == 3


def test_append_artifact_seq_defaults_to_none(tmp_path: Path):
    """A caller that doesn't know the owning message's seq yet (or an old
    pre-U6(b) row) must not crash and must round-trip as None rather than
    some sentinel — the GUI resume path treats None as "append at the end",
    same as v1's behavior."""
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T0)
    art = ImageArtifact(data=b"pngbytes", mime_type="image/png", path="/tmp/x.png")
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T0)

    loaded = store.load_artifacts(conv_id)
    assert loaded[0].seq is None


def test_delete_conversation_does_not_touch_other_conversations(tmp_path: Path):
    store = _store(tmp_path)
    conv_a = store.create_conversation(timestamp=T0)
    conv_b = store.create_conversation(timestamp=T0)
    store.append_message(conv_a, Message(role="user", content="a"), timestamp=T0)
    store.append_message(conv_b, Message(role="user", content="b"), timestamp=T0)

    store.delete_conversation(conv_a)

    assert store.get_conversation(conv_a) is None
    assert store.get_conversation(conv_b) is not None
    assert [m.content for m in store.load_messages(conv_b)] == ["b"]


def _schedule_store(tmp_path: Path) -> ScheduleRunStore:
    return ScheduleRunStore(tmp_path / "aida.db")


def test_schedule_run_store_last_run_reflects_most_recent(tmp_path: Path):
    store = _schedule_store(tmp_path)
    store.record_run(schedule_name="nightly", fired_at=T0, status="ok")
    store.record_run(schedule_name="nightly", fired_at=T1, status="failed", error="boom")

    last = store.last_run("nightly")
    assert last is not None
    assert last.fired_at == T1
    assert last.status == "failed"
    assert last.error == "boom"


def test_schedule_run_store_last_run_missing_schedule_returns_none(tmp_path: Path):
    store = _schedule_store(tmp_path)
    assert store.last_run("does-not-exist") is None


def test_schedule_run_store_recent_runs_only_this_schedule(tmp_path: Path):
    store = _schedule_store(tmp_path)
    store.record_run(schedule_name="nightly", fired_at=T0, status="ok")
    store.record_run(schedule_name="other", fired_at=T0, status="ok")

    runs = store.recent_runs("nightly")
    assert [r.schedule_name for r in runs] == ["nightly"]


def test_schedule_run_store_recent_failures_excludes_ok(tmp_path: Path):
    store = _schedule_store(tmp_path)
    store.record_run(schedule_name="nightly", fired_at=T0, status="ok")
    store.record_run(schedule_name="nightly", fired_at=T1, status="failed", error="boom")

    failures = store.recent_failures()
    assert [f.status for f in failures] == ["failed"]


def test_schedule_run_store_conversation_id_round_trips(tmp_path: Path):
    """``schedule_runs.conversation_id`` has a real foreign key into
    ``conversations`` (both tables live in the same DB file), so the
    referenced conversation must actually exist first."""
    conv_store = _store(tmp_path)
    conv_id = conv_store.create_conversation(timestamp=T0, origin="schedule")

    store = _schedule_store(tmp_path)
    store.record_run(schedule_name="nightly", fired_at=T0, status="ok", conversation_id=conv_id)
    assert store.last_run("nightly").conversation_id == conv_id
