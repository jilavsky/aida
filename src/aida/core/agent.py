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

import contextlib
import dataclasses
import logging
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable

from aida.artifacts.base import Artifact, FileArtifact, ImageArtifact
from aida.config.logging_setup import get_logger
from aida.core.argument_coercion import coerce_arguments
from aida.core.events import (
    AgentError,
    AgentEvent,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    ProviderRetrying,
    SteeringMessageDelivered,
    TextDelta,
    TextFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnNudged,
    UsageInfo,
)
from aida.core.loop_detection import (
    DEFAULT_LOOP_NUDGE_THRESHOLD,
    MAX_LOOP_NUDGES,
    LoopSign,
    ProgressMonitor,
)
from aida.core.tools import NativeTool
from aida.providers.base import (
    UNPARSED_ARGUMENTS_KEY,
    CompletionSettings,
    ImageRef,
    LLMProvider,
    Message,
    ToolCall,
    ToolSchema,
)
from aida.providers.retry import DEFAULT_RETRY_POLICY, RetryPolicy, complete_with_retries

DEFAULT_MAX_ITERATIONS = 50

#: What a tool call that never ran gets recorded as. Both providers reject a
#: history where an assistant ``tool_use``/``tool_calls`` entry has no
#: matching result message, so a cancelled turn must still answer every call
#: it announced — see ``_run_turns``' cancel branch and
#: ``aida.core.context.repair_tool_call_pairing``.
CANCELLED_TOOL_RESULT = "(cancelled — this tool call was never executed)"

#: Sent when a reply was cut off at the output token limit before it made a
#: usable tool call. Once per turn (``MAX_LENGTH_RECOVERIES``).
LENGTH_NUDGE = (
    "[Sent automatically by AIDA] Your last reply was cut off at the output token limit "
    "before it finished. Continue the task. Keep each reply short, and write a large file "
    "in several smaller steps (write_file for the first part, then edit_file or further "
    "writes for the rest)."
)

#: Sent when a short reply after tool work made no tool call and looks like a
#: stall (``is_stall``). At most ``MAX_STALL_NUDGES`` per turn.
STALL_NUDGE = (
    "[Sent automatically by AIDA] Your last reply made no tool call, so nothing happened. "
    "Continue the task now. If it is already finished, say so in one sentence."
)

#: The stall nudge when the end of the reasoning contains tool-call markup:
#: the model wrote the call in its thinking and never sent it.
STALL_NUDGE_CALL_IN_REASONING = (
    "[Sent automatically by AIDA] Your reasoning contains a tool call, but it was never sent "
    "as one, so nothing happened. Send it now as a real tool call. If the task is already "
    "finished, say so in one sentence."
)

MAX_LENGTH_RECOVERIES = 1
MAX_STALL_NUDGES = 2

#: A reply longer than this is a real answer, not a stalled announcement.
STALL_MAX_CHARS = 300

#: A short reply is a stall by shape when its reasoning is at least this long
#: *and* this many times longer than the visible text. Measured 2026-11-21:
#: stalls had 15-17k chars of reasoning behind 140-175 chars of text; normal
#: endings had at most ~1.5k behind 2.5k+ chars of text.
STALL_MIN_REASONING_CHARS = 3000
STALL_REASONING_RATIO = 10

#: Tool-call markup a model may write inside its reasoning instead of sending
#: the call: Qwen/Hermes ``<tool_call>``, the XML ``<function=...>`` form, or
#: an OpenAI-style ``{"name": ..., "arguments": ...}`` object.
_TOOL_CALL_MARKUP = re.compile(
    r'<tool_call>|<function=|"name"\s*:\s*"[^"]+"\s*,\s*"(?:arguments|parameters)"\s*:'
)

_ANNOUNCES_STEP = re.compile(
    r"\b(let me|let's|i'll|i will|i am going to|i'm going to|now i|next,? i|writing|"
    r"creating|generating|proceeding|starting|moving on)\b",
    re.IGNORECASE,
)
_CLOSING_PHRASE = re.compile(
    r"\b(let me know|if you (?:want|need|would like|'d like)|feel free)\b", re.IGNORECASE
)


