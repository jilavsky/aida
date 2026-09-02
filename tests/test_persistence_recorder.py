from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.persistence.recorder import ConversationNotFoundError, ConversationRecorder
from aida.persistence.store import ConversationStore
from aida.providers.base import Message


def _recorder(tmp_path: Path, **kwargs) -> ConversationRecorder:
    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    return ConversationRecorder(store, artifact_store, tmp_path / "records", **kwargs)


def test_new_recorder_creates_a_conversation(tmp_path: Path):
    rec = _recorder(tmp_path, workspace_name="use-pyirena", profile_name="argo-claude")
    summary = rec.store.get_conversation(rec.conversation_id)
    assert summary is not None
    assert summary.workspace_name == "use-pyirena"
    assert summary.profile_name == "argo-claude"


def test_new_recorder_defaults_origin_to_none(tmp_path: Path):
    rec = _recorder(tmp_path)
    assert rec.origin is None
    assert rec.store.get_conversation(rec.conversation_id).origin is None


def test_new_recorder_records_origin(tmp_path: Path):
    rec = _recorder(tmp_path, origin="workflow")
    assert rec.origin == "workflow"
    assert rec.store.get_conversation(rec.conversation_id).origin == "workflow"


def test_resume_reads_back_origin_from_existing_row(tmp_path: Path):
    db_path = tmp_path / "aida.db"
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    store1 = ConversationStore(db_path)
    rec1 = ConversationRecorder(store1, artifact_store, tmp_path / "records", origin="schedule")
    conv_id = rec1.conversation_id

    store2 = ConversationStore(db_path)
    rec2 = ConversationRecorder(store2, artifact_store, tmp_path / "records", conversation_id=conv_id, resume=True)

    assert rec2.origin == "schedule"


