"""List conversations by age/size and delete them completely.

The one hard requirement here (Phase 4 acceptance criterion): deleting a
conversation must leave **no orphans** — no leftover artifact files, no
leftover sidecar images, no leftover ``.md`` record, and no leftover DB
rows. Every deletion path in this module removes all four, in an order
that's safe to re-run if it's ever interrupted partway (DB rows are deleted
last, so a partial failure just means "some files were already gone" on
retry, never a DB row pointing at nothing).

**"No orphans" means no orphans of files AIDA itself created** — a bound
this module now enforces explicitly, because not every path in the
``artifacts`` table belongs to AIDA. An ``ImageArtifact`` can point at the
*user's own* file rather than at a copy in AIDA's store: ``read_file`` on a
``.png`` yields an ``ImageArtifact`` whose ``path`` is the source image in
the user's folder (``aida.documents.readers._read_image_file`` deliberately
doesn't load or copy the bytes), and ``write_file`` /
``write_markdown_report`` yield ``FileArtifact``s pointing at the report
just written into the user's *target* folder. Both are recorded on the live
path (``aida.cli.chat.ChatSession.send`` records every
``ImageArtifactCreated``/``FileArtifactCreated`` event), so a naive
"unlink every recorded artifact path" would hard-delete instrument data and
finished reports out of the user's own directories — in bulk, and with no
``_trash`` fallback, from the GUI's "delete conversations older than N
days" button. Deletion is therefore scoped to ``artifacts_dir`` (AIDA's own
``~/.aida/artifacts/``) plus the conversation's sidecar folder and Markdown
record under ``records_dir``; anything outside those is left untouched and
reported in ``skipped_external_files``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from aida.config.paths import artifacts_dir as default_artifacts_dir
from aida.persistence.records import ATTACHMENTS_DIRNAME, attachments_dir, sidecar_dir
from aida.persistence.store import ConversationStore, ConversationSummary


def _is_own_conversation_dir(candidate: Path, *, parent_name: str, conversation_id: str) -> bool:
    """Whether ``candidate`` has the shape of a folder this application
    wrote for this conversation: ``<anything>/<parent_name>/<conv8>``.

    Needed because the two checks that guard an ``rmtree`` here pull
    against each other. Containment in the *current* records dir is the
    obvious guard, but it fails exactly when the recorded path matters
    most: the user changed their Records folder after the files were
    written, which is the whole reason the path is recorded rather than
    recomputed. Structure is the check that survives that move — a folder
    named for this conversation, directly under a folder named
    ``figures``/``attachments``, is ours wherever it now sits, while a
    corrupted or hand-edited row pointing at ``/`` or a home directory
    matches neither test and is refused.
    """
    return candidate.parent.name == parent_name and candidate.name == conversation_id[:8]


def _is_inside(candidate: Path, root: Path) -> bool:
    """Containment check on resolved paths, so neither a symlink nor a
    ``..`` segment can make a user-owned file look like an AIDA-owned one.
    ``strict=False`` because a recorded path may no longer exist."""
    try:
        resolved = candidate.expanduser().resolve(strict=False)
        resolved_root = root.expanduser().resolve(strict=False)
    except OSError:
        return False
    return resolved == resolved_root or resolved_root in resolved.parents


@dataclass
class DeletionResult:
    conversation_id: str
    deleted_message_rows: int
    deleted_artifact_rows: int
    deleted_artifact_files: list[str] = field(default_factory=list)
    deleted_sidecar_dir: bool = False
    deleted_record_file: bool = False
    #: Whether this conversation's attachments folder — the copies of
    #: documents a person fed into it — was removed. Surfaced rather than
    #: silent because it is the visible half of a promise: deleting a chat
    #: deletes its documents, so someone who deletes a conversation holding
    #: a manuscript under review does not find it still on disk.
    deleted_attachments_dir: bool = False
    #: Recorded artifact paths that live outside AIDA's own storage — the
    #: user's source files and the reports written into their target
    #: folder. Left on disk on purpose (see the module docstring); surfaced
    #: so a CLI/GUI can say "kept N of your own files" rather than staying
    #: silent about the distinction.
    skipped_external_files: list[str] = field(default_factory=list)


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
    store: ConversationStore,
    conversation_id: str,
    *,
    records_dir: Path,
    artifacts_dir: Path | None = None,
) -> DeletionResult:
    """Delete a conversation's DB rows, its AIDA-owned artifact files, its
    sidecar image folder, and its Markdown record — in that order, DB rows
    last, so a conversation is never left DB-referenced-but-files-gone or
    vice versa for longer than necessary. Deleting a conversation that has
    already had some of its files removed (e.g. a prior interrupted delete)
    is safe: missing files are simply skipped rather than raising.

    ``artifacts_dir`` is the boundary of what this function may unlink,
    defaulting to AIDA's own ``~/.aida/artifacts/``. Recorded paths outside
    it are the user's own files, never deleted — see the module docstring
    for why the ``artifacts`` table contains both kinds.
    """
    owned_root = artifacts_dir if artifacts_dir is not None else default_artifacts_dir()
    conversation = store.get_conversation(conversation_id)
    artifacts = store.load_artifacts(conversation_id)

    deleted_files: list[str] = []
    skipped_files: list[str] = []
    for artifact in artifacts:
        if not artifact.path:
            continue
        path = Path(artifact.path)
        if not _is_inside(path, owned_root):
            skipped_files.append(str(path))
            continue
        if path.exists():
            path.unlink()
            deleted_files.append(str(path))

    deleted_sidecar = False
    deleted_attachments = False
    deleted_record = False
    if conversation is not None:
        # Recorded path first, computed path as the fallback for rows that
        # predate migration 5. Recomputing alone is what left folders
        # orphaned whenever the Records folder setting changed after the
        # files were written.
        sidecar = (
            Path(conversation.sidecar_path)
            if conversation.sidecar_path
            else sidecar_dir(records_dir, conversation.sidecar_dirname, conversation_id)
        )
        sidecar_ok = _is_inside(sidecar, records_dir) or _is_own_conversation_dir(
            sidecar, parent_name=conversation.sidecar_dirname, conversation_id=conversation_id
        )
        if sidecar_ok and sidecar.exists():
            shutil.rmtree(sidecar)
            deleted_sidecar = True

        attachments = (
            Path(conversation.attachments_path)
            if conversation.attachments_path
            else attachments_dir(records_dir, conversation_id)
        )
        # The gate is not optional. A recorded path is data from a row that
        # could have been hand-edited or corrupted, and this is an rmtree:
        # refusing anything that is neither inside the current records dir
        # nor shaped like one of our own conversation folders is the
        # difference between a cleanup and an incident.
        attachments_ok = _is_inside(attachments, records_dir) or _is_own_conversation_dir(
            attachments, parent_name=ATTACHMENTS_DIRNAME, conversation_id=conversation_id
        )
        if attachments_ok and attachments.exists():
            shutil.rmtree(attachments)
            deleted_attachments = True

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
        deleted_attachments_dir=deleted_attachments,
        deleted_record_file=deleted_record,
        skipped_external_files=skipped_files,
    )


def find_orphan_attachment_dirs(store: ConversationStore, *, records_dir: Path) -> list[Path]:
    """Attachment folders under ``records_dir`` with no conversation row.

    The backstop for the promise that deleting a chat deletes its
    documents. Orphans are not hypothetical: an interrupted delete, a
    hand-removed database, or a records folder that moved before the paths
    were recorded all leave one — and every orphan is a copy of a document
    somebody believed they had deleted. Read-only; callers decide what to
    do with the list (``aida doctor`` reports, ``aida conversations gc``
    removes).
    """
    root = records_dir / ATTACHMENTS_DIRNAME
    if not root.is_dir():
        return []
    live = {c.id[:8] for c in store.list_conversations()}
    return sorted(child for child in root.iterdir() if child.is_dir() and child.name not in live)


def delete_orphan_attachment_dirs(store: ConversationStore, *, records_dir: Path) -> list[Path]:
    """Remove what ``find_orphan_attachment_dirs`` found. Each is re-checked
    for our own shape immediately before the ``rmtree`` rather than trusted
    from the listing, so a folder that appeared in between cannot be caught
    by a stale path."""
    removed: list[Path] = []
    for orphan in find_orphan_attachment_dirs(store, records_dir=records_dir):
        if orphan.parent.name != ATTACHMENTS_DIRNAME or not _is_inside(orphan, records_dir):
            continue
        shutil.rmtree(orphan)
        removed.append(orphan)
    return removed


__all__ = [
    "DeletionResult",
    "delete_conversation",
    "delete_orphan_attachment_dirs",
    "find_orphan_attachment_dirs",
    "list_conversations_by_age",
    "list_conversations_older_than",
]
