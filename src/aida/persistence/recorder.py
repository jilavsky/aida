"""``ConversationRecorder`` — the piece ``aida.cli.chat.ChatSession`` (and any
future frontend) talks to for persistence, so nothing above this module
writes SQL or touches the Markdown export directly.

Write-path design (Phase 4: "agent events are persisted as they stream,
crash-safe enough that a killed session leaves a readable partial
conversation"): each *finalized* ``Message`` (a full user message, a full
assistant message once its provider stream ends, each tool-result message)
is written to the DB the moment it exists, not batched until the end of a
multi-tool-call turn. A session killed mid-turn loses only the in-flight
streaming text of the turn that was interrupted — every prior message,
including earlier tool calls/results within the *same* turn, is already
durable. Token-by-token delta persistence would close that last gap too,
but adds real complexity (partial-message rows, a message_id scheme shared
between provider events and the DB) for a marginal benefit; this module
documents the trade-off rather than hiding it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aida.artifacts.base import Artifact
from aida.artifacts.store import ArtifactStore
from aida.persistence.records import record_file_path, write_transcript
from aida.persistence.store import ConversationStore
from aida.providers.base import Message

_TITLE_MAX_CHARS = 60


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _derive_title(user_text: str) -> str:
    first_line = user_text.strip().splitlines()[0] if user_text.strip() else "New conversation"
    if len(first_line) > _TITLE_MAX_CHARS:
        return first_line[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
    return first_line


class ConversationNotFoundError(Exception):
    """Raised when resuming a conversation id that isn't in the DB."""


class ConversationRecorder:
    """Persists one conversation's messages/artifacts as they happen, and
    keeps its Markdown transcript up to date."""

    def __init__(
        self,
        store: ConversationStore,
        artifact_store: ArtifactStore,
        records_dir: Path,
        *,
        conversation_id: str | None = None,
        workspace_name: str | None = None,
        profile_name: str | None = None,
        sidecar_dirname: str = "figures",
        resume: bool = False,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.records_dir = records_dir

        if resume:
            if conversation_id is None:
                raise ValueError("resume=True requires a conversation_id")
            existing = store.get_conversation(conversation_id)
            if existing is None:
                raise ConversationNotFoundError(f"no conversation with id {conversation_id!r}")
            self.conversation_id = conversation_id
            self.title = existing.title
            self.workspace_name = existing.workspace_name
            self.profile_name = existing.profile_name
            self.sidecar_dirname = existing.sidecar_dirname
            self._record_path = Path(existing.record_path) if existing.record_path else None
        else:
            self.title = None
            self.workspace_name = workspace_name
            self.profile_name = profile_name
            self.sidecar_dirname = sidecar_dirname
            self.conversation_id = store.create_conversation(
                timestamp=_now_iso(),
                conversation_id=conversation_id,
                workspace_name=workspace_name,
                profile_name=profile_name,
                sidecar_dirname=sidecar_dirname,
            )
            self._record_path = None

    def record_message(self, message: Message) -> int:
        """Persist one finalized message immediately, set an auto-derived
        title on the first user message if none exists yet, and refresh the
        Markdown transcript. Returns the message's sequence number."""
        timestamp = _now_iso()
        if self.title is None and message.role == "user" and message.content:
            self.title = _derive_title(message.content)
            self.store.set_title(self.conversation_id, self.title, timestamp=timestamp)

        seq = self.store.append_message(self.conversation_id, message, timestamp=timestamp)
        self.export_transcript()
        return seq

    def record_artifact(self, artifact: Artifact, *, call_id: str | None) -> None:
        """Persist a real ``Artifact`` object's metadata."""
        self.store.append_artifact_from_object(
            self.conversation_id, artifact, call_id=call_id, timestamp=_now_iso()
        )

    def record_artifact_fields(
        self, *, artifact_id: str, kind: str, path: str | None, mime_type: str | None, call_id: str | None
    ) -> None:
        """Persist artifact metadata from primitive fields — what
        ``aida.cli.chat`` actually has on hand from an
        ``ImageArtifactCreated``/``FileArtifactCreated`` event, which
        doesn't carry a full ``Artifact`` instance."""
        self.store.append_artifact(
            self.conversation_id,
            artifact_id=artifact_id,
            kind=kind,
            path=path,
            mime_type=mime_type,
            call_id=call_id,
            timestamp=_now_iso(),
        )

    def export_transcript(self) -> Path:
        """Write (or overwrite) this conversation's Markdown transcript,
        reusing the same path on every call once one has been chosen —
        see ``aida.persistence.records.write_transcript`` for why."""
        if self._record_path is None:
            self._record_path = record_file_path(self.records_dir, self.conversation_id, self.title)

        write_transcript(
            path=self._record_path,
            records_dir=self.records_dir,
            artifact_store=self.artifact_store,
            conversation_id=self.conversation_id,
            title=self.title,
            workspace_name=self.workspace_name,
            profile_name=self.profile_name,
            messages=self.store.load_messages(self.conversation_id),
            artifacts=self.store.load_artifacts(self.conversation_id),
            sidecar_dirname=self.sidecar_dirname,
        )
        self.store.set_record_path(self.conversation_id, str(self._record_path), timestamp=_now_iso())
        return self._record_path

    def load_history(self) -> list[Message]:
        """All messages persisted so far, in order — used to rebuild
        context on resume."""
        return self.store.load_messages(self.conversation_id)


__all__ = ["ConversationNotFoundError", "ConversationRecorder"]
