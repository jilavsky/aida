"""The session engine shared by every frontend: ``ChatSession`` (mutable
per-session state — provider/loop, message history, native tools, the
conversation recorder) and ``start_session`` (the shared startup logic that
resolves a workspace, starts its MCP servers, builds tools/context, and
returns a ready ``ChatSession``).

B8 (planning/improvement_plan_2026-08.md): this used to live in
``aida.cli.chat``, which worked fine for the CLI itself but put
``aida.ui.qt.bridge`` in the position of importing from ``aida.cli`` to get
at it — the one place in the codebase where the intended layering
(``core``/``providers``/``mcp``/``workspace`` -> ``cli``/``ui.qt``, never the
reverse) read backwards. Both ``aida.cli.chat`` and ``aida.ui.qt.bridge`` now
import the engine from here; ``aida.cli.chat`` keeps every one of these names
re-exported (``from aida.core.session import ...`` at its own top) so nothing
that imported them from their old home breaks.

What did *not* move: anything that talks to a real terminal (``print_event``,
the ``input()``-driven REPL loop, ``argparse`` wiring, ``main``) stays in
``aida.cli.chat`` — that is genuinely CLI-frontend code, not session engine.
The one function here that still touches a terminal is ``cli_confirm``
(``start_session``'s default ``confirm_callback`` when a caller doesn't pass
its own) — it has no CLI-framework dependency (no argparse, no REPL state),
just ``asyncio.to_thread(input, ...)``, so it lives with the engine that
needs it as a default rather than forcing every caller to supply one.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.coding.templates import load_templates, templates_context_text
from aida.coding.tools import default_coding_tools
from aida.config.logging_setup import get_logger
from aida.config.paths import (
    artifacts_dir,
    ensure_records_dir,
    ensure_scratch_dir,
    knowledge_db_path,
    skills_dir,
)
from aida.config.secrets import env_var_name, get_secret
from aida.config.settings import McpConfig, McpServerConfig, Settings, WorkspaceConfig
from aida.config.users import (
    resolve_active_user,
    resolve_records_dir_for_user,
    resolve_workspace_for_user,
)
from aida.core.agent import AgentLoop
from aida.core.confirmation import REMEMBERABLE_ACTIONS, ConfirmAnswer, RememberingConfirm
from aida.core.context import (
    DEFAULT_RESERVED_OUTPUT_TOKENS,
    TrimPlan,
    build_coding_context_block,
    build_identity_context_block,
    build_system_message,
    build_workspace_context_block,
    compaction_request_messages,
    compaction_summary_message,
    estimate_message_tokens,
    estimate_tool_schema_tokens,
    history_budget,
    load_skill_texts,
    plan_trim,
    repair_tool_call_pairing,
)
from aida.core.events import (
    AgentError,
    ContextTrimmed,
    FileArtifactCreated,
    ImageArtifactCreated,
    RetrievalPerformed,
    TextFinished,
    UsageInfo,
)
from aida.core.tools import NativeTool, default_native_tools
from aida.documents.figure_tools import OcrBackend, default_figure_tools
from aida.documents.ocr.mistral import SECRET_REF as OCR_SECRET_REF
from aida.documents.tools import default_document_tools
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.retrieval import (
    ActiveKnowledgeBase,
    RetrievedPassage,
    retrieve_from_active_kb,
)
from aida.mcp.groups import resolve_explicit, resolve_group
from aida.mcp.manager import NAMESPACE_SEPARATOR, McpManager
from aida.persistence.recorder import ConversationNotFoundError, ConversationRecorder
from aida.persistence.store import ConversationStore
from aida.providers.base import CompletionSettings, ImageRef, Message
from aida.providers.profiles import (
    UnknownProviderKindError,
    build_embeddings_provider,
    build_provider,
)
from aida.workspace.files import default_file_tools
from aida.workspace.safety import ConfirmationRequest, ConfirmCallback, SafetyGuard
from aida.workspace.web import default_web_tools
from aida.workspace.workspaces import (
    get_workspace,
    resolve_workspace_environment,
    validate_workspace,
)


async def cli_confirm(request: ConfirmationRequest) -> ConfirmAnswer:
    """The raw, interactive ``RawConfirmCallback`` for any terminal-based
    caller (today, only ``aida.cli.chat``): blocks on a real terminal
    prompt via ``asyncio.to_thread(input, ...)`` so it doesn't freeze the
    event loop out from under any concurrently-streaming output. The GUI
    (``aida.ui.qt.bridge.ChatBridge``) always passes its own callback that
    shows a modal dialog instead — see ``start_session``'s docstring.

    Never handed to ``SafetyGuard``/``McpManager`` directly — always
    wrapped in a ``RememberingConfirm`` first (see ``_start_session``),
    which is what turns this tri-state ``ConfirmAnswer`` back into the
    plain bool every ``ConfirmCallback`` consumer expects, and remembers
    an ``ALLOW_FOR_CHAT`` answer for the rest of this process's session."""
    rememberable = request.remember_scope is not None and request.action in REMEMBERABLE_ACTIONS
    suffix = "[y/N/a]" if rememberable else "[y/N]"
    if rememberable:
        prompt = f"\n[confirm] {request.detail} {suffix} (a = allow for the rest of this chat) "
    else:
        prompt = f"\n[confirm] {request.detail} {suffix} "
    answer = await asyncio.to_thread(input, prompt)
    normalized = answer.strip().lower()
    if rememberable and normalized in ("a", "always"):
        return ConfirmAnswer.ALLOW_FOR_CHAT
    if normalized in ("y", "yes"):
        return ConfirmAnswer.ALLOW_ONCE
    return ConfirmAnswer.DENY


logger = get_logger("session")


class UnknownMcpServerError(Exception):
    """Raised when ``--mcp`` names a server that isn't in ``mcp.json``."""


class UnknownProfileError(Exception):
    """Raised when ``--profile``/``/profile`` names a profile that isn't
    configured in ``providers.yaml``."""


class UnknownWorkspaceError(Exception):
    """Raised when ``--workspace`` names a workspace that isn't in
    ``workspaces.yaml``."""


class SessionBusyError(Exception):
    """Raised when a second mutation of a session's state is attempted while
    one is already in flight.

    ``ChatSession`` owns three operations that rewrite the same state — a
    turn (``send``, which appends to ``self.messages`` and streams from
    ``self.provider``), manual compaction (``compact_now``, which *replaces*
    ``self.messages`` wholesale after awaiting a summarization call), and a
    profile switch (``switch_profile``, which swaps out the very provider
    the running turn is streaming from). Any two of them overlapping
    corrupts something: compaction computes its plan, awaits a summary, and
    then applies a slice assignment based on a message list the turn has
    since appended to — silently discarding those messages; a switch closes
    a provider mid-stream.

    The GUI disables the controls that reach these while a turn runs, but
    the invariant is enforced here rather than only there, because the CLI
    and any future caller bypass the GUI entirely. Refusing is deliberately
    preferred over queueing: a compaction or profile switch that silently
    took effect several minutes later, after the turn the user was watching
    finished, would be a worse surprise than being told "not right now"."""