def looks_like_announced_step(text: str) -> bool:
    """Whether a text-only reply reads as "I am about to do X" rather than
    the end of the task: short (``STALL_MAX_CHARS``), not a question, and
    either phrased as an upcoming action ("Let me check…", "Writing the
    report now.") or ending in a colon or an ellipsis. Closing offers ("Let
    me know if…") never count. English phrasing only; a miss just means
    the turn ends as it always did."""
    stripped = text.strip()
    if not stripped or len(stripped) > STALL_MAX_CHARS or stripped.endswith("?"):
        return False
    if _CLOSING_PHRASE.search(stripped):
        return False
    return bool(_ANNOUNCES_STEP.search(stripped)) or stripped.endswith((":", "…", "..."))


def is_stall(text: str, reasoning_chars: int) -> bool:
    """Whether a text-only reply that follows tool work is a stall rather
    than the end of the task.

    By shape first: a short reply (``STALL_MAX_CHARS``) behind a large
    amount of reasoning (``STALL_MIN_REASONING_CHARS`` and
    ``STALL_REASONING_RATIO`` times the text), typically a model that drafted
    the next step in its thinking and then stopped. For providers that send
    no reasoning, ``looks_like_announced_step`` is the fallback. Never a
    question, and never a closing offer ("Let me know if…").
    """
    stripped = text.strip()
    if len(stripped) > STALL_MAX_CHARS or stripped.endswith("?"):
        return False
    if _CLOSING_PHRASE.search(stripped):
        return False
    if (
        reasoning_chars >= STALL_MIN_REASONING_CHARS
        and reasoning_chars >= STALL_REASONING_RATIO * len(stripped)
    ):
        return True
    return looks_like_announced_step(stripped)


def has_tool_call_markup(reasoning: str) -> bool:
    """Whether ``reasoning`` contains something written like a tool call."""
    return bool(_TOOL_CALL_MARKUP.search(reasoning))


