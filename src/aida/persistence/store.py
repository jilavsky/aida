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
from aida.providers.base import Message, ToolCall


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
    ) -> str:
        conv_id = conversation_id or new_conversation_id()
        self._conn.execute(
            "INSERT INTO conversations "
            "(id, title, workspace_name, profile_name, sidecar_dirname, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conv_id, title, workspace_name, profile_name, sidecar_dirname, timestamp, timestamp),
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

    def list_conversations(self) -> list[ConversationSummary]:
        rows = self._conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c ORDER BY c.updated_at DESC"
        ).fetchall()
        return [self._row_to_summary(row, row["message_count"]) for row in rows]

    def set_title(self, conversation_id: str, title: str, *, timestamp: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, timestamp, conversation_id),
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
        return [self._row_to_message(row) for row in rows]

    def load_messages_with_seq(self, conversation_id: str) -> list[tuple[int, Message]]:
        """Same as ``load_messages`` but paired with each message's ``seq``
        — U6(b): the GUI resume path needs it to interleave artifacts at
        their original position (``artifacts.seq`` matches one of these)."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC", (conversation_id,)
        ).fetchall()
        return [(row["seq"], self._row_to_message(row)) for row in rows]

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
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


__all__ = ["ArtifactRecord", "ConversationStore", "ConversationSummary", "new_conversation_id"]
