"""``aida chat`` — interactive streaming chat with a provider (Phase 2),
now workspace-aware and persisted (Phase 4).

The reference frontend implementation (PLAN.md §10 Phase 2 row): every
printed line maps 1:1 to an event from ``aida.core.events``, so this file
doubles as the spec any future frontend (Qt in Phase 5, ...) needs to match.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.coding.templates import load_templates, templates_context_text
from aida.coding.tools import default_coding_tools
from aida.config.logging_setup import get_logger
from aida.config.paths import artifacts_dir, ensure_records_dir, knowledge_db_path, skills_dir
from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    Settings,
    WorkspaceConfig,
    load_settings,
)
from aida.core.agent import AgentLoop
from aida.core.context import (
    build_coding_context_block,
    build_system_message,
    build_workspace_context_block,
    load_skill_texts,
)
from aida.core.cost import estimate_cost_usd
from aida.core.events import (
    AgentError,
    AgentEvent,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    RetrievalPerformed,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
    UsageInfo,
)
from aida.core.tools import NativeTool, default_native_tools
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
from aida.providers.base import CompletionSettings, Message
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


async def cli_confirm(request: ConfirmationRequest) -> bool:
    """The CLI's default ``ConfirmCallback`` (Phase 6): blocks on a real
    terminal prompt via ``asyncio.to_thread(input, ...)`` so it doesn't
    freeze the event loop out from under any concurrently-streaming output.
    The GUI (``aida.ui.qt.bridge.ChatBridge``) passes its own callback that
    shows a modal dialog instead — see ``start_session``'s docstring."""
    answer = await asyncio.to_thread(input, f"\n[confirm] {request.detail} [y/N] ")
    return answer.strip().lower() in ("y", "yes")


logger = get_logger("session")


class UnknownMcpServerError(Exception):
    """Raised when ``--mcp`` names a server that isn't in ``mcp.json``."""


class UnknownProfileError(Exception):
    """Raised when ``--profile``/``/profile`` names a profile that isn't
    configured in ``providers.yaml``."""


class UnknownWorkspaceError(Exception):
    """Raised when ``--workspace`` names a workspace that isn't in
    ``workspaces.yaml``."""


