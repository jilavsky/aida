"""List conversations by age/size and delete them completely.

The one hard requirement here (Phase 4 acceptance criterion): deleting a
conversation must leave **no orphans** — no leftover artifact files, no
leftover sidecar images, no leftover ``.md`` record, and no leftover DB
rows. Every deletion path in this module removes all four, in an order
that's safe to re-run if it's ever interrupted partway (DB rows are deleted
last, so a partial failure just means "some files were already gone" on
retry, never a DB row pointing at nothing).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from aida.persistence.records import sidecar_dir
from aida.persistence.store import ConversationStore, ConversationSummary


@dataclass
class DeletionResult:
    conversation_id: str
    deleted_message_rows: int
    deleted_artifact_rows: int
    deleted_artifact_files: list[str] = field(default_factory=list)
    deleted_sidecar_dir: bool = False
    deleted_record_file: bool = False


def list_conversations_by_age(store: ConversationStore, *, oldest_first: bool = True) -> list[ConversationSummary]:
    """``ConversationStore.list_conversations`` is already newest-first;
    this just makes "oldest first" (the natural order for a cleanup UI)
    explicit and named."""
    conversations = store.list_conversations()
    return list(reversed(conversations)) if oldest_first else conversations


def list_conversations_older_than(store: ConversationStore, cutoff_iso: str) -> list[ConversationSummary]:
    """Conversations whose ``updated_at`` is older than ``cutoff_iso``
    (an ISO-8601 timestamp, e.g. from ``datetime.now(UTC).isoformat()`` minus
    an age threshold) — the "optional auto-cleanup age threshold" building
    block. Comparison is a plain string comparison, which is
    chronologically correct for ISO-8601 timestamps in a consistent format."""
    return [c for c in store.list_conversations() if c.updated_at < cutoff_iso]


def delete_conversation(
    store: ConversationStore, conversation_id: str, *, records_dir: Path
) -> DeletionResult:
    """Delete a conversation's DB rows, its artifact files, its sidecar
    image folder, and its Markdown record — in that order, DB rows last, so
    a conversation is never left DB-referenced-but-files-gone or vice versa
    for longer than necessary. Deleting a conversation that has already had
    some of its files removed (e.g. a prior interrupted delete) is safe:
    missing files are simply skipped rather than raising."""
    conversation = store.get_conversation(conversation_id)
    artifacts = store.load_artifacts(conversation_id)

    deleted_files: list[str] = []
    for artifact in artifacts:
        if artifact.path:
            path = Path(artifact.path)
            if path.exists():
                path.unlink()
                deleted_files.append(str(path))

    deleted_sidecar = False
    deleted_record = False
    if conversation is not None:
        sidecar = sidecar_dir(records_dir, conversation.sidecar_dirname, conversation_id)
        if sidecar.exists():
            shutil.rmtree(sidecar)
            deleted_sidecar = True

        if conversation.record_path:
            record_path = Path(conversation.record_path)
            if record_path.exists():
                record_path.unlink()
                deleted_record = True

    message_count, artifact_count = store.delete_conversation(conversation_id)

    return DeletionResult(
        conversation_id=conversation_id,
        deleted_message_rows=message_count,
        deleted_artifact_rows=artifact_count,
        deleted_artifact_files=deleted_files,
        deleted_sidecar_dir=deleted_sidecar,
        deleted_record_file=deleted_record,
    )


__all__ = [
    "DeletionResult",
    "delete_conversation",
    "list_conversations_by_age",
    "list_conversations_older_than",
]
