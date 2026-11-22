"""Test helpers for context compaction.

A transcript may be summarized in several pieces (see
``aida.core.context.summary_transcript_chunks``), so a test that scripts
"one summary reply, then the real reply" would depend on how many pieces a
given history happens to split into. ``SummarizingMockProvider`` answers
every compaction request itself and replays its script only for ordinary
requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from aida.core.context import COMPACTION_PROMPT
from aida.core.events import AgentEvent, TextFinished
from aida.providers.base import CompletionSettings, Message, ToolSchema
from aida.providers.mock import MockProvider, MockTurn


def is_compaction_request(messages: list[Message]) -> bool:
    return len(messages) == 1 and messages[0].content.startswith(COMPACTION_PROMPT)


class SummarizingMockProvider(MockProvider):
    """``MockProvider`` that replies ``summary_text`` to every compaction
    request without consuming the script, and counts those requests in
    ``summary_requests``."""

    def __init__(self, script: list[MockTurn] | None = None, *, summary_text: str = "- summary"):
        super().__init__(script)
        self.summary_text = summary_text
        self.summary_requests: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        settings: CompletionSettings,
    ) -> AsyncIterator[AgentEvent]:
        if is_compaction_request(messages):
            self.summary_requests.append(list(messages))
            yield TextFinished(message_id="mock-summary", text=self.summary_text)
            return
        async for event in super().complete(messages, tools, settings):
            yield event