def print_event(event: AgentEvent) -> None:
    """Render one ``AgentEvent`` to stdout. Kept as a standalone function
    (not inlined in the REPL loop) so tests can assert on its output for a
    given event without driving a whole session."""
    if isinstance(event, TextStarted):
        return
    if isinstance(event, TextDelta):
        print(event.text, end="", flush=True)
    elif isinstance(event, TextFinished):
        print()
    elif isinstance(event, ToolCallStarted):
        print(f"\n[tool call] {event.tool_name}({event.arguments})")
    elif isinstance(event, ToolCallFinished):
        status = "error" if event.is_error else "ok"
        print(f"[tool result:{status}] {event.tool_name} -> {event.result}")
    elif isinstance(event, ImageArtifactCreated):
        print(f"[image artifact] {event.path or event.artifact_id}")
    elif isinstance(event, FileArtifactCreated):
        print(f"[file artifact] {event.path}")
    elif isinstance(event, MessageFinished):
        pass  # no separate line; TextFinished already closed the reply
    elif isinstance(event, UsageInfo):
        # Bug report: "Can we get cost estimate... token use may be better
        # ... it is a black box." Printed unconditionally now (was
        # previously swallowed, "for a future --verbose flag") — a provider
        # that doesn't report usage just never emits this event at all, so
        # there's nothing spurious to gate behind a flag.
        if event.output_tokens and event.duration_seconds:
            rate = event.output_tokens / event.duration_seconds
            print(
                f"[usage] {event.input_tokens} in / {event.output_tokens} out tokens, "
                f"{event.duration_seconds:.1f}s ({rate:.1f} tok/s)"
            )
        elif event.input_tokens or event.output_tokens:
            print(f"[usage] {event.input_tokens} in / {event.output_tokens} out tokens")
    elif isinstance(event, AgentError):
        detail = f" ({event.detail})" if event.detail else ""
        print(f"\n[error:{event.layer}] {event.message}{detail}")


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
            lines.append(f"### {Path(passage.source_path).name}{heading_suffix} (score {passage.score:.2f})")
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
    ) -> None:
        self.settings = settings
        self.tools = tools if tools is not None else default_native_tools()
        self.profile_name = profile_name
        self.profile = resolve_profile(settings, profile_name)
        self.provider = build_provider(self.profile)
        self.completion_settings = CompletionSettings(model=self.profile.model)
        self.loop = AgentLoop(
            self.provider, self.completion_settings, self.tools, max_iterations=settings.app.max_agent_iterations
        )
        self.recorder = recorder
        # Phase 8 (RAG): resolved once at session-start (aida.cli.chat.
        # start_session), queried fresh every turn in send() — retrieval is
        # query-dependent, unlike skills/MCP-instructions text, which is
        # why it can't reuse extra_context_texts (built once, before any
        # user message exists). Empty for a workspace with no
        # knowledge_bases configured — zero retrieval calls, zero cost.
        self.active_knowledge_bases = active_knowledge_bases or []
        # Bug report: "Can we get cost estimate... token use may be
        # better... at this moment it is a black box." Cumulative across
        # the whole session (including across /profile switches, which
        # deliberately don't reset it — the model may change but the
        # user's own running total shouldn't). See aida.core.cost.
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        skill_texts = load_skill_texts(skills_dir(), skill_names or [])
        system_message = build_system_message(system_prompt, skill_texts, extra_texts=extra_context_texts)
        history = list(initial_messages) if initial_messages else []
        self.messages: list[Message] = ([system_message] if system_message.content else []) + history

    async def switch_profile(self, name: str) -> None:
        new_profile = resolve_profile(self.settings, name)  # validate before tearing anything down
        old_provider = self.provider
        self.profile = new_profile
        self.profile_name = name
        self.provider = build_provider(self.profile)
        self.completion_settings = CompletionSettings(model=self.profile.model)
        self.loop = AgentLoop(
            self.provider, self.completion_settings, self.tools, max_iterations=self.settings.app.max_agent_iterations
        )
        # self.messages is intentionally left untouched: history carries over.
        await old_provider.aclose()

    def cancel(self) -> None:
        self.loop.cancel()

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

    async def send(self, user_text: str):
        """Run one turn. Phase 8 (RAG): if any knowledge bases are active,
        retrieves passages for ``user_text`` and injects them as a
        *strictly ephemeral* extra message — appended to ``self.messages``
        (so the model sees it this turn, via the same list ``AgentLoop.run``
        mutates and the recorder watches) but removed again once the turn
        ends, and explicitly excluded from persistence throughout. This
        keeps ``self.messages`` the one canonical mutable list without ever
        writing stale retrieved context to the DB or re-sending it next
        turn with a different question."""
        user_message = Message(role="user", content=user_text)
        self.messages.append(user_message)
        if self.recorder is not None:
            self.recorder.record_message(user_message)
        persisted = len(self.messages)

        context_message: Message | None = None
        if self.active_knowledge_bases:
            passages_by_kb = await self._retrieve_context(user_text)
            if passages_by_kb:
                yield RetrievalPerformed(
                    passages_by_kb={
                        kb_name: [
                            {"text": p.text, "source_path": p.source_path, "heading": p.heading, "score": p.score}
                            for p in passages
                        ]
                        for kb_name, passages in passages_by_kb.items()
                    }
                )
                context_message = Message(role="user", content=_format_retrieved_context(passages_by_kb))
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
                    )
                elif isinstance(event, FileArtifactCreated):
                    self.recorder.record_artifact_fields(
                        artifact_id=event.artifact_id,
                        kind="FileArtifact",
                        path=event.path,
                        mime_type=event.mime_type,
                        call_id=event.call_id,
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
            if context_message is not None and context_message in self.messages:
                self.messages.remove(context_message)

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
            self.recorder.store.close()


async def _run_repl(session: ChatSession) -> None:
    print(
        f"AIDA chat — profile: {session.profile_name} "
        f"({session.profile.kind}, model={session.profile.model})"
    )
    if session.recorder is not None:
        print(f"[conversations] recording as {session.recorder.conversation_id}")
    print(
        "Type /exit to quit, /profile NAME to switch profiles mid-session, "
        "/max-iterations N to raise the per-turn tool-call cap.\n"
    )

    try:
        await _repl_loop(session)
    finally:
        # Always close the (possibly swapped) provider's HTTP client before
        # asyncio.run() tears down the event loop — see ChatSession.aclose.
        await session.aclose()


async def _repl_loop(session: ChatSession) -> None:
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print("\n[cancelled]")
            continue

        if not line:
            continue
        if line == "/exit":
            return
        if line.startswith("/max-iterations "):
            # Bug report: hit the iteration cap mid-session with no way to
            # raise it short of quitting and re-editing AppConfig (the
            # Settings-dialog control only reaches a *new* AgentLoop, and
            # the CLI has no dialog at all). Mutating session.loop directly
            # takes effect on the very next turn, no restart needed.
            value_str = line.removeprefix("/max-iterations ").strip()
            try:
                value = int(value_str)
            except ValueError:
                print(f"Not a number: {value_str!r}")
                continue
            if value < 1:
                print("Must be at least 1.")
                continue
            session.loop.max_iterations = value
            print(f"Max tool-call iterations per turn set to {value} for this session.")
            continue
        if line.startswith("/profile "):
            new_name = line.removeprefix("/profile ").strip()
            try:
                await session.switch_profile(new_name)
            except UnknownProfileError as exc:
                print(str(exc))
                continue
            print(
                f"Switched to profile {session.profile_name!r} "
                f"({session.profile.kind}, model={session.profile.model}). History carries over."
            )
            continue

        try:
            async for event in session.send(line):
                print_event(event)
        except KeyboardInterrupt:
            session.cancel()
            print("\n[cancelled]")
            continue

        if session.total_input_tokens or session.total_output_tokens:
            cost = estimate_cost_usd(session.total_input_tokens, session.total_output_tokens)
            print(
                f"[session total] {session.total_input_tokens} in / {session.total_output_tokens} out "
                f"tokens, ~${cost:.4f} est."
            )


def _ensure_workspace_folders(workspace: WorkspaceConfig) -> None:
    """"Can we create the folders if they do not exist? I need to populate
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
    checks still apply on top of this either way."""
    for folder in [*workspace.source_folders, workspace.target_folder]:
        if not folder:
            continue
        path = Path(folder).expanduser()
        if path.exists():
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
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
    confirm_callback = confirm_callback or cli_confirm
    store = ConversationStore()
    artifact_store = ArtifactStore()
    records_dir = ensure_records_dir(Path(settings.app.records_dir) if settings.app.records_dir else None)

    resumed_summary = None
    if resume_conversation_id:
        resumed_summary = store.get_conversation(resume_conversation_id)
        if resumed_summary is None:
            store.close()
            raise ConversationNotFoundError(f"no conversation with id {resume_conversation_id!r}")

    effective_workspace_name = workspace_name or (resumed_summary.workspace_name if resumed_summary else None)
    effective_profile_name = profile_name or (resumed_summary.profile_name if resumed_summary else None)
    sidecar_dirname = resumed_summary.sidecar_dirname if resumed_summary else "figures"

    all_skill_names = list(skill_names or [])
    system_prompt: str | None = None
    mcp_servers: list[McpServerConfig] = []
    explicit_mcp = bool(mcp_group or mcp_names)
    workspace: WorkspaceConfig | None = None

    if effective_workspace_name:
        workspace = get_workspace(settings, effective_workspace_name)
        if workspace is None:
            store.close()
            raise UnknownWorkspaceError(
                f"Unknown workspace {effective_workspace_name!r}. "
                f"Configured workspaces: {', '.join(sorted(settings.workspaces.workspaces)) or '(none)'}"
            )
        validation = validate_workspace(settings, workspace)
        for warning in validation.warnings:
            print(f"[workspace] warning: {warning}")
            logger.warning("workspace %r: %s", workspace.name, warning)
        if not validation.ok:
            store.close()
            raise UnknownProfileError(f"workspace {effective_workspace_name!r}: {validation.detail}")

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
            store.close()
            raise

    if effective_profile_name is None:
        store.close()
        raise UnknownProfileError(
            "No profile given: pass --profile NAME, or --workspace NAME with a profile configured."
        )

    effective_safety_mode = workspace.safety if workspace else settings.app.default_safety_mode
    guard = SafetyGuard.for_workspace(
        source_folders=workspace.source_folders if workspace else [],
        target_folder=workspace.target_folder if workspace else None,
        # Bug report: writing into ~/.aida/artifacts (AIDA's own generated-
        # output folder) always asked for confirmation, since nothing put it
        # in any allowed-folders list. Always-allow just that subfolder, not
        # the rest of ~/.aida (config.yaml, secrets refs, and the DB live
        # there too and stay gated).
        global_allowed_folders=[*settings.app.allowed_folders, str(artifacts_dir())],
        mode=effective_safety_mode,
        confirm_callback=confirm_callback,
        # Phase 9: union'd the same way allowed folders already are — a
        # workspace's own allowlist plus whatever's globally allowlisted.
        command_allowlist=settings.app.command_allowlist + (workspace.command_allowlist if workspace else []),
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
        command_allowlist=settings.app.command_allowlist + (workspace.command_allowlist if workspace else []),
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
    for kb_name in (workspace.knowledge_bases if workspace else []):
        kb_config = settings.knowledge.knowledge_bases.get(kb_name)
        if kb_config is None:
            print(f"[knowledge] warning: workspace references unknown knowledge base {kb_name!r} — skipping")
            logger.warning("workspace %r references unknown knowledge base %r", effective_workspace_name, kb_name)
            continue
        if not kb_config.embedding_profile:
            print(f"[knowledge] warning: knowledge base {kb_name!r} has no embedding_profile configured — skipping")
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
        active_knowledge_bases.append(
            ActiveKnowledgeBase(
                name=kb_name,
                connection=conn,
                embeddings_provider=embeddings_provider,
                embedding_profile_name=kb_config.embedding_profile,
            )
        )
        print(f"[knowledge] {kb_name}: {kb_index.chunk_count(conn)} chunk(s) indexed")
        logger.debug("knowledge base %r ready with %d chunk(s)", kb_name, kb_index.chunk_count(conn))

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
        mcp_manager = McpManager(mcp_servers, artifact_store=artifact_store, confirm_callback=confirm_callback)
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
        )
        initial_messages = None

    try:
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
        )
    except UnknownProfileError:
        if mcp_manager is not None:
            await mcp_manager.aclose()
        for kb in active_knowledge_bases:
            with contextlib.suppress(Exception):
                await kb.embeddings_provider.aclose()
            with contextlib.suppress(Exception):
                kb.connection.close()
        store.close()
        raise

    return session, mcp_manager


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida chat")
    parser.add_argument("--profile", default="", help="Provider profile name from providers.yaml")
    parser.add_argument(
        "--workspace",
        default="",
        help="Named workspace from workspaces.yaml (loads its profile/mcp group/skills/system prompt)",
    )
    parser.add_argument(
        "--skills", default="", help="Comma-separated skill names to load into the system context"
    )
    parser.add_argument(
        "--mcp-group",
        default="",
        help="Named MCP server group from mcp.json to enable (overrides the workspace's group, if any)",
    )
    parser.add_argument(
        "--mcp",
        default="",
        help="Comma-separated MCP server names to enable directly, bypassing groups",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override the per-turn tool-call iteration cap (default: AppConfig.max_agent_iterations)",
    )
    return parser