def resolve_mcp_servers(
    mcp_config: McpConfig, *, group: str | None, names: list[str] | None
) -> list[McpServerConfig]:
    """Precedence: an explicit ``--mcp server1,server2`` list wins over
    ``--mcp-group NAME``; if neither is given, no MCP servers are enabled
    (lazy start — configuring a server in ``mcp.json`` never launches it by
    itself). Raises ``UnknownMcpServerError`` naming any typo'd server."""
    if names:
        try:
            return resolve_explicit(mcp_config, names)
        except ValueError as exc:
            raise UnknownMcpServerError(str(exc)) from exc
    if group:
        return resolve_group(mcp_config, group)
    return []


def resolve_profile(settings: Settings, name: str):
    """Look up a profile by name, raising a clear error naming what's
    available — never a bare KeyError."""
    profile = settings.providers.profiles.get(name)
    if profile is None:
        available = ", ".join(sorted(settings.providers.profiles)) or "(none configured)"
        raise UnknownProfileError(f"Unknown profile {name!r}. Configured profiles: {available}")
    return profile


def _completion_settings_for_profile(profile) -> CompletionSettings:
    """Build the ``CompletionSettings`` for one provider request from a
    profile (B2). Shared by ``ChatSession.__init__`` and ``switch_profile``
    so the two construction sites can't drift.

    A profile with no ``temperature`` set now sends *no* temperature rather
    than falling back to a made-up 0.7 — see ``CompletionSettings``. A
    profile that wants a specific one still says so and gets it.
    ``max_tokens`` keeps its fallback: unlike temperature, a request with
    no token cap is not something every endpoint accepts."""
    kwargs: dict[str, object] = {
        "model": profile.model,
        "supports_vision": profile.supports_vision,
        "temperature": profile.temperature,
    }
    if profile.max_tokens is not None:
        kwargs["max_tokens"] = profile.max_tokens
    return CompletionSettings(**kwargs)


def _format_retrieved_context(passages_by_kb: dict[str, list[RetrievedPassage]]) -> str:
    """Render retrieved passages as the text injected into the model's
    context for one turn — grouped by knowledge base, each passage tagged
    with its source file, section heading, and score so the model can (and
    should) cite where an answer came from."""
    lines = ["# Retrieved context for this question"]
    for kb_name, passages in passages_by_kb.items():
        lines.append("")
        lines.append(f"## From knowledge base: {kb_name}")
        for passage in passages:
            heading_suffix = f" — {passage.heading}" if passage.heading else ""
            lines.append("")
            lines.append(
                f"### {Path(passage.source_path).name}{heading_suffix} (score {passage.score:.2f})"
            )
            lines.append(passage.text)
    return "\n".join(lines)


