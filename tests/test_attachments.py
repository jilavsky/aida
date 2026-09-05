"""The attachment store (planning/documents_implementation.md Phase B).

Two properties matter here, and they pull in opposite directions:

1. A document a person attached must survive the original being moved,
   renamed or cleaned up — otherwise a resumed conversation talks about a
   paper nobody can open again.
2. Deleting the conversation must delete every copy AIDA made. Someone who
   deletes a chat holding a manuscript under review must not find that
   manuscript still sitting in their home directory.

The second is the reason the first is bounded: only files a *person*
attached are copied, never one the agent opened with ``read_file``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.documents.attachments import assets_dir_for, store_attachment, store_attachments
from aida.persistence.cleanup import delete_conversation
from aida.persistence.db import CURRENT_SCHEMA_VERSION, connect
from aida.persistence.recorder import ConversationRecorder
from aida.persistence.records import attachments_dir
from aida.persistence.store import ConversationStore
from aida.providers.base import ImageRef, Message
from tests.mock_mcp_server import TINY_PNG_BYTES


def _recorder(tmp_path: Path) -> tuple[ConversationRecorder, ConversationStore, Path]:
    records = tmp_path / "records"
    store = ConversationStore(tmp_path / "aida.db")
    recorder = ConversationRecorder(store, ArtifactStore(tmp_path / "artifacts"), records)
    return recorder, store, records


def _paper(tmp_path: Path, name: str = "paper.pdf") -> Path:
    source = tmp_path / "Downloads" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-1.4 pretend")
    return source


# --- storing -------------------------------------------------------------


def test_store_attachment_copies_the_file_and_its_text(tmp_path: Path):
    source = _paper(tmp_path)
    stored = store_attachment(source, tmp_path / "att", text="Extracted body text")

    assert stored.error is None
    assert Path(stored.stored_path).read_bytes() == b"%PDF-1.4 pretend"
    assert Path(stored.text_path).read_text() == "Extracted body text"
    # The derived-files folder is named but not created — an attachment
    # nothing was derived from leaves no empty folder behind.
    assert not Path(stored.assets_dir).exists()
    assert assets_dir_for(Path(stored.stored_path)).name == "paper.assets"


def test_stored_copy_outlives_the_original(tmp_path: Path):
    """The whole point: a Downloads folder that gets cleaned must not take
    the conversation's copy of the paper with it."""
    source = _paper(tmp_path)
    stored = store_attachment(source, tmp_path / "att")
    source.unlink()

    assert not source.exists()
    assert Path(stored.stored_path).exists()


def test_a_second_attachment_of_the_same_name_does_not_clobber_the_first(tmp_path: Path):
    """Overwriting would silently replace a document earlier turns still
    refer to."""
    first = store_attachment(_paper(tmp_path), tmp_path / "att", text="first")
    second_source = tmp_path / "elsewhere" / "paper.pdf"
    second_source.parent.mkdir(parents=True)
    second_source.write_bytes(b"%PDF-1.4 different")
    second = store_attachment(second_source, tmp_path / "att", text="second")

    assert first.stored_path != second.stored_path
    assert Path(first.stored_path).read_bytes() == b"%PDF-1.4 pretend"
    assert Path(second.stored_path).read_bytes() == b"%PDF-1.4 different"
    assert Path(first.text_path).read_text() == "first"
    assert Path(second.text_path).read_text() == "second"


def test_an_unreadable_source_is_reported_not_raised(tmp_path: Path):
    """A failed bookkeeping copy must never cost the user the turn they
    just sent — the content is already in the message."""
    result = store_attachments([tmp_path / "gone.pdf"], tmp_path / "att")
    assert len(result.failures) == 1
    assert result.failures[0].error


# --- the recorder side ---------------------------------------------------


def test_keep_attachments_records_where_the_folder_actually_is(tmp_path: Path):
    recorder, store, records = _recorder(tmp_path)
    result = recorder.keep_attachments([str(_paper(tmp_path))])

    expected = attachments_dir(records, recorder.conversation_id)
    assert Path(result.directory) == expected
    # Recorded on the row, not left to be recomputed later.
    assert store.get_conversation(recorder.conversation_id).attachments_path == str(expected)


def test_no_attachments_means_no_folder_and_no_recorded_path(tmp_path: Path):
    """An ordinary conversation must not sprout empty folders."""
    recorder, store, records = _recorder(tmp_path)
    recorder.record_message(Message(role="user", content="just talking"))

    assert not (records / "attachments").exists()
    assert store.get_conversation(recorder.conversation_id).attachments_path is None


def test_resumed_recorder_reuses_the_recorded_attachments_path(tmp_path: Path):
    recorder, store, records = _recorder(tmp_path)
    recorder.keep_attachments([str(_paper(tmp_path))])
    conv_id = recorder.conversation_id

    resumed = ConversationRecorder(
        store, ArtifactStore(tmp_path / "artifacts"), records, conversation_id=conv_id, resume=True
    )
    assert resumed.attachments_dir() == attachments_dir(records, conv_id)