def unknown_tool_message(name: str, tools: dict[str, NativeTool]) -> str:
    """The reply to a call naming no known tool, with a suggestion when the
    name is a known tool plus garbage (``edit_file=path``, ``edit_file(``)
    or a near-miss spelling. The guessed tool is never run: its arguments
    may be incomplete."""
    import difflib

    guess: str | None = None
    head = re.split(r"[=(\s]", name, maxsplit=1)[0]
    if head in tools:
        guess = head
    else:
        close = difflib.get_close_matches(name, list(tools), n=1, cutoff=0.8)
        guess = close[0] if close else None
    if guess is None:
        return f"Unknown tool: {name}"
    required = tools[guess].schema.parameters.get("required") or []
    needs = f" Its required arguments are: {', '.join(required)}." if required else ""
    return (
        f"Unknown tool {name!r}. Did you mean {guess!r}?{needs} The call was not run; send it "
        "again with the correct tool name and all of its arguments."
    )


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
        before_round_trip: Callable[[], Awaitable[AgentEvent | None]] | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        tool_result_filter: Callable[[str, str, str], str] | None = None,
        auto_continue_on_stall: bool = True,
        loop_nudge_threshold: int = DEFAULT_LOOP_NUDGE_THRESHOLD,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.tools = tools or {}
        self.max_iterations = max_iterations
        #: How a transient provider failure (Argo busy, overloaded, rate
        #: limited, ...) is resent before it reaches the user as an error —
        #: see aida.providers.retry.
        self.retry_policy = retry_policy
        # Awaited (and its event yielded, if any) at the top of every round
        # trip, including the first: one turn's tool results can grow the
        # history by hundreds of thousands of characters. ``ChatSession``
        # passes its ``_trim_context``, a no-op when under budget. It may
        # shrink tool results older than the last few rounds and drop whole
        # turns before the last one, but always leaves the messages since
        # the last user message well-formed (calls still paired with results).
        self._before_round_trip = before_round_trip
        #: ``(tool_name, call_id, content) -> content`` applied to each tool
        #: result before it enters the history (``ChatSession`` caps its size
        #: there). ``ToolCallFinished`` still carries the unfiltered result.
        self._tool_result_filter = tool_result_filter
        #: Whether a short "I'll do X now" reply with no tool call, right
        #: after tool work, gets ``STALL_NUDGE`` instead of ending the turn.
        self.auto_continue_on_stall = auto_continue_on_stall
        #: Repeats (same call and result, same error, near-copy scripts)
        #: before a turn that keeps working without progress is nudged
        #: (``aida.core.loop_detection``). Below 2: off.
        self.loop_nudge_threshold = loop_nudge_threshold
        self._cancelled = False
        # Text the user typed while a turn was already running, waiting to
        # be handed to the model at the next round trip — see
        # queue_user_message. A deque because the only operations are
        # append-one and popleft-one, both atomic, so the Qt thread can
        # queue while the loop thread drains without a lock.
        self._queued_user_messages: deque[str] = deque()

    def _tool_schemas(self) -> list[ToolSchema]:
        return [t.schema for t in self.tools.values()]

    def cancel(self) -> None:
        """Request cancellation. Takes effect at the next checkpoint, which
        is any of: between provider events (so a model mid-answer is cut
        off within one token rather than streamed to the end), before the
        next provider call, or before the next tool execution.

        The one thing it does *not* interrupt is a tool call already in
        flight — that await runs to completion (bounded by the tool's own
        timeout) and cancellation is honored the moment it returns.
        Killing it mid-call would leave an MCP server with an orphaned
        request and a half-written file with no way to tell which.
        """
        self._cancelled = True

    def clear_cancel(self) -> None:
        """Drop a pending cancellation request. ``ChatSession`` calls this
        when a turn ends, so a Stop that arrives after the run finished but
        before the turn's own cleanup did cannot cancel the next turn."""
        self._cancelled = False

    def queue_user_message(self, text: str) -> None:
        """Hand the running turn a message to deliver at its next round trip
        (user request: "when agent is working, user has no chance for input
        to the process ... so I can tell agent what I forgot").

        Safe to call from another thread — the GUI calls it straight from
        the Qt thread the way it already calls ``cancel()``. Delivery is
        deliberately *between* round trips, never mid-stream and never
        between a tool call and its result: the conversation must stay
        well-formed (every ``tool_use`` answered by its ``tool_result``
        before anything else is appended), which is the same invariant the
        cancellation path goes out of its way to preserve.

        Queuing with no turn running is not an error, but nothing here will
        deliver it — the caller checks ``take_undelivered_messages`` and
        sends it as a normal turn instead.
        """
        text = text.strip()
        if text:
            self._queued_user_messages.append(text)

    def take_undelivered_messages(self) -> list[str]:
        """Remove and return anything queued that was never delivered (the
        turn ended, was cancelled, or errored first).

        Exists so a frontend can put the user's words back in front of them
        rather than silently eating them — text typed during a turn that
        stops half a second later must not simply vanish.
        """
        drained: list[str] = []
        while self._queued_user_messages:
            drained.append(self._queued_user_messages.popleft())
        return drained

    def _deliver_queued_messages(self, messages: list[Message]) -> list[AgentEvent]:
        """Append every queued interjection to ``messages`` as an ordinary
        user turn, oldest first.

        Plain ``role="user"`` messages rather than a new wire concept: both
        providers accept consecutive user messages (the Anthropic API
        combines same-role messages, see ``to_anthropic_params``), so a
        message landing right after a batch of tool results needs no special
        handling anywhere downstream — persistence, resume and trimming all
        treat it as what it is, something the user said.
        """
        events: list[AgentEvent] = []
        while self._queued_user_messages:
            text = self._queued_user_messages.popleft()
            messages.append(Message(role="user", content=text))
            events.append(SteeringMessageDelivered(text=text))
            logger.info("delivered queued user message mid-turn (%d chars)", len(text))
        return events

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
        length_cutoffs = 0
        stall_nudges = 0
        loop_nudges = 0
        progress = ProgressMonitor(threshold=self.loop_nudge_threshold)
        previous_round_had_tools = False

        while True:
            if self._cancelled:
                yield AgentError(layer="core", message="cancelled")
                return

            # Anything the user typed while this turn was running goes in
            # here — after the previous round trip's tool results are all
            # appended, before the next request is built. See
            # queue_user_message.
            for event in self._deliver_queued_messages(messages):
                yield event

            if self._before_round_trip is not None:
                trim_event = await self._before_round_trip()
                if trim_event is not None:
                    yield trim_event

            iterations += 1
            if iterations > self.max_iterations:
                yield AgentError(
                    layer="core",
                    message=f"iteration cap reached ({self.max_iterations})",
                )
                return

            assistant_text = ""
            #: Deltas accumulated as they stream, so a turn cancelled
            #: mid-answer still has the text the user already watched
            #: appear. ``TextFinished`` (the authoritative full text) never
            #: arrives on that path, which would otherwise leave the
            #: transcript showing text that the saved history does not have.
            streamed_text = ""
            pending_tool_calls: list[ToolCall] = []
            terminated_by_error = False
            cancelled_mid_stream = False
            stop_reason: str | None = None
            reasoning_chars = 0
            reasoning_tail = ""
            output_tokens: int | None = None

            round_trip_started = time.monotonic()
            # User report: "the Stop button seems to get stuck ... I have
            # been staring at Working for 180 seconds". Cancellation used
            # to be checked only *between* round trips, so Stop pressed
            # while the model was part-way through a long answer did
            # nothing until the whole answer had streamed in — on a verbose
            # model that is minutes of a button that looks broken.
            #
            # aclosing(), not a bare `break`: breaking out of an `async for`
            # leaves the generator suspended at its yield, and the provider's
            # HTTP response stays open until the garbage collector happens to
            # finalize it. aclosing() throws GeneratorExit in at the yield
            # right now, which unwinds the SDK's stream and drops the
            # connection — the difference between "stops" and "looks stopped".
            async with contextlib.aclosing(
                complete_with_retries(
                    self.provider,
                    messages,
                    self._tool_schemas(),
                    self.settings,
                    policy=self.retry_policy,
                    # Polled during a retry backoff, so Stop does not have
                    # to wait out the delay — see complete_with_retries.
                    should_cancel=lambda: self._cancelled,
                )
            ) as stream:
                async for event in stream:
                    if isinstance(event, ProviderRetrying):
                        # The failed attempt and the wait before the next
                        # one are not part of the round trip the user's
                        # tokens-per-second figure should describe.
                        round_trip_started = time.monotonic() + event.delay_seconds
                    # Wallclock duration of this provider round-trip, stamped
                    # here (not by the provider itself) so it's available
                    # uniformly regardless of whether a given provider's own
                    # API reports timing — see UsageInfo's docstring.
                    if isinstance(event, UsageInfo) and event.duration_seconds is None:
                        event = dataclasses.replace(
                            event, duration_seconds=time.monotonic() - round_trip_started
                        )
                    yield event
                    if isinstance(event, TextFinished):
                        assistant_text = event.text
                    elif isinstance(event, TextDelta):
                        streamed_text += event.text
                    elif isinstance(event, ToolCallStarted):
                        pending_tool_calls.append(
                            ToolCall(
                                id=event.call_id, name=event.tool_name, arguments=event.arguments
                            )
                        )
                    elif isinstance(event, MessageFinished):
                        stop_reason = event.stop_reason
                        reasoning_chars = event.reasoning_chars
                        reasoning_tail = event.reasoning_tail
                    elif isinstance(event, UsageInfo):
                        output_tokens = event.output_tokens
                    elif isinstance(event, AgentError):
                        terminated_by_error = True
                    # Checked after the yield, so the event the caller is
                    # mid-way through handling is never swallowed — a Stop
                    # pressed during it takes effect on the next one.
                    if self._cancelled:
                        cancelled_mid_stream = True
                        break

            if terminated_by_error:
                return

            # An aborted stream has no TextFinished, so fall back to what
            # actually arrived.
            if not assistant_text:
                assistant_text = streamed_text

            # Why the round trip ended: at INFO when it ends the turn (the
            # line that explains an unexpected stop), DEBUG otherwise.
            logger.log(
                logging.DEBUG if pending_tool_calls else logging.INFO,
                "round trip %d ended: stop_reason=%s, text=%d chars, tool_calls=%d, "
                "output_tokens=%s, reasoning=%d chars",
                iterations,
                stop_reason,
                len(assistant_text),
                len(pending_tool_calls),
                output_tokens if output_tokens is not None else "?",
                reasoning_chars,
            )

            if cancelled_mid_stream and not pending_tool_calls:
                # Nothing was announced, so there is no orphaned tool_use
                # for the next request to choke on and the shared cancel
                # branch below has nothing to answer. Append the partial
                # answer only if there *is* one: both providers reject an
                # assistant message whose content is an empty string, so a
                # stream cut off before its first token must leave history
                # untouched rather than poison the next turn.
                if assistant_text:
                    messages.append(Message(role="assistant", content=assistant_text))
                yield AgentError(layer="core", message="cancelled")
                return

            cut_off = (
                not cancelled_mid_stream
                and stop_reason == "length"
                and all(UNPARSED_ARGUMENTS_KEY in tc.arguments for tc in pending_tool_calls)
            )
            if cut_off:
                length_cutoffs += 1

            if not pending_tool_calls:
                if cut_off and length_cutoffs <= MAX_LENGTH_RECOVERIES:
                    # Cut off before any tool call: keep the partial text and
                    # ask the model to carry on in smaller steps.
                    if assistant_text:
                        messages.append(Message(role="assistant", content=assistant_text))
                    yield self._nudge(messages, "length", LENGTH_NUDGE)
                    previous_round_had_tools = False
                    continue
                messages.append(Message(role="assistant", content=assistant_text))
                if cut_off:
                    yield AgentError(layer="core", message=_REPEATED_CUTOFF_MESSAGE)
                    return
                if (
                    stop_reason == "stop"
                    and previous_round_had_tools
                    and len(assistant_text.strip()) <= STALL_MAX_CHARS
                ):
                    # A short ending right after tool work: the case worth a
                    # look in the log, nudged or not. The reasoning tail is
                    # logged, never added to the history.
                    # A question to the user is never answered for them, even
                    # when the reasoning drafted the call it asks about.
                    call_in_reasoning = has_tool_call_markup(
                        reasoning_tail
                    ) and not assistant_text.strip().endswith("?")
                    logger.info(
                        "short reply after tool work (%d chars, reasoning %d chars, tool-call "
                        "markup in reasoning: %s); end of reasoning: %r",
                        len(assistant_text),
                        reasoning_chars,
                        "yes" if call_in_reasoning else "no",
                        reasoning_tail,
                    )
                    if (
                        self.auto_continue_on_stall
                        and stall_nudges < MAX_STALL_NUDGES
                        and (call_in_reasoning or is_stall(assistant_text, reasoning_chars))
                    ):
                        stall_nudges += 1
                        nudge = STALL_NUDGE_CALL_IN_REASONING if call_in_reasoning else STALL_NUDGE
                        yield self._nudge(messages, "stall", nudge)
                        previous_round_had_tools = False
                        continue
                return  # final answer — this turn is done

            messages.append(
                Message(role="assistant", content=assistant_text, tool_calls=pending_tool_calls)
            )
            previous_round_had_tools = True
            # Every call was cut off mid-arguments: each gets the "cut off"
            # tool result below. The second time in one turn, answer them the
            # same way and then end the turn instead of looping.
            stop_after_results = cut_off and length_cutoffs > MAX_LENGTH_RECOVERIES
            #: The first sign of looping this round's calls completed; sent
            #: once all of their results are in the history.
            loop_sign: LoopSign | None = None

            # A stream cancelled *after* announcing calls falls through to
            # the per-call loop below, whose own cancel branch answers every
            # one of them with CANCELLED_TOOL_RESULT — the history-validity
            # invariant is maintained in exactly one place rather than two.

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
                    result_content: object = unknown_tool_message(tc.name, self.tools)
                    is_error = True
                    logger.warning(
                        "tool call to unknown tool %r (arguments=%r)", tc.name, tc.arguments
                    )
                elif UNPARSED_ARGUMENTS_KEY in tc.arguments:
                    # The provider's own stream ended (almost always
                    # stop_reason=max_tokens) before this call's argument
                    # JSON finished, so tc.arguments is not real arguments —
                    # it's the raw partial string under this one sentinel
                    # key (see providers/base.py's docstring on it).
                    # Running the tool anyway means it goes looking for a
                    # real argument that was never there and raises a bare,
                    # unrelated KeyError — telling the model what actually
                    # happened instead lets it retry with a shorter/split
                    # call rather than repeating the same failing one.
                    result_content = (
                        "This tool call's arguments were cut off by the output token limit "
                        "before they finished streaming, so the call was not run. Shorten the "
                        "content or split it into smaller calls (e.g. write in parts, or a "
                        "shorter report) and try again."
                    )
                    is_error = True
                    logger.warning(
                        "tool call to %s had unparsed arguments (stream likely ended at "
                        "stop_reason=max_tokens): %r",
                        tc.name,
                        tc.arguments[UNPARSED_ARGUMENTS_KEY],
                    )
                else:
                    try:
                        # Weaker local models send "false" for a boolean or
                        # "3" for an integer; without this a native tool's
                        # bool(arguments["overwrite"]) reads "false" as True.
                        # MCP tools are coerced again in McpManager, which
                        # is a no-op on already-coerced arguments.
                        arguments, notes = coerce_arguments(tc.arguments, tool.schema.parameters)
                        if notes:
                            logger.warning(
                                "tool call %s: auto-corrected %d argument(s): %s",
                                tc.name,
                                len(notes),
                                "; ".join(notes),
                            )
                        result = await tool.func(arguments)
                        result_content = result.content
                        is_error = result.is_error
                        result_artifacts = result.artifacts
                    except Exception as exc:  # noqa: BLE001 - a tool crash must not kill the loop
                        # Bare str(exc) is unreadable for exceptions whose
                        # message is just the offending value — a KeyError
                        # from `arguments["path"]` stringifies to `'path'`
                        # alone, telling the model nothing about what went
                        # wrong. Some exceptions also stringify to "" (e.g.
                        # a bare `assert`), which is worse.
                        result_content = f"{type(exc).__name__}: {exc}"
                        is_error = True
                        logger.warning(
                            "tool %s(%r) raised: %s", tc.name, tc.arguments, exc, exc_info=True
                        )

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
                history_content = _stringify(result_content)
                sign = progress.observe(tc.name, tc.arguments, history_content, is_error)
                if sign is not None:
                    logger.info("loop sign (%s) after %s", sign.kind, tc.name)
                    loop_sign = loop_sign or sign
                if self._tool_result_filter is not None:
                    history_content = self._tool_result_filter(tc.name, tc.id, history_content)
                messages.append(
                    Message(
                        role="tool",
                        content=history_content,
                        tool_call_id=tc.id,
                        name=tc.name,
                        images=image_refs,
                    )
                )

            if stop_after_results:
                yield AgentError(layer="core", message=_REPEATED_CUTOFF_MESSAGE)
                return
            if loop_sign is not None and loop_nudges < MAX_LOOP_NUDGES:
                # After the results, so every call stays paired with its
                # result; a short "I'll consolidate" reply to it with no
                # tool call is then caught by the stall check.
                loop_nudges += 1
                yield self._nudge(messages, "loop", loop_sign.message)
            # loop continues: provider gets called again with tool results appended

    def _nudge(self, messages: list[Message], reason: str, text: str) -> TurnNudged:
        """Append ``text`` as a user message and return the event saying so."""
        messages.append(Message(role="user", content=text))
        logger.info("turn continued automatically (%s)", reason)
        return TurnNudged(reason=reason, text=text)


_REPEATED_CUTOFF_MESSAGE = (
    "The model's reply was cut off at the output token limit again, so the turn stopped. "
    "Raise max_tokens for this profile (reasoning models spend part of it on thinking), "
    "or ask for the work in smaller steps."
)


__all__ = [
    "CANCELLED_TOOL_RESULT",
    "DEFAULT_MAX_ITERATIONS",
    "LENGTH_NUDGE",
    "MAX_LENGTH_RECOVERIES",
    "MAX_LOOP_NUDGES",
    "MAX_STALL_NUDGES",
    "STALL_NUDGE",
    "STALL_NUDGE_CALL_IN_REASONING",
    "AgentLoop",
    "has_tool_call_markup",
    "is_stall",
    "looks_like_announced_step",
    "unknown_tool_message",
]