class ChatSession:
    """Holds the mutable state of one chat session: current provider/loop,
    message history, native tools, and (Phase 4) the recorder persisting
    everything to SQLite + a Markdown transcript. Mid-session ``/profile``
    switches swap the provider + loop but keep ``messages`` (and thus
    history)."""

    def __init__(
        self,
        settings: Settings,
        profile_name: str,
        tools: dict[str, NativeTool] | None = None,
        skill_names: list[str] | None = None,
        system_prompt: str | None = None,
        recorder: ConversationRecorder | None = None,
        initial_messages: list[Message] | None = None,
        extra_context_texts: list[str] | None = None,
        active_knowledge_bases: list[ActiveKnowledgeBase] | None = None,
        identity_text: str | None = None,
    ) -> None:
        self.settings = settings
        self.tools = tools if tools is not None else default_native_tools()
        self.profile_name = profile_name
        self.profile = resolve_profile(settings, profile_name)
        self.provider = build_provider(self.profile)
        self.completion_settings = _completion_settings_for_profile(self.profile)
        self.loop = AgentLoop(
            self.provider,
            self.completion_settings,
            self.tools,
            max_iterations=settings.app.max_agent_iterations,
        )
        self.recorder = recorder
        # Phase 8 (RAG): resolved once at session-start (start_session),
        # queried fresh every turn in send() — retrieval is query-dependent,
        # unlike skills/MCP-instructions text, which is why it can't reuse
        # extra_context_texts (built once, before any user message exists).
        # Empty for a workspace with no knowledge_bases configured — zero
        # retrieval calls, zero cost.
        self.active_knowledge_bases = active_knowledge_bases or []
        # Bug report: "Can we get cost estimate... token use may be
        # better... at this moment it is a black box." Cumulative across
        # the whole session (including across /profile switches, which
        # deliberately don't reset it — the model may change but the
        # user's own running total shouldn't). See aida.core.cost.
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # Serializes the three operations that mutate this session's state —
        # see SessionBusyError. Held for the whole of send()/compact_now()/
        # switch_profile(); the internal helpers they call (_trim_context,
        # _apply_trim_plan, ...) deliberately do *not* take it, since they
        # only ever run underneath one of those three.
        self._mutation_lock = asyncio.Lock()
        # Bumped every time self.messages is structurally rewritten or
        # appended to on behalf of a turn. _apply_trim_plan captures it
        # before awaiting summarization and re-checks it before the slice
        # assignment: a belt-and-braces assertion that the plan it is about
        # to apply still describes the list it was computed from. The lock
        # above is what actually prevents the race; this is what would catch
        # a future refactor that reintroduces it.
        self._history_generation = 0

        skill_texts = load_skill_texts(skills_dir(), skill_names or [])
        system_message = build_system_message(
            system_prompt, skill_texts, extra_texts=extra_context_texts, identity_text=identity_text
        )
        # Resumed history can arrive already broken: a session killed
        # (crash, force-quit) after an assistant message with tool calls was
        # persisted but before its results were leaves orphaned tool_use
        # blocks, which every later turn's request is then rejected for.
        # Repaired once here, on the way in — see
        # aida.core.context.repair_tool_call_pairing.
        history = repair_tool_call_pairing(list(initial_messages)) if initial_messages else []
        self.messages: list[Message] = (
            [system_message] if system_message.content else []
        ) + history

    @property
    def is_mutating(self) -> bool:
        """Whether a turn, a compaction, or a profile switch is in flight.

        Exposed so a frontend can disable the controls that would raise
        ``SessionBusyError`` instead of letting the user press them and get
        an error back."""
        return self._mutation_lock.locked()

    async def switch_profile(self, name: str) -> None:
        """Swap provider + loop to another configured profile, keeping the
        conversation history.

        Two things about the ordering here are load-bearing, and both were
        wrong before:

        *Nothing* is assigned to ``self`` until every fallible step has
        already succeeded. The old version set ``self.profile`` and
        ``self.profile_name`` and *then* called ``build_provider``, so a
        profile with a typo'd ``kind:`` (``UnknownProviderKindError``), a
        missing secret, or any other client-construction failure left the
        session advertising the new profile's name while still holding the
        old provider and loop — a half-switched state the failure handlers
        explicitly assumed could not exist.

        And the switch is refused outright while a turn is running. The old
        version closed ``old_provider`` unconditionally, which on a switch
        made mid-stream closed the very client the live ``AgentLoop`` was
        reading from.
        """
        if self._mutation_lock.locked():
            raise SessionBusyError(
                "Can't switch profile while a turn is running — stop it or wait for it to finish."
            )
        async with self._mutation_lock:
            # Every fallible step first, into locals. resolve_profile raises
            # UnknownProfileError for an unknown name; build_provider raises
            # UnknownProviderKindError (and whatever the underlying client
            # constructor raises) for a configured-but-unbuildable one.
            new_profile = resolve_profile(self.settings, name)
            new_completion_settings = _completion_settings_for_profile(new_profile)
            new_provider = build_provider(new_profile)
            # Carry over the iteration cap already in effect (e.g. set
            # moments earlier via /max-iterations) rather than resetting it
            # to settings.app.max_agent_iterations — a /profile switch
            # shouldn't silently discard a value the user just set in the
            # same session.
            new_loop = AgentLoop(
                new_provider,
                new_completion_settings,
                self.tools,
                max_iterations=self.loop.max_iterations,
            )

            # Nothing below here can fail, so the swap is all-or-nothing.
            old_provider = self.provider
            self.profile = new_profile
            self.profile_name = name
            self.provider = new_provider
            self.completion_settings = new_completion_settings
            self.loop = new_loop
            # self.messages is intentionally left untouched: history carries over.

            # Last, and only now that nothing else refers to it. A provider
            # whose aclose() fails must not roll the switch back — the new
            # provider is already live and correct at this point — so the
            # failure is logged rather than raised.
            try:
                await old_provider.aclose()
            except Exception as exc:  # noqa: BLE001 - see above
                logger.warning(
                    "closing the previous provider after a profile switch failed: %s", exc
                )

    def cancel(self) -> None:
        self.loop.cancel()

    def queue_user_message(self, text: str) -> None:
        """Deliver ``text`` to the *running* turn at its next round trip —
        see ``AgentLoop.queue_user_message``. A plain synchronous call, like
        ``cancel()``: safe from any thread, no scheduling onto the loop."""
        self.loop.queue_user_message(text)

    def take_undelivered_messages(self) -> list[str]:
        """Queued interjections the turn ended before reaching — see
        ``AgentLoop.take_undelivered_messages``."""
        return self.loop.take_undelivered_messages()

    def _history_budget(self) -> int | None:
        """The token budget for *sent* history right now — per-profile
        ``context_window`` (PLAN.md §1.3) falling back to the global
        ``AppConfig.max_context_tokens``, exactly as before this existed for
        a profile that never sets it. ``None`` means trimming/compaction is
        disabled entirely (the global default is ``0``, same meaning it
        always had)."""
        context_window = self.profile.context_window or self.settings.app.max_context_tokens
        if not context_window:
            return None
        reserved_output_tokens = (
            self.profile.max_tokens
            if self.profile.max_tokens is not None
            else DEFAULT_RESERVED_OUTPUT_TOKENS
        )
        tool_schema_tokens = estimate_tool_schema_tokens(
            [tool.schema for tool in self.tools.values()]
        )
        return history_budget(
            context_window=context_window,
            reserved_output_tokens=reserved_output_tokens,
            tool_schema_tokens=tool_schema_tokens,
        )

    def context_fullness(self) -> tuple[int, int]:
        """``(estimated tokens the next request would send, the usable
        history budget it's measured against)`` — planning/
        context_management.md §3.5's fullness indicator ("Context: 42k /
        88k (48%)"), computed the exact same way ``_trim_context`` computes
        its own budget so the two can never disagree. The second element is
        ``0`` when trimming/compaction is disabled (``0`` budget) — a
        caller displaying a percentage should treat that as "not
        applicable", not divide by it."""
        current = sum(estimate_message_tokens(m) for m in self.messages)
        return current, (self._history_budget() or 0)

    async def _compact_context(self, turns: list[list[Message]]) -> Message | None:
        """Summarize ``turns`` (whole turns about to be dropped, oldest
        first) via the *active* provider — no new provider API needed, see
        ``compaction_request_messages``. Returns the replacement summary
        ``Message``, or ``None`` if summarization didn't produce one (an
        ``AgentError`` from the provider, an empty reply, or an outright
        exception) — callers must fall back to plain dropping in that case.
        Compaction failing must never fail the user's turn, so nothing here
        raises."""
        if not turns:
            return None
        request_messages = compaction_request_messages(turns)
        # Low temperature, no tools: this is a factual-extraction request,
        # not a creative turn, and the summarizer must never itself try to
        # call a tool. Reuses the active profile/model rather than a
        # separate "utility profile" — see context_management.md §6.
        summary_settings = CompletionSettings(
            model=self.completion_settings.model,
            # Only pin a low temperature if the active profile shows this
            # endpoint takes one at all — a model that fixes temperature at
            # its own default rejects 0.0 exactly as it rejects 0.7, and a
            # failed compaction silently costs the user their history.
            temperature=0.0 if self.completion_settings.temperature is not None else None,
            supports_vision=False,
        )
        try:
            summary_text = ""
            for_error: str | None = None
            async for event in self.provider.complete(request_messages, [], summary_settings):
                if isinstance(event, TextFinished):
                    summary_text = event.text
                elif isinstance(event, AgentError):
                    for_error = f"{event.layer}: {event.message}"
            if for_error is not None:
                logger.warning(
                    "context compaction failed, falling back to plain trim: %s", for_error
                )
                return None
            if not summary_text.strip():
                logger.warning(
                    "context compaction produced an empty summary, falling back to plain trim"
                )
                return None
            return compaction_summary_message(summary_text)
        except Exception as exc:  # noqa: BLE001 - compaction failing must never fail the turn
            logger.warning("context compaction failed, falling back to plain trim: %s", exc)
            return None

    async def _apply_trim_plan(self, plan: TrimPlan) -> ContextTrimmed | None:
        """Shared by the automatic (``_trim_context``) and manual
        (``compact_now``) paths: try to summarize ``plan.dropped_turns``
        (PLAN.md §1.3 compaction) and fall back to plain-discarding them if
        summarization fails, then mutate ``self.messages`` in place (because
        ``AgentLoop.run`` appends to that same list object) and report what
        actually happened."""
        # Captured *before* the await below. `plan` describes exact slices
        # of self.messages; if anything appended to or rewrote that list
        # while summarization was in flight, the slice assignment at the end
        # of this method would throw those messages away. ChatSession's
        # mutation lock is what prevents that from happening at all — this
        # check is the assertion that says so out loud, and would catch a
        # future caller that reaches _apply_trim_plan without holding it.
        generation = self._history_generation
        summary_message = await self._compact_context(plan.dropped_turns)
        if generation != self._history_generation:
            logger.warning(
                "conversation history changed while compaction was summarizing "
                "(generation %d -> %d); discarding the stale plan rather than applying it",
                generation,
                self._history_generation,
            )
            return None
        if summary_message is not None:
            new_messages = [*plan.system_messages, summary_message, *plan.kept_turn_messages]
            summarized = True
            summary_tokens = estimate_message_tokens(summary_message)
        else:
            new_messages = plan.kept_messages
            summarized = False
            summary_tokens = 0

        dropped_turns = len(plan.dropped_turns)
        estimated_tokens = sum(estimate_message_tokens(m) for m in new_messages)
        logger.info(
            "%s conversation history to fit ~%d tokens: %d message(s) -> %d (%d turn(s) %s)",
            "compacted" if summarized else "trimmed",
            estimated_tokens,
            len(self.messages),
            len(new_messages),
            dropped_turns,
            "summarized" if summarized else "dropped",
        )
        self.messages[:] = new_messages
        self._history_generation += 1
        return ContextTrimmed(
            dropped_turns=dropped_turns,
            estimated_tokens=estimated_tokens,
            summarized=summarized,
            summary_tokens=summary_tokens,
        )

    async def _trim_context(self) -> ContextTrimmed | None:
        """Drop (or, PLAN.md §1.3, summarize) the oldest whole turns from
        the in-memory history once it exceeds the active budget — see
        ``_history_budget``.

        Nothing managed context size before this existed at all:
        ``self.messages`` grew for the whole session until the provider
        rejected a request for length, mid-analysis, with no way back short
        of starting over.

        B7: trimming used to be invisible outside a log line — returns a
        ``ContextTrimmed`` event (for ``send()`` to yield to the frontend)
        when it actually changed something, ``None`` otherwise (disabled,
        or already under budget)."""
        budget = self._history_budget()
        if budget is None:
            return None  # 0 (global default or resolved profile window) disables trimming
        plan = plan_trim(self.messages, budget)
        if not plan.was_trimmed:
            return None
        return await self._apply_trim_plan(plan)

    async def compact_now(self, *, min_recent_turns: int = 4) -> ContextTrimmed | None:
        """Manual compaction — the CLI's ``/compact`` and the GUI's
        "Compact Conversation" action (planning/context_management.md §3.4):
        summarize everything but the most recent ``min_recent_turns`` turns
        right now, regardless of whether the budget is currently exceeded,
        so the user can compact at a natural task boundary instead of
        waiting for it to trigger automatically mid-thought. Shares
        ``_apply_trim_plan`` with the automatic path — same summarize/
        fall-back-to-drop behavior, just with the trim threshold forced to
        ``0`` so ``plan_trim`` always finds something to drop, short of the
        ``min_recent_turns`` floor. Returns ``None`` if there's nothing to
        compact (fewer turns than the floor).

        Raises ``SessionBusyError`` if a turn or a profile switch is in
        flight. Compaction computes a plan from ``self.messages``, awaits a
        summarization round trip, and then *replaces the whole list*; run
        concurrently with a turn, that final assignment is made from a plan
        that predates every assistant and tool message the turn appended in
        the meantime, silently discarding them. It would also put a second
        concurrent request through the one provider instance."""
        if self._mutation_lock.locked():
            raise SessionBusyError(
                "Can't compact while a turn is running — stop it or wait for it to finish."
            )
        async with self._mutation_lock:
            plan = plan_trim(self.messages, 0, min_recent_turns=min_recent_turns)
            if not plan.was_trimmed:
                return None
            return await self._apply_trim_plan(plan)

    async def _retrieve_context(self, user_text: str) -> dict[str, list[RetrievedPassage]]:
        """Query every active knowledge base for this turn's question.
        A knowledge base that fails (a stale embedding-profile mismatch
        after someone edited providers.yaml, a transient network error, ...)
        is logged and skipped rather than raised — the same "one bad X must
        not take down the whole Y" reasoning already applied to a bad MCP
        server or a bad file in an ingest pass. A knowledge base that
        returns nothing above its score threshold contributes no entry."""
        passages_by_kb: dict[str, list[RetrievedPassage]] = {}
        for kb in self.active_knowledge_bases:
            try:
                passages = await retrieve_from_active_kb(kb, user_text)
            except Exception as exc:  # noqa: BLE001 - see docstring
                logger.warning("knowledge base %r retrieval failed: %s", kb.name, exc)
                continue
            if passages:
                passages_by_kb[kb.name] = passages
        return passages_by_kb

    def _persist_new_messages(self, persisted: int, context_message: Message | None) -> int:
        """Record every message appended to ``self.messages`` since index
        ``persisted``, skipping ``context_message`` by identity — the
        retrieved-context message must never reach the DB (see ``send()``'s
        docstring: it's ephemeral, visible to the model for this turn only).
        Returns the new ``persisted`` index. No-op (returns unchanged) with
        no recorder."""
        if self.recorder is None:
            return persisted
        while persisted < len(self.messages):
            message = self.messages[persisted]
            if message is not context_message:
                self.recorder.record_message(message)
            persisted += 1
        return persisted

    async def send(
        self,
        user_text: str,
        *,
        images: list[ImageRef] | None = None,
        attachment_paths: list[str] | None = None,
        attachment_texts: dict[str, str] | None = None,
    ):
        """Run one turn, holding the session's mutation lock for its whole
        duration.

        A thin wrapper around ``_run_turn``, which carries the actual logic.
        Splitting them keeps the lock acquisition readable as one line
        instead of re-indenting the entire turn body, and — because this is
        an async *generator* — guarantees the lock is released whether the
        turn drains naturally, raises, or is abandoned by a consumer that
        stops iterating (``aclose()`` throws ``GeneratorExit`` in at the
        ``yield``, which unwinds the ``async with`` exactly like any other
        exception).

        Raises ``SessionBusyError`` if another turn, a compaction, or a
        profile switch is already in flight — see ``SessionBusyError``. Note
        that a *queued interjection* into a running turn is a different
        thing entirely and still works: it goes through the synchronous
        ``queue_user_message``, which never touches this lock."""
        if self._mutation_lock.locked():
            raise SessionBusyError("A turn is already running in this session.")
        async with self._mutation_lock:
            async for event in self._run_turn(
                user_text,
                images=images,
                attachment_paths=attachment_paths,
                attachment_texts=attachment_texts,
            ):
                yield event

    async def _run_turn(
        self,
        user_text: str,
        *,
        images: list[ImageRef] | None = None,
        attachment_paths: list[str] | None = None,
        attachment_texts: dict[str, str] | None = None,
    ):
        """Run one turn. Phase 8 (RAG): if any knowledge bases are active,
        retrieves passages for ``user_text`` and injects them as a
        *strictly ephemeral* extra message — appended to ``self.messages``
        (so the model sees it this turn, via the same list ``AgentLoop.run``
        mutates and the recorder watches) but removed again once the turn
        ends, and explicitly excluded from persistence throughout. This
        keeps ``self.messages`` the one canonical mutable list without ever
        writing stale retrieved context to the DB or re-sending it next
        turn with a different question.

        ``images`` (B1): GUI image attachments — additive, keyword-only,
        defaults to none so the CLI's plain ``session.send(line)`` call is
        unaffected. Whether they actually reach the model as vision input
        this turn still depends on the active profile's ``supports_vision``
        and the recency cap — see ``aida.providers.vision``.

        ``attachment_paths``: files a *person* attached to this message, as
        paths on their machine. Kept as copies in the conversation's own
        folder so they outlive the original being moved or cleaned up, and
        so the Markdown transcript is complete — see
        ``aida.documents.attachments``. Deliberately separate from
        ``images``, which are about what the *model* sees this turn; these
        are about what the *conversation* keeps. Never passed for a file the
        agent opened itself: those already live where the user put them."""
        user_message = Message(role="user", content=user_text, images=list(images or []))
        self.messages.append(user_message)
        self._history_generation += 1  # see __init__ / _apply_trim_plan
        if self.recorder is not None:
            self.recorder.record_message(user_message)
            if attachment_paths:
                # After the message is recorded, never before: the copy is
                # bookkeeping, and a failure in it must not cost the user
                # the turn they just sent. keep_attachments never raises.
                # The extracted text is written beside the copy, so the
                # folder shows what the model actually received rather than
                # only the file it came from. Passed in rather than
                # re-derived: the caller already parsed the document to
                # build this message, and parsing a 150-page PDF twice on
                # the turn it arrived on would be felt.
                self.recorder.keep_attachments(
                    list(attachment_paths), texts=attachment_texts or None
                )
        # Keep the *sent* history under budget (the recorded history in the
        # DB is never trimmed — resume/export still show everything). Done
        # here, before `persisted` is captured, because trimming shifts
        # every index in self.messages. Whole turns only, so a tool result
        # is never separated from the call it answers — see trim_history.
        trim_event = await self._trim_context()
        if trim_event is not None:
            yield trim_event
        persisted = len(self.messages)

        context_message: Message | None = None
        if self.active_knowledge_bases:
            passages_by_kb = await self._retrieve_context(user_text)
            if passages_by_kb:
                yield RetrievalPerformed(
                    passages_by_kb={
                        kb_name: [
                            {
                                "text": p.text,
                                "source_path": p.source_path,
                                "heading": p.heading,
                                "score": p.score,
                            }
                            for p in passages
                        ]
                        for kb_name, passages in passages_by_kb.items()
                    }
                )
                context_message = Message(
                    role="user", content=_format_retrieved_context(passages_by_kb)
                )
                self.messages.append(context_message)

        try:
            async for event in self.loop.run(self.messages):
                yield event
                if isinstance(event, UsageInfo):
                    self.total_input_tokens += event.input_tokens
                    self.total_output_tokens += event.output_tokens
                # Persist each finalized message the instant it lands in
                # self.messages (agent.py appends assistant/tool messages
                # in-place as the turn progresses) — this is the "crash-safe
                # enough" write path: everything up to the currently-streaming
                # text is already durable. See aida.persistence.recorder's
                # module docstring for the full trade-off.
                persisted = self._persist_new_messages(persisted, context_message)
                if self.recorder is None:
                    continue
                if isinstance(event, ImageArtifactCreated):
                    self.recorder.record_artifact_fields(
                        artifact_id=event.artifact_id,
                        kind="ImageArtifact",
                        path=event.path,
                        mime_type=event.mime_type,
                        call_id=event.call_id,
                        # U6(b): the tool-result message this artifact
                        # belongs with hasn't been persisted yet at this
                        # point (agent.py yields ImageArtifactCreated/
                        # FileArtifactCreated before appending that
                        # message — see AgentLoop.run) — next_message_seq()
                        # is exactly the seq it's about to receive, so the
                        # GUI resume path can interleave this artifact
                        # right after that message.
                        message_seq=self.recorder.next_message_seq(),
                    )
                elif isinstance(event, FileArtifactCreated):
                    self.recorder.record_artifact_fields(
                        artifact_id=event.artifact_id,
                        kind="FileArtifact",
                        path=event.path,
                        mime_type=event.mime_type,
                        call_id=event.call_id,
                        message_seq=self.recorder.next_message_seq(),
                    )

            # A turn's final assistant message (the one that ends it with no
            # further tool call) is appended to self.messages by AgentLoop right
            # before its generator returns — with no event emitted afterward to
            # trigger the catch-up loop above, so it would otherwise never get
            # persisted. This only runs if the `async for` above drains
            # naturally (turn completed); a caller that stops consuming early
            # (or a real process killed mid-stream) never reaches it, which is
            # exactly the intended "crash-safe enough" boundary — see
            # aida.persistence.recorder's module docstring.
            persisted = self._persist_new_messages(persisted, context_message)
        finally:
            # Ephemeral: never persisted (guarded above by identity), and
            # never allowed to accumulate into next turn's context either —
            # each turn embeds a fresh query and gets fresh passages.
            # Removed *by identity*, matching how persistence excludes it:
            # list.remove() matches by ==, and Message is a plain dataclass,
            # so an ordinary user message that happened to have identical
            # field values would be the one deleted instead.
            if context_message is not None:
                for position in range(len(self.messages) - 1, -1, -1):
                    if self.messages[position] is context_message:
                        del self.messages[position]
                        break
            # One transcript write per turn instead of one per message —
            # see ConversationRecorder.record_message. Suppressed because a
            # failed *export* must not be what breaks a turn: the messages
            # themselves are already durable in the DB, and the next flush
            # (or session close) writes the file again from scratch anyway.
            if self.recorder is not None:
                with contextlib.suppress(Exception):
                    self.recorder.flush_transcript()

    async def aclose(self) -> None:
        """Release the current provider's connections, every active
        knowledge base's connection/embeddings-provider, and the
        recorder's DB connection. Must be called when the session ends
        (see ``_run_repl``'s try/finally) — otherwise ``asyncio.run()`` can
        close the event loop out from under an open HTTP client and print a
        spurious traceback on exit."""
        await self.provider.aclose()
        for kb in self.active_knowledge_bases:
            with contextlib.suppress(Exception):  # best-effort cleanup on the way out
                await kb.embeddings_provider.aclose()
            with contextlib.suppress(Exception):
                kb.connection.close()
        if self.recorder is not None:
            # Settle any transcript write deferred by the recorder's rate
            # limit before the DB connection it reads from goes away.
            with contextlib.suppress(Exception):
                self.recorder.flush_transcript()
            self.recorder.store.close()