def test_transcript_links_the_attachments(tmp_path: Path):
    recorder, _store, _records = _recorder(tmp_path)
    recorder.keep_attachments([str(_paper(tmp_path))], texts={str(_paper(tmp_path)): "body"})
    recorder.record_message(Message(role="user", content="what does this say?"))

    transcript = recorder.export_transcript().read_text()
    assert "**attachment:** [paper.pdf](attachments/" in transcript
    assert recorder.conversation_id[:8] in transcript


# --- deletion: the hard requirement --------------------------------------


def test_deleting_a_conversation_deletes_its_attachments(tmp_path: Path):
    recorder, store, records = _recorder(tmp_path)
    source = _paper(tmp_path, "manuscript.pdf")
    recorder.keep_attachments([str(source)])
    recorder.record_message(Message(role="user", content="review this"))
    folder = attachments_dir(records, recorder.conversation_id)
    assert (folder / "manuscript.pdf").exists()

    result = delete_conversation(
        store, recorder.conversation_id, records_dir=records, artifacts_dir=tmp_path / "artifacts"
    )

    assert result.deleted_attachments_dir is True
    assert not folder.exists()
    # ...and the person's own file is untouched.
    assert source.exists()


def test_deletion_still_finds_attachments_after_the_records_dir_setting_changed(tmp_path: Path):
    """The regression this migration exists for. Deletion used to recompute
    the folder from the *current* records dir, so changing the Records
    folder in Settings orphaned everything written under the old one —
    clutter for figures, a broken promise for attached documents."""
    recorder, store, old_records = _recorder(tmp_path)
    recorder.keep_attachments([str(_paper(tmp_path, "confidential.pdf"))])
    folder = attachments_dir(old_records, recorder.conversation_id)
    assert (folder / "confidential.pdf").exists()

    # The user later points Settings at a different Records folder.
    new_records = tmp_path / "records-elsewhere"
    new_records.mkdir()
    result = delete_conversation(
        store,
        recorder.conversation_id,
        records_dir=new_records,
        artifacts_dir=tmp_path / "artifacts",
    )

    assert result.deleted_attachments_dir is True
    assert not folder.exists(), "the documents must go even though the setting moved"
    # The old records folder itself is left alone — only our own
    # per-conversation subfolder was removed.
    assert old_records.exists()


def test_deletion_refuses_a_recorded_path_outside_the_records_dir(tmp_path: Path):
    """A recorded path is data from a row that could be corrupt or
    hand-edited, and this is an rmtree: containment is the difference
    between a cleanup and an incident."""
    recorder, store, records = _recorder(tmp_path)
    recorder.record_message(Message(role="user", content="hi"))
    precious = tmp_path / "not-ours"
    precious.mkdir()
    (precious / "keep-me.txt").write_text("important")
    store.set_attachments_path(recorder.conversation_id, str(precious), timestamp="2026-01-01")

    result = delete_conversation(
        store, recorder.conversation_id, records_dir=records, artifacts_dir=tmp_path / "artifacts"
    )

    assert result.deleted_attachments_dir is False
    assert (precious / "keep-me.txt").exists()


def test_attached_images_are_still_copied_and_still_deleted(tmp_path: Path):
    """Pre-existing behaviour that must not regress: an attached image is
    adopted into the artifact store, survives its original being deleted,
    and goes away with the conversation."""
    recorder, store, records = _recorder(tmp_path)
    source = tmp_path / "Downloads" / "shot.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(TINY_PNG_BYTES)

    recorder.record_message(
        Message(role="user", content="look", images=[ImageRef(path=str(source))])
    )
    stored = Path(store.load_messages(recorder.conversation_id)[0].images[0].path)
    source.unlink()
    assert stored.exists() and stored != source

    delete_conversation(
        store, recorder.conversation_id, records_dir=records, artifacts_dir=tmp_path / "artifacts"
    )
    assert not stored.exists()


# --- schema --------------------------------------------------------------


def test_migration_5_adds_the_path_columns_to_an_existing_v4_database(tmp_path: Path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, title TEXT, workspace_name TEXT, profile_name TEXT,
            sidecar_dirname TEXT NOT NULL DEFAULT 'figures', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, record_path TEXT, origin TEXT, "user" TEXT
        );
        INSERT INTO conversations (id, title, created_at, updated_at)
        VALUES ('old1', 'Before attachments', '2026-01-01', '2026-01-01');
        PRAGMA user_version = 4;
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        row = conn.execute(
            "SELECT title, attachments_path, sidecar_path FROM conversations WHERE id = ?",
            ("old1",),
        ).fetchone()
        assert row["title"] == "Before attachments"
        assert row["attachments_path"] is None
        assert row["sidecar_path"] is None
    finally:
        conn.close()


