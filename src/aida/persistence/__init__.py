"""SQLite persistence: conversations, messages, tool calls, artifact metadata.
Never imports Qt.

See ``aida.persistence.db`` (schema + connection), ``aida.persistence.store``
(``ConversationStore`` CRUD), ``aida.persistence.recorder``
(``ConversationRecorder`` — what ``aida.cli.chat`` actually talks to),
``aida.persistence.records`` (Markdown transcript export), and
``aida.persistence.cleanup`` (delete-with-no-orphans).
"""

from aida.persistence.cleanup import DeletionResult, delete_conversation, list_conversations_by_age
from aida.persistence.recorder import ConversationNotFoundError, ConversationRecorder
from aida.persistence.store import ArtifactRecord, ConversationStore, ConversationSummary

__all__ = [
    "ArtifactRecord",
    "ConversationNotFoundError",
    "ConversationRecorder",
    "ConversationStore",
    "ConversationSummary",
    "DeletionResult",
    "delete_conversation",
    "list_conversations_by_age",
]