def _ensure_workspace_folders(workspace: WorkspaceConfig) -> None:
    """ "Can we create the folders if they do not exist? I need to populate
    them at some point" (bug report): source/target folders previously only
    ever *warned* when missing (``validate_workspace``) — a freshly-created
    workspace pointed at folders that don't exist yet made starting a
    session with it awkward, since the user had to go create each one by
    hand first, outside AIDA, before it would stop warning. Creates each one
    (parents included) now, on every session start, so a workspace is usable
    the moment it's defined. A creation failure (permissions, a path that
    collides with an existing file, a not-yet-mounted network drive, ...)
    only warns — same "don't crash on a folder problem" policy as
    ``validate_workspace`` — since ``SafetyGuard``'s own reachability
    checks still apply on top of this either way.

    One asymmetry, deliberate: a **source** folder is only created when its
    parent already exists, while a target folder is created with parents.
    A source folder is where the user's data already lives, so a missing
    one usually means a network share isn't mounted yet (PLAN.md §5:
    "Source folders may be network mounts... treat slow/missing mounts
    gracefully") — and `mkdir(parents=True)` on `/Volumes/data/RUN_2026_08`
    with the share offline would fabricate the whole mount path as empty
    local directories, which then shadows the real mount point and makes
    the agent report an empty data folder instead of "not mounted". A
    target folder has no such meaning: it's an output location the user
    named, and creating it (parents included) is exactly the convenience
    the original request asked for."""
    creations = [(folder, False) for folder in workspace.source_folders]
    creations.append((workspace.target_folder, True))
    for folder, with_parents in creations:
        if not folder:
            continue
        path = Path(folder).expanduser()
        if path.exists():
            continue
        if not with_parents and not path.parent.exists():
            print(
                f"[workspace] warning: source folder {path} does not exist and neither does its "
                "parent — not creating it (an unmounted network share?)"
            )
            logger.warning("source folder %s missing along with its parent — not created", path)
            continue
        try:
            path.mkdir(parents=with_parents, exist_ok=True)
            print(f"[workspace] created folder: {path}")
            logger.info("created workspace folder: %s", path)
        except OSError as exc:
            print(f"[workspace] warning: could not create folder {path}: {exc}")
            logger.warning("could not create workspace folder %s: %s", path, exc)


