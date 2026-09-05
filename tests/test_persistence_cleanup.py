from __future__ import annotations

from pathlib import Path

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.persistence.cleanup import (
    delete_conversation,
    list_conversations_by_age,
    list_conversations_older_than,
)
from aida.persistence.records import record_file_path, write_transcript
from aida.persistence.store import ConversationStore
from aida.providers.base import Message

T_OLD = "2026-01-01T00:00:00"
T_MID = "2026-06-01T00:00:00"
T_NEW = "2026-08-19T00:00:00"


def _store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "aida.db")


def test_list_conversations_by_age_oldest_first(tmp_path: Path):
    store = _store(tmp_path)
    newer = store.create_conversation(timestamp=T_NEW)
    older = store.create_conversation(timestamp=T_OLD)

    ordered = list_conversations_by_age(store)
    assert [c.id for c in ordered] == [older, newer]


def test_list_conversations_older_than_cutoff(tmp_path: Path):
    store = _store(tmp_path)
    old_conv = store.create_conversation(timestamp=T_OLD)
    store.create_conversation(timestamp=T_NEW)

    stale = list_conversations_older_than(store, T_MID)
    assert [c.id for c in stale] == [old_conv]


def test_delete_conversation_removes_db_rows(tmp_path: Path):
    store = _store(tmp_path)
    conv_id = store.create_conversation(timestamp=T_NEW)
    store.append_message(conv_id, Message(role="user", content="hi"), timestamp=T_NEW)

    result = delete_conversation(store, conv_id, records_dir=tmp_path / "records")

    assert result.deleted_message_rows == 1
    assert store.get_conversation(conv_id) is None


def test_delete_conversation_leaves_no_orphan_artifact_file(tmp_path: Path):
    store = _store(tmp_path)
    artifact_store = ArtifactStore(base_dir=tmp_path / "aida-artifacts")
    conv_id = store.create_conversation(timestamp=T_NEW)

    art = artifact_store.save_image(ImageArtifact(data=b"pngbytes", mime_type="image/png"))
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T_NEW)
    assert Path(art.path).exists()

    result = delete_conversation(
        store, conv_id, records_dir=tmp_path / "records", artifacts_dir=tmp_path / "aida-artifacts"
    )

    assert str(art.path) in result.deleted_artifact_files
    assert not Path(art.path).exists()


def test_delete_conversation_leaves_no_orphan_record_or_sidecar(tmp_path: Path):
    store = _store(tmp_path)
    artifact_store = ArtifactStore(base_dir=tmp_path / "aida-artifacts")
    records_dir = tmp_path / "records"
    conv_id = store.create_conversation(timestamp=T_NEW, sidecar_dirname="figures")

    art = artifact_store.save_image(ImageArtifact(data=b"pngbytes", mime_type="image/png"))
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T_NEW)
    store.append_message(
        conv_id,
        Message(role="tool", content="[image]", tool_call_id="call_1", name="get_image"),
        timestamp=T_NEW,
    )

    path = record_file_path(records_dir, conv_id, None)
    write_transcript(
        path=path,
        records_dir=records_dir,
        artifact_store=artifact_store,
        conversation_id=conv_id,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=store.load_messages(conv_id),
        artifacts=store.load_artifacts(conv_id),
    )
    store.set_record_path(conv_id, str(path), timestamp=T_NEW)

    sidecar_copy = records_dir / "figures" / conv_id[:8] / Path(art.path).name
    assert path.exists()
    assert sidecar_copy.exists()

    result = delete_conversation(
        store, conv_id, records_dir=records_dir, artifacts_dir=tmp_path / "aida-artifacts"
    )

    assert result.deleted_record_file is True
    assert result.deleted_sidecar_dir is True
    assert not path.exists()
    assert not sidecar_copy.exists()
    assert not (records_dir / "figures" / conv_id[:8]).exists()


def test_delete_conversation_does_not_affect_other_conversations_files(tmp_path: Path):
    store = _store(tmp_path)
    artifact_store = ArtifactStore(base_dir=tmp_path / "aida-artifacts")
    records_dir = tmp_path / "records"

    conv_a = store.create_conversation(timestamp=T_NEW)
    conv_b = store.create_conversation(timestamp=T_NEW)

    art_a = artifact_store.save_image(ImageArtifact(data=b"a", mime_type="image/png"))
    art_b = artifact_store.save_image(ImageArtifact(data=b"b", mime_type="image/png"))
    store.append_artifact_from_object(conv_a, art_a, call_id="call_a", timestamp=T_NEW)
    store.append_artifact_from_object(conv_b, art_b, call_id="call_b", timestamp=T_NEW)

    delete_conversation(
        store, conv_a, records_dir=records_dir, artifacts_dir=tmp_path / "aida-artifacts"
    )

    assert not Path(art_a.path).exists()
    assert Path(art_b.path).exists()
    assert store.get_conversation(conv_b) is not None


def test_delete_conversation_missing_files_does_not_raise(tmp_path: Path):
    store = _store(tmp_path)
    artifact_store = ArtifactStore(base_dir=tmp_path / "aida-artifacts")
    conv_id = store.create_conversation(timestamp=T_NEW)
    art = artifact_store.save_image(ImageArtifact(data=b"x", mime_type="image/png"))
    store.append_artifact_from_object(conv_id, art, call_id="call_1", timestamp=T_NEW)

    Path(art.path).unlink()  # simulate a prior partial cleanup

    result = delete_conversation(
        store, conv_id, records_dir=tmp_path / "records", artifacts_dir=tmp_path / "aida-artifacts"
    )
    assert result.deleted_artifact_files == []  # nothing to delete, no crash
    assert store.get_conversation(conv_id) is None


def test_delete_unknown_conversation_is_a_no_op(tmp_path: Path):
    store = _store(tmp_path)
    result = delete_conversation(store, "does-not-exist", records_dir=tmp_path / "records")
    assert result.deleted_message_rows == 0
    assert result.deleted_artifact_rows == 0