async def _async_main(
    settings: Settings,
    *,
    profile_name: str | None,
    workspace_name: str | None,
    skill_names: list[str],
    mcp_group: str,
    mcp_names: list[str],
) -> int:
    try:
        session, mcp_manager = await start_session(
            settings,
            profile_name=profile_name,
            workspace_name=workspace_name,
            skill_names=skill_names,
            mcp_group=mcp_group,
            mcp_names=mcp_names,
        )
    except (UnknownProfileError, UnknownWorkspaceError, UnknownMcpServerError) as exc:
        print(str(exc))
        return 1

    try:
        await _run_repl(session)
    finally:
        if mcp_manager is not None:
            await mcp_manager.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    skill_names = [s.strip() for s in args.skills.split(",") if s.strip()]
    mcp_names = [s.strip() for s in args.mcp.split(",") if s.strip()]

    settings = load_settings()
    if args.max_iterations is not None:
        # Per-invocation override, not persisted — same "session-scoped,
        # not saved back to disk" treatment as --profile/--workspace above.
        settings.app.max_agent_iterations = args.max_iterations
    return asyncio.run(
        _async_main(
            settings,
            profile_name=args.profile or None,
            workspace_name=args.workspace or None,
            skill_names=skill_names,
            mcp_group=args.mcp_group,
            mcp_names=mcp_names,
        )
    )