async def start_session(
    settings: Settings,
    *,
    profile_name: str | None = None,
    workspace_name: str | None = None,
    skill_names: list[str] | None = None,
    mcp_group: str = "",
    mcp_names: list[str] | None = None,
    resume_conversation_id: str | None = None,
    confirm_callback: ConfirmCallback | None = None,
    origin: str | None = None,
    user: str | None = None,
) -> tuple[ChatSession, McpManager | None]:
    """Start a session, releasing everything it acquired if any step fails.

    All the work is in ``_start_session``; this wrapper exists for the
    ``AsyncExitStack``. Startup acquires four kinds of resource that must be
    released if they are not handed to a ``ChatSession``: the SQLite store,
    each knowledge base's connection *and* its embeddings HTTP client, and
    the MCP servers, which are real subprocesses. Cleanup used to be a
    single ``try`` placed around ``ChatSession(...)`` alone, several hundred
    lines after the first of those was opened — so a failure in between (a
    locked database, a corrupt history, an ``McpManager.start_all`` error
    that was not the per-server ``McpServerError`` it isolates) left MCP
    subprocesses running for the life of the process and connections open,
    from a session that reported a clean startup error and looked shut down.

    Registering each release with the stack immediately after the matching
    acquisition means the ordering can no longer drift as this function
    grows. ``pop_all()`` on success is what transfers ownership: nothing is
    closed once a ``ChatSession`` exists to own it.
    """
    async with contextlib.AsyncExitStack() as stack:
        session, mcp_manager = await _start_session(
            stack,
            settings,
            profile_name=profile_name,
            workspace_name=workspace_name,
            skill_names=skill_names,
            mcp_group=mcp_group,
            mcp_names=mcp_names,
            resume_conversation_id=resume_conversation_id,
            confirm_callback=confirm_callback,
            origin=origin,
            user=user,
        )
        # Construction succeeded: the session owns these now, so unregister
        # every cleanup rather than running it on the way out of the `async
        # with`.
        stack.pop_all()
        return session, mcp_manager


