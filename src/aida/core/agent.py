"""The agent loop (PLAN.md §3 "Agent loop"):

    user message -> build context -> provider.complete(messages, tools)
                 -> stream events to caller
                 -> on tool call: execute -> feed result back
                 -> repeat until final text or iteration cap

This module is GUI-independent (PLAN.md §3 hard rule 1) — it only knows
about ``aida.providers`` and ``aida.core.events``/``tools``. The CLI chat
harness (``aida.cli.chat``) and, later, the Qt frontend both drive it the
same way: append a user ``Message``, iterate ``AgentLoop.run()``.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import AsyncIterator

from aida.artifacts.base import Artifact, FileArtifact, ImageArtifact
from aida.config.logging_setup import get_logger
from aida.core.events import (
    AgentError,
    AgentEvent,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    TextFinished,
    ToolCallFinished,
    ToolCallStarted,
    UsageInfo,
)
from aida.core.tools import NativeTool
from aida.providers.base import (
    CompletionSettings,
    ImageRef,
    LLMProvider,
    Message,
    ToolCall,
    ToolSchema,
)

DEFAULT_MAX_ITERATIONS = 10

#: What a tool call that never ran gets recorded as. Both providers reject a
#: history where an assistant ``tool_use``/``tool_calls`` entry has no
#: matching result message, so a cancelled turn must still answer every call
#: it announced — see ``_run_turns``' cancel branch and
#: ``aida.core.context.repair_tool_call_pairing``.
CANCELLED_TOOL_RESULT = "(cancelled — this tool call was never executed)"

logger = get_logger("agent")


def _stringify(value: object) -> str:
    """Tool results fed back to the model must be strings (or the provider's
    content type); non-string results are JSON-encoded where possible."""
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value)
    except TypeError:
        return str(value)


class AgentLoop:
    """Drives one conversation's worth of provider calls + tool execution.

    One ``AgentLoop`` instance is meant to live for one conversation (or one
    CLI chat session); call ``run(messages)`` once per user turn, with
    ``messages`` being the *full* history so far — ``run`` appends to it in
    place as the turn progresses, so the caller's list is the source of
    truth for the next call too.
    """

    def __init__(
        self,
        provider: LLMProvider,
        settings: CompletionSettings,
        tools: dict[str, NativeTool] | None = None,
        *,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.tools = tools or {}
        self.max_iterations = max_iterations
        self._cancelled = False

    def _tool_schemas(self) -> list[ToolSchema]:
        return [t.schema for t in self.tools.values()]

    def cancel(self) -> None:
        """Request cancellation. Takes effect at the next checkpoint (before
        the next provider call or before the next tool execution) — an
        in-flight provider stream is drained, not killed mid-token."""
        self._cancelled = True

    async def run(self, messages: list[Message]) -> AsyncIterator[AgentEvent]:
        """Run turns (provider call, then any requested tool calls, repeat)
        until the model gives a final text answer, cancellation is
        requested, or ``max_iterations`` is hit. Mutates ``messages`` in
        place with each assistant/tool message as it happens.

        A ``cancel()`` issued *before* this call (or received while it's
        running) is honored and then cleared once this call ends — via
        ``try/finally``, so it resets on every exit path (normal
        completion, cancellation, iteration cap, provider error) — so a
        reused ``AgentLoop`` (as ``ChatSession`` does across turns) starts
        each new turn uncancelled rather than staying wedged after one
        Ctrl-C.
        """
        try:
            async for event in self._run_turns(messages):
                yield event
        finally:
            self._cancelled = False

    async def _run_turns(self, messages: list[Message]) -> AsyncIterator[AgentEvent]:
        iterations = 0

        while True:
            if self._cancelled:
                yield AgentError(layer="core", message="cancelled")
                return

            iterations += 1
            if iterations > self.max_iterations:
                yield AgentError(
                    layer="core",
                    message=f"iteration cap reached ({self.max_iterations})",
                )
                return

            assistant_text = ""
            pending_tool_calls: list[ToolCall] = []
            terminated_by_error = False

            round_trip_started = time.monotonic()
            async for event in self.provider.complete(messages, self._tool_schemas(), self.settings):
                # Wallclock duration of this provider round-trip, stamped
                # here (not by the provider itself) so it's available
                # uniformly regardless of whether a given provider's own
                # API reports timing — see UsageInfo's docstring.
                if isinstance(event, UsageInfo) and event.duration_seconds is None:
                    event = dataclasses.replace(event, duration_seconds=time.monotonic() - round_trip_started)
                yield event
                if isinstance(event, TextFinished):
                    assistant_text = event.text
                elif isinstance(event, ToolCallStarted):
                    pending_tool_calls.append(
                        ToolCall(id=event.call_id, name=event.tool_name, arguments=event.arguments)
                    )
                elif isinstance(event, MessageFinished):
                    pass  # stop_reason itself doesn't drive control flow; tool_calls list does
                elif isinstance(event, AgentError):
                    terminated_by_error = True

            if terminated_by_error:
                return

            messages.append(
                Message(role="assistant", content=assistant_text, tool_calls=pending_tool_calls)
            )

            if not pending_tool_calls:
                return  # final answer — this turn is done

            for position, tc in enumerate(pending_tool_calls):
                if self._cancelled:
                    # The assistant message appended above announces *every*
                    # call in pending_tool_calls. Returning here without
                    # answering the ones that never ran leaves orphaned
                    # tool_use blocks in `messages`, which both providers
                    # reject on the *next* request ("each tool_use must have
                    # a corresponding tool_result") — so pressing Stop
                    # mid-tool-call used to wedge the conversation
                    # permanently, resumed sessions included, since those
                    # messages are persisted as they land. Answering them
                    # with a synthetic result keeps the history valid and
                    # tells the model plainly what happened.
                    for cancelled_call in pending_tool_calls[position:]:
                        yield ToolCallFinished(
                            call_id=cancelled_call.id,
                            tool_name=cancelled_call.name,
                            result=CANCELLED_TOOL_RESULT,
                            is_error=True,
                        )
                        messages.append(
                            Message(
                                role="tool",
                                content=CANCELLED_TOOL_RESULT,
                                tool_call_id=cancelled_call.id,
                                name=cancelled_call.name,
                            )
                        )
                    yield AgentError(layer="core", message="cancelled")
                    return

                tool = self.tools.get(tc.name)
                result_artifacts: list[Artifact] = []
                logger.debug("tool call: %s(%r)", tc.name, tc.arguments)
                if tool is None:
                    result_content: object = f"Unknown tool: {tc.name}"
                    is_error = True
                    logger.warning("tool call to unknown tool %r (arguments=%r)", tc.name, tc.arguments)
                else:
                    try:
                        result = await tool.func(tc.arguments)
                        result_content = result.content
                        is_error = result.is_error
                        result_artifacts = result.artifacts
                    except Exception as exc:  # noqa: BLE001 - a tool crash must not kill the loop
                        result_content = str(exc)
                        is_error = True
                        logger.warning("tool %s(%r) raised: %s", tc.name, tc.arguments, exc, exc_info=True)

                if is_error:
                    logger.info("tool %s finished with error: %s", tc.name, result_content)
                else:
                    logger.debug("tool %s finished ok", tc.name)

                yield ToolCallFinished(
                    call_id=tc.id, tool_name=tc.name, result=result_content, is_error=is_error
                )

                # Typed artifacts a tool result carried (PLAN.md hard rule 3):
                # tell the frontend about each one via its own event, rather
                # than letting an ImageArtifact silently ride along inside
                # ToolCallFinished.result where a UI would have to guess.
                for artifact in result_artifacts:
                    if isinstance(artifact, ImageArtifact):
                        yield ImageArtifactCreated(
                            artifact_id=artifact.id,
                            call_id=tc.id,
                            mime_type=artifact.mime_type,
                            path=artifact.path,
                        )
                    elif isinstance(artifact, FileArtifact) and artifact.path is not None:
                        yield FileArtifactCreated(
                            artifact_id=artifact.id,
                            call_id=tc.id,
                            path=artifact.path,
                            mime_type=artifact.mime_type,
                        )

                # B1: any ImageArtifact this tool result produced is
                # attached to the tool message so a later provider call can
                # send it as vision input (aida.providers.vision) — it's
                # already on disk with a path by this point (McpManager
                # saves before returning), so there is nothing more to do
                # here than remember where. A path-less ImageArtifact
                # (shouldn't happen post-save, but defensive) is skipped
                # rather than attached with nothing to read later.
                image_refs = [
                    ImageRef(path=a.path, mime_type=a.mime_type)
                    for a in result_artifacts
                    if isinstance(a, ImageArtifact) and a.path
                ]
                messages.append(
                    Message(
                        role="tool",
                        content=_stringify(result_content),
                        tool_call_id=tc.id,
                        name=tc.name,
                        images=image_refs,
                    )
                )

            # loop continues: provider gets called again with tool results appended


__all__ = ["CANCELLED_TOOL_RESULT", "DEFAULT_MAX_ITERATIONS", "AgentLoop"]