def test_deletion_refuses_a_recorded_path_that_is_neither_ours_nor_contained(tmp_path: Path):
    """Belt and braces on the structural gate: a path with the right shape
    but the wrong conversation id must not be removed either, or one
    conversation's delete could take another's documents."""
    recorder, store, records = _recorder(tmp_path)
    recorder.record_message(Message(role="user", content="hi"))
    someone_else = tmp_path / "elsewhere" / "attachments" / "deadbeef"
    someone_else.mkdir(parents=True)
    (someone_else / "theirs.pdf").write_bytes(b"%PDF")
    store.set_attachments_path(recorder.conversation_id, str(someone_else), timestamp="2026-01-01")

    result = delete_conversation(
        store, recorder.conversation_id, records_dir=records, artifacts_dir=tmp_path / "artifacts"
    )

    assert result.deleted_attachments_dir is False
    assert (someone_else / "theirs.pdf").exists()


def test_cleanup_older_than_inherits_the_attachment_deletion(tmp_path: Path):
    """The bulk path must not be a hole in the guarantee — it is the one
    most likely to be used on a chat someone has forgotten the contents
    of."""
    from aida.persistence.cleanup import list_conversations_older_than

    recorder, store, records = _recorder(tmp_path)
    recorder.keep_attachments([str(_paper(tmp_path, "old-manuscript.pdf"))])
    recorder.record_message(Message(role="user", content="review this"))
    folder = attachments_dir(records, recorder.conversation_id)
    assert folder.exists()

    stale = list_conversations_older_than(store, "2099-01-01")
    assert [s.id for s in stale] == [recorder.conversation_id]
    for summary in stale:
        delete_conversation(
            store, summary.id, records_dir=records, artifacts_dir=tmp_path / "artifacts"
        )

    assert not folder.exists()


# --- the orphan backstop -------------------------------------------------


def test_orphan_sweep_finds_a_folder_whose_conversation_is_gone(tmp_path: Path):
    """Every orphan is a copy of a document somebody believed they had
    deleted — an interrupted delete, or a records folder that moved before
    the paths were recorded."""
    from aida.persistence.cleanup import delete_orphan_attachment_dirs, find_orphan_attachment_dirs

    recorder, store, records = _recorder(tmp_path)
    recorder.keep_attachments([str(_paper(tmp_path))])
    recorder.record_message(Message(role="user", content="hi"))
    live_folder = attachments_dir(records, recorder.conversation_id)

    orphan = records / "attachments" / "deadbeef"
    orphan.mkdir(parents=True)
    (orphan / "left-behind.pdf").write_bytes(b"%PDF")

    assert find_orphan_attachment_dirs(store, records_dir=records) == [orphan]
    assert delete_orphan_attachment_dirs(store, records_dir=records) == [orphan]
    assert not orphan.exists()
    # The live conversation's folder is untouched.
    assert live_folder.exists()


def test_orphan_sweep_is_a_no_op_with_no_attachments_folder(tmp_path: Path):
    from aida.persistence.cleanup import find_orphan_attachment_dirs

    _recorder_obj, store, records = _recorder(tmp_path)
    assert find_orphan_attachment_dirs(store, records_dir=records) == []


def test_transcript_lists_the_document_not_its_extracted_text(tmp_path: Path):
    """`paper.pdf.md` holds the extracted text of `paper.pdf` — it is not a
    second attachment, and listing it as one makes the record look like the
    user handed over two files."""
    recorder, _store, _records = _recorder(tmp_path)
    source = _paper(tmp_path)
    recorder.keep_attachments([str(source)], texts={str(source): "body"})
    recorder.record_message(Message(role="user", content="read it"))

    transcript = recorder.export_transcript().read_text()
    assert transcript.count("**attachment:**") == 1
    assert "[paper.pdf]" in transcript
    assert "paper.pdf.md]" not in transcript


def test_a_genuine_markdown_attachment_is_still_listed(tmp_path: Path):
    """The filter keys on the companion actually being present, so an .md
    file the user really did attach is not swallowed by it."""
    recorder, _store, _records = _recorder(tmp_path)
    notes = tmp_path / "Downloads" / "notes.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("# My notes")
    recorder.keep_attachments([str(notes)])
    recorder.record_message(Message(role="user", content="summarise"))

    assert "[notes.md]" in recorder.export_transcript().read_text()


def test_the_extracted_text_is_written_beside_the_document(tmp_path: Path):
    """The folder must show what the model actually received, not only the
    file it came from. The text was being dropped: `keep_attachments` was
    called without it, so only the PDF ever landed there."""
    recorder, _store, records = _recorder(tmp_path)
    source = _paper(tmp_path)
    recorder.keep_attachments([str(source)], texts={str(source): "Extracted body text"})

    folder = attachments_dir(records, recorder.conversation_id)
    assert (folder / "paper.pdf").exists()
    assert (folder / "paper.pdf.md").read_text() == "Extracted body text"
