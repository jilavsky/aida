"""High-level conversation persistence: create/append/load/list/delete.

Wraps ``aida.persistence.db``'s raw SQLite connection with the shapes the
rest of AIDA actually works with (``aida.providers.base.Message``,
``aida.artifacts.base.Artifact``) so nothing above this module writes SQL.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from aida.artifacts.base import Artifact, FileArtifact, ImageArtifact
from aida.persistence.db import connect
from aida.providers.base import ImageRef, Message, ToolCall

#: The ``artifacts.kind`` value used for an image the *user* attached to a
#: message, as opposed to one a tool produced. Distinct so the resume path
#: can rebuild ``Message.images`` from these rows alone, and so the GUI's
#: artifact rendering does not show a user's own attachment a second time as
#: though a tool had returned it.
USER_IMAGE_KIND = "UserImage"


def new_conversation_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ConversationSummary:
    """One row of ``conversations``, plus a cheap message count — enough
    for ``aida conversations list`` without loading every message."""

    id: str
    title: str | None
    workspace_name: str | None
    profile_name: str | None
    sidecar_dirname: str
    created_at: str
    updated_at: str
    record_path: str | None
    message_count: int
    #: Phase 10: ``None`` for every interactive chat conversation (the
    #: overwhelming majority, and everything created before this field
    #: existed); ``"workflow"`` or ``"schedule"`` for one created by
    #: ``aida.core.workflows.run_workflow`` — see ``aida.persistence.db``
    #: migration 3.
    origin: str | None = None
    #: The organization label this conversation belongs to (migration 4) —
    #: a person on a shared beamline machine, a project on a laptop. NULL
    #: for everything created before the column existed and for every
    #: install that never sets one. Not an identity and not a permission:
    #: see ``aida.config.users``.
    user: str | None = None
    #: Where this conversation's attachments folder and sidecar folder
    #: actually are (migration 5). NULL for conversations created before
    #: the columns existed, and for any that never attached anything.
    #: Recorded rather than recomputed so a later change to the Records
    #: folder setting cannot orphan files that must be deletable — see
    #: ``aida.persistence.records.attachments_dir``.
    attachments_path: str | None = None
    sidecar_path: str | None = None


@dataclass
class ArtifactRecord:
    """One row of ``artifacts``."""

    id: str
    conversation_id: str
    call_id: str | None
    kind: str
    path: str | None
    mime_type: str | None
    created_at: str
    #: U6(b): the ``seq`` of the message this artifact belongs with (set at
    #: write time — see ``ConversationRecorder.record_artifact_fields`` /
    #: ``next_message_seq``), so a resumed conversation can show it right
    #: after that message instead of appended at the end of the transcript.
    #: ``None`` for rows written before this field existed, or for any
    #: caller that doesn't know the owning message's seq yet.
    seq: int | None = None


def _artifact_kind(artifact: Artifact) -> str:
    return type(artifact).__name__


def _artifact_path(artifact: Artifact) -> str | None:
    if isinstance(artifact, (ImageArtifact, FileArtifact)):
        return artifact.path
    return None


def _artifact_mime_type(artifact: Artifact) -> str | None:
    return getattr(artifact, "mime_type", None)


class ConversationStore:
    """CRUD over the ``conversations``/``messages``/``artifacts`` tables."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._conn = connect(db_path)

    # --- conversations -------------------------------------------------

    def create_conversation(
        self,
        *,
        timestamp: str,
        conversation_id: str | None = None,
        title: str | None = None,
        workspace_name: str | None = None,
        profile_name: str | None = None,
        sidecar_dirname: str = "figures",
        origin: str | None = None,
        user: str | None = None,
    ) -> str:
        conv_id = conversation_id or new_conversation_id()
        self._conn.execute(
            "INSERT INTO conversations "
            '(id, title, workspace_name, profile_name, sidecar_dirname, created_at, updated_at, origin, "user") '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, title, workspace_name, profile_name, sidecar_dirname, timestamp, timestamp, origin, user or None),
        )
        self._conn.commit()
        return conv_id

    def get_conversation(self, conversation_id: str) -> ConversationSummary | None:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        count = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        return self._row_to_summary(row, count)

    def list_conversations(
        self, user: str | None = None, *, include_unowned: bool = True
    ) -> list[ConversationSummary]:
        """Newest first. ``user=None`` means no filtering at all — today's
        behaviour, and what cleanup and every "show all" path want.

        ``include_unowned`` keeps conversations with a NULL ``user`` visible
        while filtering, and defaults to True on purpose: every conversation
        that predates migration 4 has NULL there, so excluding them would
        make a user's whole history vanish from the sidebar the first time
        they picked a name — a data-loss-shaped surprise from what is only a
        labelling feature.
        """
        sql = (
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c"
        )
        params: tuple[str, ...] = ()
        if user:
            sql += ' WHERE (c."user" = ?' + (' OR c."user" IS NULL)' if include_unowned else ")")
            params = (user,)
        sql += " ORDER BY c.updated_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_summary(row, row["message_count"]) for row in rows]

    def set_conversation_user(self, conversation_ids: list[str], user: str, *, timestamp: str) -> int:
        """Move specific conversations to ``user`` (or unlabel them when it
        is empty). Returns how many rows changed.

        Distinct from ``rename_user``, which moves *everything* a name owns:
        this is the "I had the wrong name selected when I started that
        chat" repair, which is the mistake a free-text label makes easy and
        which nothing else could fix. ``updated_at`` is deliberately left
        alone — relabelling is not activity, and bumping it would reorder
        the sidebar and quietly change what a cleanup-older-than sweep
        would catch.
        """
        if not conversation_ids:
            return 0
        placeholders = ",".join("?" for _ in conversation_ids)
        cursor = self._conn.execute(
            f'UPDATE conversations SET "user" = ? WHERE id IN ({placeholders})',  # noqa: S608 - ids are bound
            (user or None, *conversation_ids),
        )
        self._conn.commit()
        return cursor.rowcount

    def rename_user(self, old_name: str, new_name: str, *, timestamp: str) -> int:
        """Relabel every conversation belonging to ``old_name``. Returns how
        many rows changed.

        This is the repair operation a free-text name box makes necessary
        rather than merely nice: nothing validates a typed name, so "Jan",
        "jan" and "Jam" are three different people as far as the DB is
        concerned, and without this there is no way to put a split history
        back together from inside the app. Renaming onto a name that
        already exists is therefore a *merge*, deliberately — that is
        exactly what fixing a typo means.

        An empty ``new_name`` clears the label (NULL), which is what
        "remove this user" means here: their conversations stay, and become
        visible to everyone, because nothing about this axis is a
        permission and deleting someone's work is a different, louder
        operation that already lives in the sidebar.
        """
        if not old_name:
            return 0
        cursor = self._conn.execute(
            'UPDATE conversations SET "user" = ?, updated_at = updated_at WHERE "user" = ?',
            (new_name or None, old_name),
        )
        self._conn.commit()
        return cursor.rowcount

    def user_counts(self) -> list[tuple[str, int]]:
        """``[(name, conversations)]``, case-insensitively sorted — what the
        management dialog lists. Counts only conversations that actually
        hold messages, matching what the sidebar shows, so a name does not
        appear to own work that was never used."""
        rows = self._conn.execute(
            'SELECT c."user" AS name, COUNT(*) AS n FROM conversations c '
            'WHERE c."user" IS NOT NULL AND c."user" != \'\' '
            "AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id) "
            'GROUP BY c."user" ORDER BY c."user" COLLATE NOCASE'
        ).fetchall()
        return [(row["name"], row["n"]) for row in rows]

    def known_users(self) -> list[str]:
        """Every distinct non-empty ``user`` in this DB, case-insensitively
        sorted — what populates the GUI picker. No user table and no
        registration step: a name exists because a conversation used it."""
        rows = self._conn.execute(
            'SELECT DISTINCT "user" FROM conversations '
            'WHERE "user" IS NOT NULL AND "user" != \'\' ORDER BY "user" COLLATE NOCASE'
        ).fetchall()
        return [row["user"] for row in rows]

    def set_title(self, conversation_id: str, title: str, *, timestamp: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, timestamp, conversation_id),
        )
        self._conn.commit()

    def set_attachments_path(self, conversation_id: str, path: str, *, timestamp: str) -> None:
        """Record where this conversation's attachments folder really is,
        on first use. Deletion reads this back instead of recomputing it
        from the current records-dir setting."""
        self._conn.execute(
            "UPDATE conversations SET attachments_path = ?, updated_at = ? WHERE id = ?",
            (path, timestamp, conversation_id),
        )
        self._conn.commit()

    def set_sidecar_path(self, conversation_id: str, path: str, *, timestamp: str) -> None:
        """Same, for the sidecar (``figures/``) folder — closing the same
        recompute-at-delete-time hole for conversation-produced images."""
        self._conn.execute(
            "UPDATE conversations SET sidecar_path = ?, updated_at = ? WHERE id = ?",
            (path, timestamp, conversation_id),
        )
        self._conn.commit()

    def set_record_path(self, conversation_id: str, path: str, *, timestamp: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET record_path = ?, updated_at = ? WHERE id = ?",
            (path, timestamp, conversation_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_summary(row: sqlite3.Row, message_count: int) -> ConversationSummary:
        return ConversationSummary(
            id=row["id"],
            title=row["title"],
            workspace_name=row["workspace_name"],
            profile_name=row["profile_name"],
            sidecar_dirname=row["sidecar_dirname"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            record_path=row["record_path"],
            message_count=message_count,
            origin=row["origin"],
            user=row["user"],
            attachments_path=row["attachments_path"],
            sidecar_path=row["sidecar_path"],
        )

    # --- messages --------------------------------------------------------

    def append_message(self, conversation_id: str, message: Message, *, timestamp: str) -> int:
        """Persist one message, returning its sequence number within the
        conversation. Bumps the conversation's ``updated_at``."""
        seq = self._next_seq(conversation_id)
        tool_calls_json = (
            json.dumps([{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in message.tool_calls])
            if message.tool_calls
            else None
        )
        self._conn.execute(
            "INSERT INTO messages "
            "(conversation_id, seq, role, content, tool_calls_json, tool_call_id, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                seq,
                message.role,
                message.content,
                tool_calls_json,
                message.tool_call_id,
                message.name,
                timestamp,
            ),
        )
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (timestamp, conversation_id)
        )
        self._conn.commit()
        return seq

    def load_messages(self, conversation_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC", (conversation_id,)
        ).fetchall()
        images = self._attached_images(conversation_id)
        return [self._row_to_message(row, images.get(row["seq"], [])) for row in rows]

    def load_messages_with_seq(self, conversation_id: str) -> list[tuple[int, Message]]:
        """Same as ``load_messages`` but paired with each message's ``seq``
        — U6(b): the GUI resume path needs it to interleave artifacts at
        their original position (``artifacts.seq`` matches one of these)."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC", (conversation_id,)
        ).fetchall()
        images = self._attached_images(conversation_id)
        return [(row["seq"], self._row_to_message(row, images.get(row["seq"], []))) for row in rows]

    def append_attached_images(
        self, conversation_id: str, *, message_seq: int, images: list[ImageRef], timestamp: str
    ) -> None:
        """Persist the images attached to one user message, anchored to its
        ``seq``.

        Stored as ``artifacts`` rows of kind ``USER_IMAGE_KIND`` rather than
        in a second table: the shape needed (id, path, mime type, and a
        message anchor) is exactly what that table already holds, and the
        existing artifact-deletion path then cleans these up with everything
        else in the conversation for free.

        Nothing persisted ``Message.images`` at all before this. The text
        placeholder for an attachment survived a resume while its pixels did
        not, so a resumed conversation showed the model a reference to an
        image it could no longer see — and the only pointer to those pixels
        had been a path in whatever folder the user picked the file from.
        """
        for position, ref in enumerate(images):
            self.append_artifact(
                conversation_id,
                artifact_id=f"{conversation_id}:{message_seq}:{position}",
                kind=USER_IMAGE_KIND,
                path=ref.path,
                mime_type=ref.mime_type,
                call_id=None,
                timestamp=timestamp,
                seq=message_seq,
            )

    def _attached_images(self, conversation_id: str) -> dict[int, list[ImageRef]]:
        """``{message seq: its attached images}``, in the order they were
        attached — ``id`` ends in the attachment's position, so ordering by
        it restores the original sequence."""
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE conversation_id = ? AND kind = ? AND seq IS NOT NULL "
            "ORDER BY seq ASC, id ASC",
            (conversation_id, USER_IMAGE_KIND),
        ).fetchall()
        by_seq: dict[int, list[ImageRef]] = {}
        for row in rows:
            if not row["path"]:
                continue
            by_seq.setdefault(row["seq"], []).append(
                ImageRef(path=row["path"], mime_type=row["mime_type"])
            )
        return by_seq

    @staticmethod
    def _row_to_message(row: sqlite3.Row, images: list[ImageRef] | None = None) -> Message:
        tool_calls = (
            [ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in json.loads(row["tool_calls_json"])]
            if row["tool_calls_json"]
            else []
        )
        return Message(
            role=row["role"],
            content=row["content"],
            tool_calls=tool_calls,
            tool_call_id=row["tool_call_id"],
            name=row["name"],
            images=list(images or []),
        )

    def _next_seq(self, conversation_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row[0]

    def next_seq(self, conversation_id: str) -> int:
        """Public wrapper for ``_next_seq`` — U6(b): the seq the *next*
        appended message will receive. An ``ImageArtifactCreated``/
        ``FileArtifactCreated`` event can arrive before the tool-result
        message that will carry it is itself persisted (see
        ``aida.core.agent.AgentLoop.run``), so ``aida.cli.chat.ChatSession``
        needs to know that message's future seq ahead of time to record the
        association (``ConversationRecorder.next_message_seq``)."""
        return self._next_seq(conversation_id)

    # --- artifacts ---------------------------------------------------------

    def append_artifact(
        self,
        conversation_id: str,
        *,
        artifact_id: str,
        kind: str,
        path: str | None,
        mime_type: str | None,
        call_id: str | None,
        timestamp: str,
        seq: int | None = None,
    ) -> None:
        """Primitive fields, not an ``Artifact`` object — this is what a
        frontend actually has on hand (e.g. from an
        ``ImageArtifactCreated``/``FileArtifactCreated`` event, which
        carries exactly these fields and not a full ``Artifact`` instance).
        Use ``append_artifact_from_object`` when a real ``Artifact`` is
        available instead. ``seq`` is U6(b)'s interleave anchor — see
        ``ArtifactRecord.seq``."""
        self._conn.execute(
            "INSERT INTO artifacts (id, conversation_id, call_id, kind, path, mime_type, created_at, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, conversation_id, call_id, kind, path, mime_type, timestamp, seq),
        )
        self._conn.commit()

    def append_artifact_from_object(
        self,
        conversation_id: str,
        artifact: Artifact,
        *,
        call_id: str | None,
        timestamp: str,
        seq: int | None = None,
    ) -> None:
        self.append_artifact(
            conversation_id,
            artifact_id=artifact.id,
            kind=_artifact_kind(artifact),
            path=_artifact_path(artifact),
            mime_type=_artifact_mime_type(artifact),
            call_id=call_id,
            timestamp=timestamp,
            seq=seq,
        )

    def load_artifacts(self, conversation_id: str) -> list[ArtifactRecord]:
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [
            ArtifactRecord(
                id=row["id"],
                conversation_id=row["conversation_id"],
                call_id=row["call_id"],
                kind=row["kind"],
                path=row["path"],
                mime_type=row["mime_type"],
                created_at=row["created_at"],
                seq=row["seq"],
            )
            for row in rows
        ]

    # --- deletion (aida.persistence.cleanup builds on this) -------------

    def delete_conversation(self, conversation_id: str) -> tuple[int, int]:
        """Delete the conversation's DB rows only (messages, artifacts,
        conversation) — does NOT touch files on disk. Returns
        ``(deleted_message_count, deleted_artifact_count)``.
        ``aida.persistence.cleanup.delete_conversation`` is the one that
        also removes the artifact files/sidecar dir/record file; call that
        instead of this directly unless you specifically want DB-only
        deletion."""
        message_count = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        artifact_count = self._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        self._conn.execute("DELETE FROM artifacts WHERE conversation_id = ?", (conversation_id,))
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._conn.commit()
        return message_count, artifact_count

    def close(self) -> None:
        self._conn.close()


@dataclass
class ScheduleRun:
    """One row of ``schedule_runs`` — the scheduler's own last-fired/status
    bookkeeping (planning/phase10_scheduling_design.md §5: kept separate
    from the user-edited ``schedules.yaml`` on purpose, same reasoning as
    keeping conversations in SQLite rather than in workspace config)."""

    id: int
    schedule_name: str
    fired_at: str
    status: str
    conversation_id: str | None
    error: str | None


class ScheduleRunStore:
    """CRUD over the ``schedule_runs`` table. A separate small class rather
    than more methods on ``ConversationStore``: it has nothing to do with
    conversation content, just scheduler run history, but it shares the same
    DB file/connection helper (``aida.persistence.db.connect``) since a
    dedicated second SQLite file for a handful of rows would be
    disproportionate."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._conn = connect(db_path)

    def record_run(
        self,
        *,
        schedule_name: str,
        fired_at: str,
        status: str,
        conversation_id: str | None = None,
        error: str | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO schedule_runs (schedule_name, fired_at, status, conversation_id, error) "
            "VALUES (?, ?, ?, ?, ?)",
            (schedule_name, fired_at, status, conversation_id, error),
        )
        self._conn.commit()
        return cursor.lastrowid

    def last_run(self, schedule_name: str) -> ScheduleRun | None:
        row = self._conn.execute(
            "SELECT * FROM schedule_runs WHERE schedule_name = ? ORDER BY fired_at DESC, id DESC LIMIT 1",
            (schedule_name,),
        ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def recent_runs(self, schedule_name: str, *, limit: int = 20) -> list[ScheduleRun]:
        rows = self._conn.execute(
            "SELECT * FROM schedule_runs WHERE schedule_name = ? ORDER BY fired_at DESC, id DESC LIMIT ?",
            (schedule_name, limit),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def recent_failures(self, *, limit: int = 20) -> list[ScheduleRun]:
        """Across every schedule — what the GUI's failure indicator shows."""
        rows = self._conn.execute(
            "SELECT * FROM schedule_runs WHERE status != 'ok' ORDER BY fired_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> ScheduleRun:
        return ScheduleRun(
            id=row["id"],
            schedule_name=row["schedule_name"],
            fired_at=row["fired_at"],
            status=row["status"],
            conversation_id=row["conversation_id"],
            error=row["error"],
        )

    def close(self) -> None:
        self._conn.close()


__all__ = [
    "ArtifactRecord",
    "ConversationStore",
    "ConversationSummary",
    "ScheduleRun",
    "ScheduleRunStore",
    "new_conversation_id",
]