def test_record_message_derives_title_from_first_user_message(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_message(Message(role="user", content="Plot the SAXS data for sample S001"))
    assert rec.title == "Plot the SAXS data for sample S001"
    summary = rec.store.get_conversation(rec.conversation_id)
    assert summary.title == "Plot the SAXS data for sample S001"


def test_title_not_overwritten_by_later_messages(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_message(Message(role="user", content="first question"))
    rec.record_message(Message(role="assistant", content="an answer"))
    rec.record_message(Message(role="user", content="second question"))
    assert rec.title == "first question"


def test_long_title_truncated(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_message(Message(role="user", content="x" * 200))
    assert len(rec.title) <= 60


def test_record_message_persists_immediately(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_message(Message(role="user", content="hi"))
    loaded = rec.store.load_messages(rec.conversation_id)
    assert len(loaded) == 1
    assert loaded[0].content == "hi"


def test_record_message_writes_transcript_file(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_message(Message(role="user", content="hi"))
    assert rec._record_path is not None
    assert rec._record_path.exists()
    assert "hi" in rec._record_path.read_text(encoding="utf-8")


def test_export_transcript_reuses_same_path_across_calls(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_message(Message(role="user", content="first title"))
    first_path = rec.export_transcript()
    rec.title = "a totally different title"  # simulate a later title change
    second_path = rec.export_transcript()
    assert first_path == second_path


def test_record_artifact_persists(tmp_path: Path):
    rec = _recorder(tmp_path)
    art = rec.artifact_store.save_image(ImageArtifact(data=b"x", mime_type="image/png"))
    rec.record_artifact(art, call_id="call_1")
    loaded = rec.store.load_artifacts(rec.conversation_id)
    assert len(loaded) == 1
    assert loaded[0].call_id == "call_1"


def test_next_message_seq_reflects_persisted_messages(tmp_path: Path):
    """U6(b): ChatSession reads this before the tool-result message that
    will carry an artifact has itself been persisted (see aida.cli.chat's
    ImageArtifactCreated/FileArtifactCreated handling)."""
    rec = _recorder(tmp_path)
    assert rec.next_message_seq() == 0
    rec.record_message(Message(role="user", content="hi"))
    assert rec.next_message_seq() == 1


def test_record_artifact_fields_stores_message_seq(tmp_path: Path):
    rec = _recorder(tmp_path)
    rec.record_artifact_fields(
        artifact_id="art-1", kind="ImageArtifact", path="/tmp/x.png", mime_type="image/png", call_id="call_1",
        message_seq=2,
    )
    loaded = rec.store.load_artifacts(rec.conversation_id)
    assert loaded[0].seq == 2


def test_record_artifact_stores_message_seq(tmp_path: Path):
    rec = _recorder(tmp_path)
    art = rec.artifact_store.save_image(ImageArtifact(data=b"x", mime_type="image/png"))
    rec.record_artifact(art, call_id="call_1", message_seq=5)
    loaded = rec.store.load_artifacts(rec.conversation_id)
    assert loaded[0].seq == 5


def test_record_artifact_without_message_seq_defaults_to_none(tmp_path: Path):
    rec = _recorder(tmp_path)
    art = rec.artifact_store.save_image(ImageArtifact(data=b"x", mime_type="image/png"))
    rec.record_artifact(art, call_id="call_1")
    loaded = rec.store.load_artifacts(rec.conversation_id)
    assert loaded[0].seq is None


def test_resume_loads_existing_conversation(tmp_path: Path):
    db_path = tmp_path / "aida.db"
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    store1 = ConversationStore(db_path)
    rec1 = ConversationRecorder(
        store1, artifact_store, tmp_path / "records", workspace_name="use-pyirena", profile_name="argo-claude"
    )
    rec1.record_message(Message(role="user", content="hello"))
    rec1.record_message(Message(role="assistant", content="hi there"))
    conv_id = rec1.conversation_id

    store2 = ConversationStore(db_path)
    rec2 = ConversationRecorder(
        store2, artifact_store, tmp_path / "records", conversation_id=conv_id, resume=True
    )

    assert rec2.workspace_name == "use-pyirena"
    assert rec2.profile_name == "argo-claude"
    history = rec2.load_history()
    assert [m.content for m in history] == ["hello", "hi there"]


def test_resume_continues_appending_after_existing_history(tmp_path: Path):
    db_path = tmp_path / "aida.db"
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    store1 = ConversationStore(db_path)
    rec1 = ConversationRecorder(store1, artifact_store, tmp_path / "records")
    rec1.record_message(Message(role="user", content="before crash"))
    conv_id = rec1.conversation_id

    store2 = ConversationStore(db_path)
    rec2 = ConversationRecorder(store2, artifact_store, tmp_path / "records", conversation_id=conv_id, resume=True)
    rec2.record_message(Message(role="assistant", content="after resume"))

    history = rec2.load_history()
    assert [m.content for m in history] == ["before crash", "after resume"]


def test_resume_unknown_conversation_raises(tmp_path: Path):
    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    with pytest.raises(ConversationNotFoundError):
        ConversationRecorder(
            store, artifact_store, tmp_path / "records", conversation_id="nope", resume=True
        )


def test_resume_requires_conversation_id(tmp_path: Path):
    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    with pytest.raises(ValueError, match="resume=True requires"):
        ConversationRecorder(store, artifact_store, tmp_path / "records", resume=True)


# --- transcript rewrites are rate limited ----------------------------------
#
# Review finding: record_message called export_transcript() every single
# time, and that reloads all messages *and* all artifacts, re-copies every
# image into the sidecar folder (a full filecmp.cmp(shallow=False) byte
# comparison each), and rewrites the whole .md. A session with 40 plots and
# 200 messages meant ~8000 whole-file comparisons for a file nobody reads
# until the session ends.


def _recorder(tmp_path: Path, **kwargs) -> ConversationRecorder:
    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    return ConversationRecorder(store, artifact_store, tmp_path / "records", **kwargs)


def test_rapid_messages_do_not_each_rewrite_the_transcript(tmp_path: Path):
    rec = _recorder(tmp_path, transcript_min_interval_seconds=60.0)
    exports = []
    original = rec.export_transcript
    rec.export_transcript = lambda: (exports.append(1), original())[1]  # type: ignore[method-assign]

    for i in range(20):
        rec.record_message(Message(role="user", content=f"message {i}"))

    assert len(exports) == 1  # the first one only; the rest are deferred


def test_flush_transcript_settles_the_deferred_write(tmp_path: Path):
    rec = _recorder(tmp_path, transcript_min_interval_seconds=60.0)
    rec.record_message(Message(role="user", content="first"))
    rec.record_message(Message(role="assistant", content="deferred answer"))

    assert "deferred answer" not in rec._record_path.read_text(encoding="utf-8")
    rec.flush_transcript()
    assert "deferred answer" in rec._record_path.read_text(encoding="utf-8")


def test_flush_transcript_is_a_noop_when_nothing_changed(tmp_path: Path):
    rec = _recorder(tmp_path, transcript_min_interval_seconds=60.0)
    rec.record_message(Message(role="user", content="only message"))
    rec.flush_transcript()

    exports = []
    original = rec.export_transcript
    rec.export_transcript = lambda: (exports.append(1), original())[1]  # type: ignore[method-assign]
    rec.flush_transcript()

    assert exports == []


def test_the_db_write_is_still_immediate(tmp_path: Path):
    """The crash-safety property this module documents is unchanged — only
    the derived Markdown view lags."""
    rec = _recorder(tmp_path, transcript_min_interval_seconds=60.0)
    for i in range(5):
        rec.record_message(Message(role="user", content=f"m{i}"))

    assert [m.content for m in rec.load_history()] == ["m0", "m1", "m2", "m3", "m4"]