async def _start_session(
    stack: contextlib.AsyncExitStack,
    settings: Settings,
    *,
    profile_name: str | None = None,
    workspace_name: str | None = None,
    skill_names: list[str] | None = None,
    mcp_group: str = "",
    mcp_names: list[str] | None = None,
    resume_conversation_id: str | None = None,
    confirm_callback: ConfirmCallback | None = None,
    origin: str | None = None,
    user: str | None = None,
) -> tuple[ChatSession, McpManager | None]:
    """Shared session-startup logic for ``aida chat`` and
    ``aida conversations resume`` — resolves the workspace (if any), starts
    only the MCP servers that end up enabled, builds the
    ``ConversationRecorder`` (fresh or resumed), and returns a ready
    ``ChatSession``. Raises ``UnknownProfileError`` / ``UnknownWorkspaceError``
    / ``UnknownMcpServerError`` / ``ConversationNotFoundError`` — callers
    print and exit(1) rather than letting a traceback through.

    ``confirm_callback`` (Phase 6) is handed to the ``SafetyGuard`` built
    below for the native file/document tools; ``None`` (the CLI's default)
    means ``cli_confirm`` — a real blocking terminal prompt. The GUI
    (``aida.ui.qt.bridge.ChatBridge``) always passes its own callback that
    shows a modal dialog instead.

    ``origin`` (Phase 10) tags a brand-new conversation's row so the GUI
    sidebar and ``aida conversations list`` can tell an interactive chat
    apart from one ``aida.core.workflows.run_workflow`` created —
    ``None`` (every existing caller) means an ordinary interactive
    conversation, unchanged. Ignored on the resume path: an existing
    conversation's origin was already decided at creation time.
    """
    logger.debug(
        "start_session(profile_name=%r, workspace_name=%r, skill_names=%r, mcp_group=%r, "
        "mcp_names=%r, resume_conversation_id=%r)",
        profile_name,
        workspace_name,
        skill_names,
        mcp_group,
        mcp_names,
        resume_conversation_id,
    )
    # Wrapping only the *default* (cli_confirm) in RememberingConfirm here —
    # an explicitly-passed confirm_callback (the GUI's already-wrapped
    # instance, a test's own bool stub, build_headless_confirm_callback(...))
    # is used exactly as given, so "Allow for this chat" plumbing never
    # leaks into headless/test callers that never intended tri-state
    # semantics. One fresh RememberingConfirm per _start_session call means
    # its remembered-approvals set lives exactly as long as this session.
    confirm_callback = confirm_callback or RememberingConfirm(cli_confirm)
    store = ConversationStore()
    # Registered here, not at each `raise` below: every early exit from this
    # point on releases it, including ones added later that would otherwise
    # forget to.
    stack.callback(store.close)
    artifact_store = ArtifactStore()
    # Resolved once, here, and threaded through everything below: an
    # explicit argument beats $AIDA_USER beats config.yaml's active_user.
    # Empty string means "no user", which is every install that has not
    # opted in and every code path that predates this.
    active_user = resolve_active_user(user, app_config=settings.app)
    records_dir = ensure_records_dir(
        Path(resolve_records_dir_for_user(settings.app.records_dir, active_user))
        if settings.app.records_dir
        else None
    )

    resumed_summary = None
    if resume_conversation_id:
        resumed_summary = store.get_conversation(resume_conversation_id)
        if resumed_summary is None:
            raise ConversationNotFoundError(f"no conversation with id {resume_conversation_id!r}")

    effective_workspace_name = workspace_name or (
        resumed_summary.workspace_name if resumed_summary else None
    )
    effective_profile_name = profile_name or (
        resumed_summary.profile_name if resumed_summary else None
    )
    sidecar_dirname = resumed_summary.sidecar_dirname if resumed_summary else "figures"

    # B15: global (workspace-independent) identity/user framing — see
    # build_identity_context_block's docstring for why this is kept
    # separate from extra_context_texts rather than folded in (ordering:
    # it must land ahead of a workspace's own system_prompt, not after it).
    # Per-user framing where it exists, the install-wide text otherwise —
    # see AppConfig.context_for_user for why it falls back rather than
    # replaces. build_identity_context_block itself needed no change.
    identity_text = build_identity_context_block(
        assistant_name=settings.app.assistant_name,
        user_context=settings.app.context_for_user(active_user),
    )

    all_skill_names = list(skill_names or [])
    system_prompt: str | None = None
    mcp_servers: list[McpServerConfig] = []
    explicit_mcp = bool(mcp_group or mcp_names)
    workspace: WorkspaceConfig | None = None

    if effective_workspace_name:
        workspace = get_workspace(settings, effective_workspace_name)
        if workspace is None:
            raise UnknownWorkspaceError(
                f"Unknown workspace {effective_workspace_name!r}. "
                f"Configured workspaces: {', '.join(sorted(settings.workspaces.workspaces)) or '(none)'}"
            )
        # {user} expansion happens HERE, before anything reads this
        # workspace's folders: validate_workspace checks they exist,
        # _ensure_workspace_folders creates them, SafetyGuard.for_workspace
        # turns them into allowed roots, and build_workspace_context_block
        # tells the model about them. A guard built from an unexpanded path
        # would hold a literal ".../{user}/" root, so every write into the
        # real folder would read as outside the workspace and prompt —
        # which looks like a broken safety model rather than a broken path.
        # Returns the same object untouched when the workspace has no
        # placeholder in it, which is every pre-existing configuration.
        workspace = resolve_workspace_for_user(workspace, active_user)
        validation = validate_workspace(settings, workspace)
        for warning in validation.warnings:
            print(f"[workspace] warning: {warning}")
            logger.warning("workspace %r: %s", workspace.name, warning)
        if not validation.ok:
            raise UnknownProfileError(
                f"workspace {effective_workspace_name!r}: {validation.detail}"
            )

        env = resolve_workspace_environment(settings, workspace)
        if effective_profile_name is None:
            effective_profile_name = env.profile_name
        if not explicit_mcp:
            mcp_servers = env.mcp_servers
        all_skill_names = list(dict.fromkeys(env.skill_names + all_skill_names))
        system_prompt = env.system_prompt
        sidecar_dirname = env.sidecar_folder_name
        _ensure_workspace_folders(workspace)

    if explicit_mcp:
        try:
            mcp_servers = resolve_mcp_servers(settings.mcp, group=mcp_group, names=mcp_names)
        except UnknownMcpServerError:
            raise

    if effective_profile_name is None:
        raise UnknownProfileError(
            "No profile given: pass --profile NAME, or --workspace NAME with a profile configured."
        )

    effective_safety_mode = workspace.safety if workspace else settings.app.default_safety_mode
    # Bug report: "Agents seem to be saving temporary files ... in random
    # places" — one well-known scratch folder every agent/MCP server can
    # write to without confirmation, same "always-allow just this subfolder"
    # treatment as artifacts_dir() below.
    scratch = ensure_scratch_dir(settings.app.scratch_dir)
    guard = SafetyGuard.for_workspace(
        source_folders=workspace.source_folders if workspace else [],
        target_folder=workspace.target_folder if workspace else None,
        # Bug report: writing into ~/.aida/artifacts (AIDA's own generated-
        # output folder) always asked for confirmation, since nothing put it
        # in any allowed-folders list. Always-allow just that subfolder, not
        # the rest of ~/.aida (config.yaml, secrets refs, and the DB live
        # there too and stay gated). Same treatment for the scratch folder.
        global_allowed_folders=[*settings.app.allowed_folders, str(artifacts_dir()), str(scratch)],
        mode=effective_safety_mode,
        confirm_callback=confirm_callback,
        # Phase 9: union'd the same way allowed folders already are — a
        # workspace's own allowlist plus whatever's globally allowlisted.
        command_allowlist=settings.app.command_allowlist
        + (workspace.command_allowlist if workspace else []),
    )

    # Bug report: "Agent seems to have no understanding of Source and
    # Target folders" — nothing previously told the model what a
    # workspace's actual configured paths were; it could only learn them if
    # the user typed them out. Skills files can't fix this either (static
    # Markdown shared across every workspace that lists it, not
    # per-workspace user data). Generated fresh here instead.
    extra_context_texts: list[str] = []
    folder_context = build_workspace_context_block(
        source_folders=workspace.source_folders if workspace else [],
        target_folder=workspace.target_folder if workspace else None,
        global_allowed_folders=settings.app.allowed_folders,
        sidecar_dirname=sidecar_dirname,
        safety_mode=effective_safety_mode,
        scratch_dir=str(scratch),
    )
    if folder_context:
        extra_context_texts.append(folder_context)

    # Phase 9: a workspace's code templates, surfaced as a compact
    # name+docstring list (not full source — see templates_context_text's
    # docstring) so the model follows the workspace's own conventions when
    # generating instrument functions.
    if workspace and workspace.templates_dir:
        templates_context = templates_context_text(load_templates(Path(workspace.templates_dir)))
        if templates_context:
            extra_context_texts.append(templates_context)

    # Bug report: the model resorted to an ad hoc `python3 -c "..."` shell
    # probe (via run_command, needing confirmation since it was never
    # allowlisted) just to discover which interpreter/packages it had —
    # telling it directly avoids the probe in the first place.
    coding_context = build_coding_context_block(
        python_interpreter=workspace.python_interpreter if workspace else None,
        command_allowlist=settings.app.command_allowlist
        + (workspace.command_allowlist if workspace else []),
        scripting_enabled=bool(workspace and workspace.scripting_enabled),
    )
    if coding_context:
        extra_context_texts.append(coding_context)

    # Phase 8 (RAG): resolve each of the workspace's knowledge_bases names
    # into a ready-to-query ActiveKnowledgeBase (open index connection +
    # built embeddings provider) — retrieval itself is query-dependent
    # (ChatSession.send() calls it fresh per turn), but the connection and
    # provider are opened once, here, same lazy-only-if-configured
    # philosophy as MCP servers. A misconfigured reference (unknown KB
    # name, missing/unknown embedding profile) warns and is skipped rather
    # than aborting the whole session — "warn, don't crash" matches every
    # other workspace-reference validation in this module.
    active_knowledge_bases: list[ActiveKnowledgeBase] = []
    for kb_name in workspace.knowledge_bases if workspace else []:
        kb_config = settings.knowledge.knowledge_bases.get(kb_name)
        if kb_config is None:
            print(
                f"[knowledge] warning: workspace references unknown knowledge base {kb_name!r} — skipping"
            )
            logger.warning(
                "workspace %r references unknown knowledge base %r",
                effective_workspace_name,
                kb_name,
            )
            continue
        if not kb_config.embedding_profile:
            print(
                f"[knowledge] warning: knowledge base {kb_name!r} has no embedding_profile configured — skipping"
            )
            continue
        embedding_profile = settings.providers.embedding_profiles.get(kb_config.embedding_profile)
        if embedding_profile is None:
            print(
                f"[knowledge] warning: knowledge base {kb_name!r} references unknown embedding profile "
                f"{kb_config.embedding_profile!r} — skipping"
            )
            continue
        try:
            embeddings_provider = build_embeddings_provider(embedding_profile)
        except UnknownProviderKindError as exc:
            print(f"[knowledge] warning: knowledge base {kb_name!r}: {exc} — skipping")
            continue
        conn = kb_index.connect(knowledge_db_path(kb_name))
        # Registered the moment each is opened, so a failure anywhere later
        # in startup releases them — including the embeddings provider's
        # HTTP client, which is easy to forget because it looks like plain
        # configuration rather than an open connection.
        stack.push_async_callback(_close_knowledge_base, conn, embeddings_provider)
        active_knowledge_bases.append(
            ActiveKnowledgeBase(
                name=kb_name,
                connection=conn,
                embeddings_provider=embeddings_provider,
                embedding_profile_name=kb_config.embedding_profile,
            )
        )
        print(f"[knowledge] {kb_name}: {kb_index.chunk_count(conn)} chunk(s) indexed")
        logger.debug(
            "knowledge base %r ready with %d chunk(s)", kb_name, kb_index.chunk_count(conn)
        )

    mcp_manager: McpManager | None = None
    tools = default_native_tools()
    tools.update(default_file_tools(guard))
    tools.update(default_document_tools(guard, artifact_store, sidecar_dirname=sidecar_dirname))
    tools.update(default_coding_tools(guard, workspace=workspace))
    tools.update(default_web_tools(guard))
    if mcp_servers:
        # Same confirm_callback SafetyGuard just got, above — a per-tool
        # "confirm before run" flag (Phase 7) reuses the identical
        # human-in-the-loop channel (real terminal prompt / real GUI modal)
        # as file-safety confirmations, independent of the workspace's
        # relaxed/confirm mode. Passing `deny_all` here (the default this
        # used to fall back to implicitly) would make every confirm-flagged
        # MCP tool always refuse, silently — this is the wiring that makes
        # the feature actually reachable in a real session, not just in
        # McpManager's own unit tests.
        mcp_manager = McpManager(
            mcp_servers,
            artifact_store=artifact_store,
            confirm_callback=confirm_callback,
            scratch_dir=scratch,
        )
        # Registered *before* start_all, not after: these are real
        # subprocesses, and start_all isolates a per-server McpServerError
        # but not every other failure — one escaping partway through would
        # otherwise leave the servers it had already launched running with
        # nothing holding a reference to them.
        stack.push_async_callback(mcp_manager.aclose)
        mcp_tools = await mcp_manager.start_all()
        tools.update(mcp_tools)
        for skill in mcp_manager.skills():
            if skill not in all_skill_names:
                all_skill_names.append(skill)
        # A FastMCP server author can write `instructions=` specifically to
        # teach an LLM how to use *that* server's own tools (pyirena-mcp
        # ships a detailed one covering its whole read/fit/plot workflow) —
        # AIDA used to call session.initialize() and throw the result away
        # entirely, so this never reached the model even though the server
        # had already provided it.
        for name, instructions in mcp_manager.server_instructions().items():
            extra_context_texts.append(f"# {name} — server instructions\n\n{instructions}")
        for name in mcp_manager.running_server_names:
            count = sum(1 for t in mcp_tools if t.startswith(f"{name}{NAMESPACE_SEPARATOR}"))
            print(f"[mcp] {name}: {count} tool(s)")
            logger.debug("mcp server %r started with %d tool(s)", name, count)
        for name, error in mcp_manager.start_errors.items():
            print(f"[mcp] {name}: FAILED to start — {error}")
            logger.warning("mcp server %r failed to start: %s", name, error)

    if resume_conversation_id:
        recorder = ConversationRecorder(
            store, artifact_store, records_dir, conversation_id=resume_conversation_id, resume=True
        )
        initial_messages: list[Message] | None = recorder.load_history()
        print(f"[conversations] resumed with {len(initial_messages)} prior message(s)")
    else:
        recorder = ConversationRecorder(
            store,
            artifact_store,
            records_dir,
            workspace_name=effective_workspace_name,
            profile_name=effective_profile_name,
            sidecar_dirname=sidecar_dirname,
            origin=origin,
            user=active_user,
        )
        initial_messages = None

    # Registered here rather than with the other document tools, because
    # they need the conversation's attachments folder — which depends on
    # the conversation id the recorder only just assigned. Passed as a
    # callable, not a path: it must be re-read per call, since it is a
    # lookup and the recorder is the thing that knows it.
    # OCR is opt-in three times over, and all three are checked here rather
    # than inside the backend: the workspace must have asked for it, a key
    # must exist, and the user is still asked per document before anything
    # is uploaded (OcrBackend.approved_for). A None backend is what "off by
    # default" means structurally — nothing downstream has to remember to
    # check a flag.
    ocr_backend = None
    if workspace is not None and workspace.use_ocr:
        ocr_key = get_secret(OCR_SECRET_REF)
        if ocr_key:
            ocr_backend = OcrBackend(api_key=ocr_key, confirm=confirm_callback)
        else:
            print(
                f"[ocr] workspace {workspace.name!r} has use_ocr enabled but no API key is set "
                f"(Settings, or ${env_var_name(OCR_SECRET_REF)}) — falling back to the built-in "
                f"figure extractor."
            )
            logger.warning(
                "workspace %r: use_ocr enabled but no %r secret", workspace.name, OCR_SECRET_REF
            )
    tools.update(default_figure_tools(recorder.attachments_dir, ocr=ocr_backend))

    # No try/except here any more. `ChatSession.__init__` can fail in
    # several ways — `build_provider` (UnknownProviderKindError for a typo'd
    # `kind:` in providers.yaml), reading each configured skills file
    # (OSError / UnicodeDecodeError) — and so can the `ConversationRecorder`
    # and `load_history()` above, on a locked or corrupt database. All of
    # them are covered by the exit stack the caller opened, along with every
    # earlier step, rather than by a hand-written cleanup block that only
    # ever guarded this one call.
    session = ChatSession(
        settings,
        effective_profile_name,
        tools=tools,
        skill_names=all_skill_names,
        system_prompt=system_prompt,
        recorder=recorder,
        initial_messages=initial_messages,
        extra_context_texts=extra_context_texts,
        active_knowledge_bases=active_knowledge_bases,
        identity_text=identity_text,
    )
    return session, mcp_manager


async def _close_knowledge_base(conn, embeddings_provider) -> None:
    """Release one knowledge base's two resources, each independently.

    Suppressed individually so a provider whose ``aclose`` throws cannot
    leave the SQLite connection open behind it — during cleanup, a failure
    to release one thing must never become a failure to release the rest."""
    with contextlib.suppress(Exception):
        await embeddings_provider.aclose()
    with contextlib.suppress(Exception):
        conn.close()


__all__ = [
    "ChatSession",
    "UnknownMcpServerError",
    "UnknownProfileError",
    "UnknownWorkspaceError",
    "cli_confirm",
    "resolve_mcp_servers",
    "resolve_profile",
    "start_session",
]
