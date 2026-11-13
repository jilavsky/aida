"""The session engine shared by every frontend: ``ChatSession`` (mutable
per-session state — provider/loop, message history, native tools, the
conversation recorder) and ``start_session`` (the shared startup logic that
resolves a workspace, starts its MCP servers, builds tools/context, and
returns a ready ``ChatSession``).

Lives in ``core`` so both ``aida.cli.chat`` and ``aida.ui.qt.bridge`` import
it without breaking the layering (``core``/``providers``/``mcp``/``workspace``
-> ``cli``/``ui.qt``, never the reverse). ``aida.cli.chat`` re-exports these
names for backwards compatibility. Terminal-facing code (``print_event``, the
REPL, ``argparse``, ``main``) stays in ``aida.cli.chat``; the one exception is
``cli_confirm``, ``start_session``'s default confirm callback, which needs
nothing beyond ``asyncio.to_thread(input, ...)``.

This module is the composition root, not core logic: it imports from
``workspace``, ``mcp``, ``documents``, ``knowledge``, ``persistence``,
``coding`` and ``artifacts`` to wire them together. That is intended, not
layering drift; the rule that matters is that none of it imports Qt.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
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
    WORKSPACE_FOLDERS_HEADING,
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
    skill_exists,
)
from aida.core.events import (
    AgentError,
    ContextTrimmed,
    ConversationTitled,
    FileArtifactCreated,
    ImageArtifactCreated,
    RetrievalPerformed,
    TextFinished,
    UsageInfo,
)
from aida.core.titling import clean_title, title_request_messages
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
from aida.persistence.store import TITLE_GENERATED, ConversationStore
from aida.providers.base import CompletionSettings, ImageRef, Message
from aida.providers.profiles import (
    UnknownProviderKindError,
    build_embeddings_provider,
    build_provider,
)
from aida.providers.retry import SHORT_RETRY_POLICY, complete_with_retries
from aida.workspace.files import default_file_tools
from aida.workspace.safety import ConfirmationRequest, ConfirmCallback, SafetyGuard
from aida.workspace.web import default_web_tools
from aida.workspace.workspaces import (
    get_workspace,
    resolve_workspace_environment,
    validate_workspace,
)


async def cli_confirm(request: ConfirmationRequest) -> ConfirmAnswer:
    """Interactive terminal ``RawConfirmCallback`` (used by ``aida.cli.chat``).

    Prompts via ``asyncio.to_thread(input, ...)`` so the event loop keeps
    running. The GUI passes its own dialog-based callback instead. Always
    wrapped in a ``RememberingConfirm`` (see ``_start_session``), which maps
    the tri-state ``ConfirmAnswer`` to the bool ``ConfirmCallback`` consumers
    expect and remembers ``ALLOW_FOR_CHAT`` for the rest of the session."""
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

#: Advisory threshold for warning about a large combined tool count (native
#: + built-in + all active MCP tools). Deliberately below the ANL Argo
#: gateway's hard cap of 128 tools so the user can act before rejection.
_LARGE_TOOL_COUNT_WARNING_THRESHOLD = 100


class UnknownMcpServerError(Exception):
    """Raised when ``--mcp`` names a server that isn't in ``mcp.json``."""


class UnknownProfileError(Exception):
    """Raised when ``--profile``/``/profile`` names a profile that isn't
    configured in ``providers.yaml``."""


class UnknownWorkspaceError(Exception):
    """Raised when ``--workspace`` names a workspace that isn't in
    ``workspaces.yaml``."""


class NoWorkspaceFoldersError(Exception):
    """Raised by ``ChatSession.update_workspace_folders`` for a session that
    has no workspace behind it (``aida chat --profile ...`` with no
    workspace), so there are no source/target folders to change."""


class SessionBusyError(Exception):
    """Raised when a second mutation of a session's state is attempted while
    one is already in flight.

    ``send`` (appends to ``self.messages``, streams from ``self.provider``),
    ``compact_now`` (replaces ``self.messages`` after awaiting a summary) and
    ``switch_profile`` (swaps the provider) must never overlap: compaction
    would discard messages appended meanwhile, and a switch would close a
    provider mid-stream. Enforced here, not only in the GUI, because the CLI
    and other callers bypass it. Refusing beats queueing: a compaction or
    switch silently applied minutes later would surprise the user more."""


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
    """Build ``CompletionSettings`` from a profile; shared by
    ``ChatSession.__init__`` and ``switch_profile`` so they can't drift.

    An unset ``temperature`` sends none (no invented default). ``max_tokens``
    keeps its fallback because not every endpoint accepts an uncapped request."""
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


@dataclass
class WorkspaceFolderState:
    """Everything a live session needs to rebuild its folder permissions and
    its ``# Workspace folders`` context block after the user edits the
    workspace's folders mid-conversation.

    Holds the same inputs ``start_session`` used, so both halves of folder
    access (the ``SafetyGuard`` roots and the system-prompt block) can be
    rebuilt consistently without the GUI reassembling them.

    ``guard_extra_roots`` is everything the guard always allows (global
    allowed folders + AIDA's artifacts and scratch dirs);
    ``context_allowed_folders`` is what the model is *told* about (global
    allowed folders only) — they differ on purpose, see ``start_session``.
    ``internal_roots`` (artifacts + scratch) is kept separately so
    ``update_global_allowed_folders`` can rebuild ``guard_extra_roots``
    without losing it. ``in_workspace`` is False for a bare ``--profile``
    chat, which has global allowed folders but no source/target to edit.
    """

    source_folders: list[str] = field(default_factory=list)
    target_folder: str | None = None
    guard_extra_roots: list[str] = field(default_factory=list)
    context_allowed_folders: list[str] = field(default_factory=list)
    sidecar_dirname: str = "figures"
    safety_mode: str = "confirm"
    scratch_dir: str | None = None
    internal_roots: list[str] = field(default_factory=list)
    in_workspace: bool = True


class ChatSession:
    """Holds the mutable state of one chat session: current provider/loop,
    message history, native tools, and the recorder persisting everything
    to SQLite + a Markdown transcript. Mid-session ``/profile`` switches swap
    the provider + loop but keep ``messages``."""

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
        guard: SafetyGuard | None = None,
        folder_state: WorkspaceFolderState | None = None,
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
            # Re-check the budget before every round trip, since one long turn
            # can add huge tool results. Safe mid-turn: _trim_context only
            # drops whole older turns and is a no-op when nothing needs trimming.
            before_round_trip=self._trim_context,
        )
        self.recorder = recorder
        # Resolved once at start, queried every turn in send(): retrieval is
        # query-dependent, so it can't live in the prebuilt extra_context_texts.
        # Empty (zero cost) when the workspace has no knowledge bases.
        self.active_knowledge_bases = active_knowledge_bases or []
        # Cumulative for the whole session; /profile switches deliberately
        # don't reset it. See aida.core.cost.
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # Serializes send()/compact_now()/switch_profile() — see
        # SessionBusyError. Internal helpers (_trim_context, _apply_trim_plan,
        # ...) don't take it: they only run underneath one of those three,
        # including the mid-turn before_round_trip hook (fired from
        # AgentLoop.run inside send()).
        self._mutation_lock = asyncio.Lock()
        # Bumped whenever self.messages is rewritten or appended to for a turn.
        # _apply_trim_plan re-checks it after awaiting the summary to assert its
        # plan still matches the list. The lock prevents the race; this catches
        # a refactor that reintroduces it.
        self._history_generation = 0
        #: Index in ``self.messages`` of the running turn's first message not
        #: yet recorded; ``None`` between turns. Kept on the session so
        #: ``_apply_trim_plan`` can shift it when a mid-turn trim shortens the
        #: prefix (the running turn itself is never trimmed).
        self._persist_index: int | None = None
        #: True while ``send()`` is running a turn — see ``cancel``.
        self._turn_active = False
        #: Set by frontends that can show events outside a turn (the GUI).
        #: When set, titling runs as a background task after the turn and its
        #: ``ConversationTitled`` event is delivered here, freeing the input
        #: immediately. When ``None`` (CLI, ``aida run``, workflows) titling
        #: stays inline and the event is yielded from ``send()``.
        self.on_background_event: Callable[[object], None] | None = None
        #: The running background titling task, if any. ``send()``,
        #: ``compact_now()`` and ``switch_profile()`` await it first so they
        #: never share the provider with it.
        self._title_task: asyncio.Task | None = None
        #: What ``start_session`` reported while building this session
        #: (see ``StartupNotices``); empty for a session built directly.
        self.startup_notices: list[StartupNotice] = []
        #: Completed turns since the last title check. Starts at 0 on resume,
        #: so an already-named conversation waits a full interval. Whether it
        #: has a generated name lives in ``recorder.title_source`` (must
        #: survive the process). See _maybe_update_title.
        self._turns_since_title = 0
        #: Whether a titling call has been made this session. The "still on
        #: the placeholder" shortcut may skip the cadence only once, so a
        #: failing endpoint costs one call per interval, not one per turn.
        self._title_attempted = False

        # Kept so folders can change mid-conversation without restarting
        # (``update_workspace_folders``, ``update_global_allowed_folders``).
        # ``guard`` is None only in tests without file tools; ``folder_state``
        # is None for a session built outside ``start_session``.
        self.guard = guard
        self.folder_state = folder_state
        self._system_prompt = system_prompt
        self._identity_text = identity_text
        self._extra_context_texts = list(extra_context_texts or [])
        #: One-shot notices for the model at the start of the next turn (see
        #: ``_run_turn``): a silent system-message change is easy to miss when
        #: the history still shows the old paths.
        self._pending_notices: list[str] = []

        #: Names as requested (workspace + ``--skills`` + MCP-attached), kept
        #: so ``update_skills`` can report what changed and the GUI's Skills
        #: panel can show what this chat is actually running with.
        self._skill_names = list(dict.fromkeys(skill_names or []))
        self._skill_texts = load_skill_texts(skills_dir(), self._skill_names)
        #: See ``update_skills`` — lets repeated toggles collapse into one notice.
        self._pending_skills_notice: str | None = None
        self._skills_notice_baseline: list[str] = []
        skill_texts = self._skill_texts
        system_message = build_system_message(
            system_prompt, skill_texts, extra_texts=extra_context_texts, identity_text=identity_text
        )
        # A session killed between persisting tool calls and their results
        # leaves orphaned tool_use blocks that make every later request fail;
        # repair resumed history once on the way in.
        history = repair_tool_call_pairing(list(initial_messages)) if initial_messages else []
        self.messages: list[Message] = (
            [system_message] if system_message.content else []
        ) + history

    def _rebuild_system_message(self) -> None:
        """Re-render ``messages[0]`` from the same inputs ``__init__`` used,
        after one of the generated context blocks in
        ``self._extra_context_texts`` has been replaced.

        Rewriting the system message (not just posting a chat note) makes the
        change survive trimming and compaction, at the cost of one
        prompt-cache miss."""
        message = build_system_message(
            self._system_prompt,
            self._skill_texts,
            extra_texts=self._extra_context_texts,
            identity_text=self._identity_text,
        )
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = message
        elif message.content:
            self.messages.insert(0, message)

    def _replace_context_block(self, heading: str, text: str | None) -> None:
        """Swap the generated context block starting with ``heading`` for
        ``text`` (or drop it, for ``None``), leaving every other block —
        MCP server instructions, code templates — untouched and in place."""
        for position, existing in enumerate(self._extra_context_texts):
            if existing.startswith(heading):
                if text is None:
                    del self._extra_context_texts[position]
                else:
                    self._extra_context_texts[position] = text
                return
        if text is not None:
            self._extra_context_texts.insert(0, text)

    def _apply_folder_state(self) -> None:
        """Rebuild both halves of folder access — the guard's allowed roots
        and the system-prompt folder block — from ``self.folder_state``.
        Shared by ``update_workspace_folders`` and
        ``update_global_allowed_folders`` so the two can't drift apart."""
        state = self.folder_state
        if state is None:
            return
        if self.guard is not None:
            # Same constructor as start_session so roots match a fresh
            # session. Mutated in place: every built tool holds this guard.
            rebuilt = SafetyGuard.for_workspace(
                source_folders=state.source_folders,
                target_folder=state.target_folder,
                global_allowed_folders=state.guard_extra_roots,
                mode=self.guard.mode,
            )
            self.guard.allowed_roots = rebuilt.allowed_roots

        self._replace_context_block(
            WORKSPACE_FOLDERS_HEADING,
            build_workspace_context_block(
                source_folders=state.source_folders,
                target_folder=state.target_folder,
                global_allowed_folders=state.context_allowed_folders,
                sidecar_dirname=state.sidecar_dirname,
                safety_mode=state.safety_mode,
                scratch_dir=state.scratch_dir,
            ),
        )
        self._rebuild_system_message()

    def _announce_folder_change(self, notice: str) -> None:
        """Tell the model on its next round trip that its folders changed."""
        if self.is_mutating:
            # Mid-turn: deliver at the next round trip (like an interjection),
            # since the agent may be about to use the changed folder.
            self.queue_user_message(notice)
        else:
            self._pending_notices.append(notice)

    def update_global_allowed_folders(self, allowed_folders: list[str]) -> str | None:
        """Apply a changed global ``AppConfig.allowed_folders`` list (the
        Settings dialog) to this *running* session, updating the guard and
        the system prompt together.

        Works for workspace-less chats too (global folders apply to every
        chat). Returns a one-line summary for the caller to show, or
        ``None`` when nothing changed or the session has no folder state
        (one constructed outside ``start_session``)."""
        state = self.folder_state
        if state is None:
            return None
        folders = [str(folder) for folder in allowed_folders if str(folder).strip()]
        if folders == state.context_allowed_folders:
            return None
        state.context_allowed_folders = folders
        state.guard_extra_roots = [*folders, *state.internal_roots]
        self._apply_folder_state()

        if folders:
            listing = ", ".join(folders)
            notice = (
                "The user just changed the globally allowed folders, mid-conversation. "
                f"You now have access to: {listing}. Your instructions have been updated to "
                "match; use these from now on and don't ask the user to repeat the paths."
            )
        else:
            notice = (
                "The user just removed all globally allowed folders, mid-conversation. "
                "Your instructions have been updated to match."
            )
        self._announce_folder_change(notice)

        missing = [folder for folder in folders if not Path(folder).expanduser().exists()]
        summary = f"Allowed folders applied to this chat — {len(folders)} folder(s)"
        if missing:
            summary += " — not found on disk: " + ", ".join(missing)
        return summary

    def update_workspace_folders(
        self,
        *,
        source_folders: list[str],
        target_folder: str | None,
    ) -> str:
        """Apply new source/target folders to this *running* session, no restart.

        The ``SafetyGuard`` allows them immediately and the model is told
        about them; both always change together, or the session could read a
        folder it doesn't know about (or vice versa).

        Callers must expand any ``{user}`` placeholder first
        (``resolve_workspace_for_user``): a literal ``{user}`` root allows
        nothing. Persisting the folders to the workspace is the caller's
        separate decision.

        Returns a one-line summary for the user. Raises
        ``NoWorkspaceFoldersError`` for a session without workspace folder
        state (a bare ``--profile`` chat)."""
        if self.folder_state is None or not self.folder_state.in_workspace:
            raise NoWorkspaceFoldersError(
                "This chat isn't running in a workspace, so its folders can't be changed — "
                "start or resume a chat in a workspace first."
            )
        state = self.folder_state
        state.source_folders = [str(folder) for folder in source_folders if str(folder).strip()]
        state.target_folder = str(target_folder) if target_folder else None

        if state.target_folder:
            # Best-effort create, as in _ensure_workspace_folders. Source
            # folders are deliberately never created.
            try:
                Path(state.target_folder).expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # noqa: PERF203 - one path, logged and carried on
                logger.warning("could not create target folder %s: %s", state.target_folder, exc)

        self._apply_folder_state()

        notice_lines = ["The user just changed this workspace's folders, mid-conversation."]
        if state.source_folders:
            notice_lines.append(
                "Source folder(s) — read data from here: " + ", ".join(state.source_folders)
            )
        else:
            notice_lines.append("There are no source folders configured now.")
        if state.target_folder:
            notice_lines.append(
                f"Target folder — write generated reports and files here: {state.target_folder}"
            )
        notice_lines.append(
            "Your instructions have been updated to match, and you already have access to these "
            "folders. Use them from now on instead of any folder paths mentioned earlier in this "
            "conversation, and don't ask the user to repeat the paths."
        )
        self._announce_folder_change("\n".join(notice_lines))

        missing = [
            folder for folder in state.source_folders if not Path(folder).expanduser().exists()
        ]
        summary = (
            f"Folders applied to this chat — {len(state.source_folders)} source folder(s), "
            f"target: {state.target_folder or '(none)'}"
        )
        if missing:
            summary += " — not found on disk: " + ", ".join(missing)
        return summary

    @property
    def skill_names(self) -> list[str]:
        """Skills this chat's system message is currently built from."""
        return list(self._skill_names)

    def update_skills(self, skill_names: list[str]) -> str:
        """Swap the set of skills loaded into this *running* session, with
        no restart — the GUI's Skills panel.

        Skills are pasted whole into the system message, so switching lets
        the user change playbooks while keeping the conversation in context.
        The system message is rebuilt (survives trimming/compaction) and the
        next turn carries a one-shot notice, because earlier replies written
        under the old skills would otherwise keep steering the model.

        Skill files are re-read from disk, so re-applying the same set picks
        up edits. Persisting the selection is the caller's decision. Returns
        a one-line summary for the caller to show."""
        new_names = list(dict.fromkeys(name for name in skill_names if name.strip()))
        # Several toggles before the next message collapse into one notice:
        # replace an undelivered one and diff against what the model last saw.
        pending = self._pending_skills_notice
        if pending is not None and pending in self._pending_notices:
            self._pending_notices.remove(pending)
            baseline = self._skills_notice_baseline
        else:
            baseline = self._skill_names
        self._pending_skills_notice = None
        self._skills_notice_baseline = baseline

        self._skill_names = new_names
        self._skill_texts = load_skill_texts(skills_dir(), new_names)
        self._rebuild_system_message()

        added = [name for name in new_names if name not in baseline]
        removed = [name for name in baseline if name not in new_names]
        if added or removed:
            notice_lines = ["The user just changed which skills are active, mid-conversation."]
            if new_names:
                notice_lines.append("Active skills now: " + ", ".join(new_names) + ".")
            else:
                notice_lines.append("No skills are active now.")
            if removed:
                notice_lines.append(
                    "No longer active: "
                    + ", ".join(removed)
                    + ". Stop following their instructions, including output formats or roles "
                    "they set in earlier replies of this conversation."
                )
            notice_lines.append(
                "Your instructions have been updated to match; follow the active skills from now on."
            )
            notice = "\n".join(notice_lines)
            if self.is_mutating:
                self.queue_user_message(notice)
            else:
                self._pending_notices.append(notice)
                self._pending_skills_notice = notice

        missing = [name for name in new_names if not skill_exists(skills_dir(), name)]
        loaded = [name for name in new_names if name not in missing]
        summary = f"Skills applied to this chat — {len(loaded)} active"
        if loaded:
            summary += ": " + ", ".join(loaded)
        if missing:
            summary += " — not found on disk: " + ", ".join(missing)
        return summary

    @property
    def is_mutating(self) -> bool:
        """Whether a turn, a compaction, or a profile switch is in flight.

        Lets a frontend disable controls that would raise ``SessionBusyError``."""
        return self._mutation_lock.locked()

    async def switch_profile(self, name: str) -> None:
        """Swap provider + loop to another configured profile, keeping the
        conversation history.

        Load-bearing ordering: nothing is assigned to ``self`` until every
        fallible step (unknown profile, ``UnknownProviderKindError``, missing
        secret, ...) has succeeded, so a failure never leaves a half-switched
        session. The switch is refused while a turn runs, since closing the
        old provider would kill the live stream.
        """
        if self._mutation_lock.locked():
            raise SessionBusyError(
                "Can't switch profile while a turn is running — stop it or wait for it to finish."
            )
        await self._wait_for_background_title()
        async with self._mutation_lock:
            # Every fallible step first, into locals.
            new_profile = resolve_profile(self.settings, name)
            new_completion_settings = _completion_settings_for_profile(new_profile)
            new_provider = build_provider(new_profile)
            # Keep the iteration cap in effect (e.g. from /max-iterations)
            # rather than resetting it to the settings default.
            new_loop = AgentLoop(
                new_provider,
                new_completion_settings,
                self.tools,
                max_iterations=self.loop.max_iterations,
                before_round_trip=self._trim_context,
            )

            # Nothing below here can fail, so the swap is all-or-nothing.
            old_provider = self.provider
            self.profile = new_profile
            self.profile_name = name
            self.provider = new_provider
            self.completion_settings = new_completion_settings
            self.loop = new_loop
            # self.messages is intentionally left untouched: history carries over.

            # Close last; a failing aclose() must not roll back the (already
            # complete) switch, so it is only logged.
            try:
                await old_provider.aclose()
            except Exception as exc:  # noqa: BLE001 - see above
                logger.warning(
                    "closing the previous provider after a profile switch failed: %s", exc
                )

    def cancel(self) -> None:
        """Request cancellation of the running turn; a no-op when no turn
        is running.

        ``AgentLoop.cancel``'s flag is cleared only when a run ends, so
        passing it through between turns would cancel the *next* turn."""
        if self._turn_active:
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
        """The token budget for *sent* history: per-profile ``context_window``,
        falling back to the global ``AppConfig.max_context_tokens``. ``None``
        means trimming/compaction is disabled (the global default ``0``)."""
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
        """``(estimated tokens of the next request, usable history budget)``
        for the fullness indicator ("Context: 42k / 88k (48%)").

        Computed exactly as ``_trim_context`` does so they never disagree. The
        budget is ``0`` when trimming is disabled — treat that as "not
        applicable", don't divide by it."""
        current = sum(estimate_message_tokens(m) for m in self.messages)
        return current, (self._history_budget() or 0)

    async def _compact_context(self, turns: list[list[Message]]) -> Message | None:
        """Summarize ``turns`` (whole turns about to be dropped, oldest first)
        via the *active* provider.

        Returns the summary ``Message``, or ``None`` on provider error, empty
        reply, or exception — callers then fall back to plain dropping.
        Never raises: compaction failing must never fail the user's turn."""
        if not turns:
            return None
        request_messages = compaction_request_messages(turns)
        # Factual extraction with the active model: low temperature, no tools.
        summary_settings = CompletionSettings(
            model=self.completion_settings.model,
            # Pin 0.0 only if the profile sends a temperature at all: models
            # with a fixed temperature reject any value, and a failed
            # compaction costs the user their history.
            temperature=0.0 if self.completion_settings.temperature is not None else None,
            supports_vision=False,
        )
        try:
            summary_text = ""
            for_error: str | None = None
            # Short retry budget: runs mid-turn and already has a fallback.
            async for event in complete_with_retries(
                self.provider,
                request_messages,
                [],
                summary_settings,
                policy=SHORT_RETRY_POLICY,
            ):
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

    def _title_check_is_due(self) -> bool:
        """Whether this turn should spend a call on (re)naming the
        conversation — see ``_maybe_update_title`` for what it then does.

        Gates, cheapest first: the setting, a renameable conversation (has a
        recorder, no user-typed name), and the cadence. A conversation still
        on the derived placeholder skips the cadence for its *first* attempt
        only (see ``_title_attempted``), so it gets a real name after turn one.
        """
        if not self.settings.app.auto_title_conversations:
            return False
        if self.recorder is None or self.recorder.title_locked:
            return False
        if not any(message.role == "assistant" for message in self.messages):
            # Nothing to name it from yet: a turn that failed before the
            # model said anything, or a history-only session.
            return False
        if self.recorder.title_source != TITLE_GENERATED and not self._title_attempted:
            return True
        return self._turns_since_title >= max(1, self.settings.app.auto_title_interval_turns)

    async def _maybe_update_title(self) -> ConversationTitled | None:
        """Name (or re-name) this conversation from its own content.

        Same contract as ``_compact_context``: active provider, no tools,
        never raises — every failure is logged and the next due turn retries.

        Returns the event to emit when the title changed; ``None`` when not
        due, the model answered ``KEEP``, or the call failed.
        """
        self._turns_since_title += 1
        if not self._title_check_is_due():
            return None
        self._title_attempted = True
        recorder = self.recorder
        assert recorder is not None  # guaranteed by _title_check_is_due
        # Offer the current title for KEEP only if it is model-written;
        # otherwise a first-line placeholder could survive forever.
        already_named = recorder.title_source == TITLE_GENERATED
        request_messages = title_request_messages(
            self.messages, current_title=recorder.title if already_named else None
        )
        # As in _compact_context: pin 0.0 only if the profile sends a
        # temperature at all.
        title_settings = CompletionSettings(
            model=self.completion_settings.model,
            temperature=0.0 if self.completion_settings.temperature is not None else None,
            supports_vision=False,
        )
        try:
            reply = ""
            for_error: str | None = None
            async for event in self.provider.complete(request_messages, [], title_settings):
                if isinstance(event, TextFinished):
                    reply = event.text
                elif isinstance(event, AgentError):
                    for_error = f"{event.layer}: {event.message}"
            if for_error is not None:
                logger.debug(
                    "conversation titling failed, keeping the current title: %s", for_error
                )
                return None
        except Exception as exc:  # noqa: BLE001 - titling must never fail a turn
            logger.debug("conversation titling failed, keeping the current title: %s", exc)
            return None

        # Restart the cadence even on KEEP, or it would re-check every turn.
        self._turns_since_title = 0
        title = clean_title(reply)
        if title is None or title == recorder.title:
            # KEEP, unusable, or unchanged. A KEEP must not promote a derived
            # placeholder to TITLE_GENERATED, or it would never be replaced.
            return None
        recorder.set_title(title, source=TITLE_GENERATED)
        logger.info("conversation %s titled %r", recorder.conversation_id[:8], title)
        return ConversationTitled(conversation_id=recorder.conversation_id, title=title)

    async def _apply_trim_plan(
        self, plan: TrimPlan, *, summarize: bool = True
    ) -> ContextTrimmed | None:
        """Summarize ``plan.dropped_turns`` (or plain-drop them if that fails,
        or if ``summarize`` is false) and apply the plan; shared by
        ``_trim_context``, ``compact_now`` and ``clear_context``.

        Mutates ``self.messages`` in place because ``AgentLoop.run`` appends
        to that same list object."""
        # Captured before the await: `plan` holds exact slices, so any change
        # during summarization would make the slice assignment drop messages.
        # The mutation lock prevents this; the check catches a caller without it.
        generation = self._history_generation
        summary_message = await self._compact_context(plan.dropped_turns) if summarize else None
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
        length_change = len(new_messages) - len(self.messages)
        self.messages[:] = new_messages
        self._history_generation += 1
        if self._persist_index is not None:
            self._persist_index = max(0, self._persist_index + length_change)
        return ContextTrimmed(
            dropped_turns=dropped_turns,
            estimated_tokens=estimated_tokens,
            summarized=summarized,
            summary_tokens=summary_tokens,
        )

    async def _trim_context(self) -> ContextTrimmed | None:
        """Summarize (or drop) the oldest whole turns once the in-memory
        history exceeds ``_history_budget``, so the provider never rejects a
        request for length.

        Returns a ``ContextTrimmed`` event for the frontend when something
        changed, ``None`` otherwise (disabled, or already under budget)."""
        budget = self._history_budget()
        if budget is None:
            return None  # 0 (global default or resolved profile window) disables trimming
        plan = plan_trim(self.messages, budget)
        if not plan.was_trimmed:
            return None
        return await self._apply_trim_plan(plan)

    async def compact_now(self, *, min_recent_turns: int = 4) -> ContextTrimmed | None:
        """Manual compaction (CLI ``/compact``, GUI "Compact Conversation").

        Summarizes everything but the most recent ``min_recent_turns`` turns
        now, regardless of budget, so the user can compact at a natural task
        boundary. Same path as automatic trimming, with the threshold forced
        to ``0``. Returns ``None`` if there are fewer turns than the floor.

        Raises ``SessionBusyError`` if a turn or profile switch is in flight
        (see ``SessionBusyError``)."""
        if self._mutation_lock.locked():
            raise SessionBusyError(
                "Can't compact while a turn is running — stop it or wait for it to finish."
            )
        await self._wait_for_background_title()
        async with self._mutation_lock:
            plan = plan_trim(self.messages, 0, min_recent_turns=min_recent_turns)
            if not plan.was_trimmed:
                return None
            return await self._apply_trim_plan(plan)

    async def clear_context(self) -> ContextTrimmed | None:
        """Drop every turn from the history sent to the model, keeping the
        system messages, so the next turn starts from a clean context in the
        same conversation, with the same tools and MCP servers.

        Used by repeating workflow steps (``aida.core.workflows``), where a
        state file, not the chat history, carries progress between passes.
        Nothing is summarized, so no provider call is made and nothing of the
        earlier passes is carried forward. The stored conversation (database
        and Markdown record) keeps every turn. Returns ``None`` when there
        was nothing to drop.

        Raises ``SessionBusyError`` if a turn or profile switch is in flight."""
        if self._mutation_lock.locked():
            raise SessionBusyError(
                "Can't clear the context while a turn is running — stop it or wait for it "
                "to finish."
            )
        await self._wait_for_background_title()
        async with self._mutation_lock:
            plan = plan_trim(self.messages, 0, min_recent_turns=0)
            if not plan.was_trimmed:
                return None
            return await self._apply_trim_plan(plan, summarize=False)

    async def _retrieve_context(self, user_text: str) -> dict[str, list[RetrievedPassage]]:
        """Query every active knowledge base for this turn's question.

        A failing knowledge base (embedding-profile mismatch, network error,
        ...) is logged and skipped, never raised. One returning nothing above
        its score threshold contributes no entry."""
        # All knowledge bases at once: each is mostly waiting on its own
        # embeddings request, and scoring runs in a worker thread.
        results = await asyncio.gather(
            *(retrieve_from_active_kb(kb, user_text) for kb in self.active_knowledge_bases),
            return_exceptions=True,
        )
        passages_by_kb: dict[str, list[RetrievedPassage]] = {}
        for kb, outcome in zip(self.active_knowledge_bases, results, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome  # cancellation and the like are not "a bad KB"
                logger.warning("knowledge base %r retrieval failed: %s", kb.name, outcome)
                continue
            if outcome:
                passages_by_kb[kb.name] = outcome
        return passages_by_kb

    def _persist_new_messages(self, context_message: Message | None) -> None:
        """Record messages appended since ``self._persist_index`` and advance it.

        Skips ``context_message`` by identity: the ephemeral per-turn context
        must never reach the DB. No-op with no recorder or outside a turn."""
        if self.recorder is None or self._persist_index is None:
            return
        while self._persist_index < len(self.messages):
            message = self.messages[self._persist_index]
            if message is not context_message:
                self.recorder.record_message(message)
            self._persist_index += 1

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

        Wraps ``_run_turn``. As an async generator it releases the lock
        whether the turn completes, raises, or is abandoned (``aclose()``
        unwinds the ``async with``).

        Raises ``SessionBusyError`` if another turn, a compaction, or a
        profile switch is in flight. Queued interjections
        (``queue_user_message``) never touch this lock and still work.

        Titling runs here, *after* the lock is released (it only writes the
        title), so every frontend gets named conversations without extra
        wiring — see ``_maybe_update_title``. With ``on_background_event``
        set it runs as a task after this generator ends; the next ``send()``
        waits for it."""
        if self._mutation_lock.locked():
            raise SessionBusyError("A turn is already running in this session.")
        await self._wait_for_background_title()
        if self._mutation_lock.locked():
            raise SessionBusyError("A turn is already running in this session.")
        async with self._mutation_lock:
            self._turn_active = True
            try:
                async for event in self._run_turn(
                    user_text,
                    images=images,
                    attachment_paths=attachment_paths,
                    attachment_texts=attachment_texts,
                ):
                    yield event
            finally:
                self._turn_active = False
                self.loop.clear_cancel()
        if self.on_background_event is not None:
            self._title_task = asyncio.create_task(self._title_in_background())
            return
        title_event = await self._maybe_update_title()
        if title_event is not None:
            yield title_event

    async def _title_in_background(self) -> None:
        """``_maybe_update_title`` as a task after the turn, delivering its
        event through ``on_background_event``. Never raises: titling is
        cosmetic (see ``_maybe_update_title``)."""
        try:
            event = await self._maybe_update_title()
        except Exception:  # noqa: BLE001 - a cosmetic call must never surface
            logger.warning("background conversation titling failed", exc_info=True)
            return
        listener = self.on_background_event
        if event is not None and listener is not None:
            try:
                listener(event)
            except Exception:  # noqa: BLE001
                logger.warning("delivering the conversation title failed", exc_info=True)

    async def _wait_for_background_title(self) -> None:
        """Let a running background title call finish before anything
        else uses the provider or the history. Titling takes seconds and
        never raises, so waiting is simpler and safer than cancelling."""
        task = self._title_task
        if task is not None and not task.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(task)
        self._title_task = None

    async def _run_turn(
        self,
        user_text: str,
        *,
        images: list[ImageRef] | None = None,
        attachment_paths: list[str] | None = None,
        attachment_texts: dict[str, str] | None = None,
    ):
        """Run one turn.

        Pending notices and retrieved knowledge-base passages are injected as
        one *strictly ephemeral* message: appended to ``self.messages`` for
        this turn, never persisted, and removed when the turn ends, so stale
        context is never stored or re-sent.

        Args:
            user_text: The user's message.
            images: GUI image attachments; whether they reach the model
                depends on ``supports_vision`` and the recency cap (see
                ``aida.providers.vision``).
            attachment_paths: Files a *person* attached, copied into the
                conversation's folder so they outlive the original and the
                transcript is complete (see ``aida.documents.attachments``).
                Never used for files the agent opened itself.
            attachment_texts: Already-extracted text of those attachments,
                saved beside the copies."""
        user_message = Message(role="user", content=user_text, images=list(images or []))
        self.messages.append(user_message)
        self._history_generation += 1  # see __init__ / _apply_trim_plan
        if self.recorder is not None:
            self.recorder.record_message(user_message)
            if attachment_paths:
                # Only after the message is recorded; keep_attachments never
                # raises. Texts are passed in so large PDFs aren't parsed twice.
                self.recorder.keep_attachments(
                    list(attachment_paths), texts=attachment_texts or None
                )
        # Trim only the *sent* history (the DB keeps everything), before the
        # persist index is captured. Whole turns only, so tool results stay
        # with their calls.
        trim_event = await self._trim_context()
        if trim_event is not None:
            yield trim_event
        self._persist_index = len(self.messages)

        context_message: Message | None = None
        # Notices + retrieved passages share one ephemeral message, so removal
        # and persistence exclusion stay a single identity check.
        context_parts: list[str] = []
        if self._pending_notices:
            context_parts.extend(self._pending_notices)
            self._pending_notices.clear()
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
                context_parts.append(_format_retrieved_context(passages_by_kb))
        if context_parts:
            context_message = Message(role="user", content="\n\n".join(context_parts))
            self.messages.append(context_message)

        try:
            async for event in self.loop.run(self.messages):
                yield event
                if isinstance(event, UsageInfo):
                    self.total_input_tokens += event.input_tokens
                    self.total_output_tokens += event.output_tokens
                # Persist each finalized message as soon as AgentLoop appends
                # it ("crash-safe enough"; see aida.persistence.recorder).
                self._persist_new_messages(context_message)
                if self.recorder is None:
                    continue
                if isinstance(event, ImageArtifactCreated):
                    self.recorder.record_artifact_fields(
                        artifact_id=event.artifact_id,
                        kind="ImageArtifact",
                        path=event.path,
                        mime_type=event.mime_type,
                        call_id=event.call_id,
                        # The owning tool-result message is appended right
                        # after this event, so next_message_seq() is its seq;
                        # GUI resume uses it to place the artifact.
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

            # The final assistant message is appended with no event after it,
            # so persist it here. Reached only when the turn completes, which
            # is the intended "crash-safe enough" boundary.
            self._persist_new_messages(context_message)
        finally:
            self._persist_index = None
            # Remove the ephemeral message *by identity*: list.remove() uses ==
            # and could delete an identical-looking real message instead.
            if context_message is not None:
                for position in range(len(self.messages) - 1, -1, -1):
                    if self.messages[position] is context_message:
                        del self.messages[position]
                        break
            # One transcript write per turn. A failed export must not break the
            # turn: messages are already in the DB and the next flush rewrites it.
            if self.recorder is not None:
                with contextlib.suppress(Exception):
                    self.recorder.flush_transcript()

    async def aclose(self) -> None:
        """Release the current provider's connections, every active
        knowledge base's connection/embeddings-provider, and the
        recorder's DB connection. Must be called when the session ends, or
        ``asyncio.run()`` may close the loop under an open HTTP client and
        print a spurious traceback.

        Each step is guarded separately so one failure doesn't leak the rest.
        A running background title call is cancelled, not awaited."""
        task = self._title_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        self._title_task = None
        try:
            await self.provider.aclose()
        except Exception:  # noqa: BLE001 - best-effort cleanup on the way out
            logger.warning("closing the provider failed", exc_info=True)
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
            with contextlib.suppress(Exception):
                self.recorder.store.close()


@dataclass(frozen=True)
class StartupNotice:
    """One thing ``start_session`` wants the user to know: a folder it
    created, a knowledge base it skipped, an MCP server that failed to
    start. ``level`` is ``"info"`` or ``"warning"``; ``source`` is the
    area (``workspace``, ``knowledge``, ``mcp``, ``ocr``, ``conversations``).
    """

    level: str
    source: str
    message: str

    @property
    def line(self) -> str:
        """The terminal form the CLI has always printed."""
        prefix = "warning: " if self.level == "warning" else ""
        return f"[{self.source}] {prefix}{self.message}"


class StartupNotices:
    """Collects ``StartupNotice``s during startup. With ``echo`` (CLI,
    ``aida run``, workflows) each is also printed; the GUI turns echo off and
    shows them in the chat panel (``aida-gui`` has no console on Windows).
    """

    def __init__(self, *, echo: bool = True) -> None:
        self.echo = echo
        self.items: list[StartupNotice] = []

    def add(self, level: str, source: str, message: str) -> None:
        notice = StartupNotice(level=level, source=source, message=message)
        self.items.append(notice)
        if self.echo:
            print(notice.line)

    def info(self, source: str, message: str) -> None:
        self.add("info", source, message)

    def warning(self, source: str, message: str) -> None:
        self.add("warning", source, message)


def _ensure_workspace_folders(
    workspace: WorkspaceConfig, notices: StartupNotices | None = None
) -> None:
    """Create missing workspace folders on every session start, so a
    workspace is usable as soon as it's defined.

    Failures (permissions, file in the way, unmounted drive) only warn, like
    ``validate_workspace``; ``SafetyGuard`` checks still apply.

    Deliberate asymmetry: a target folder is created with parents, but a
    **source** folder only if its parent exists. A missing source usually
    means an unmounted network share, and ``mkdir(parents=True)`` would
    fabricate empty local directories that shadow the mount point and make
    the agent report an empty folder instead of "not mounted"."""
    if notices is None:
        notices = StartupNotices()
    creations = [(folder, False) for folder in workspace.source_folders]
    creations.append((workspace.target_folder, True))
    for folder, with_parents in creations:
        if not folder:
            continue
        path = Path(folder).expanduser()
        if path.exists():
            continue
        if not with_parents and not path.parent.exists():
            notices.warning(
                "workspace",
                f"source folder {path} does not exist and neither does its "
                "parent — not creating it (an unmounted network share?)",
            )
            logger.warning("source folder %s missing along with its parent — not created", path)
            continue
        try:
            path.mkdir(parents=with_parents, exist_ok=True)
            notices.info("workspace", f"created folder: {path}")
            logger.info("created workspace folder: %s", path)
        except OSError as exc:
            notices.warning("workspace", f"could not create folder {path}: {exc}")
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
    echo_notices: bool = True,
) -> tuple[ChatSession, McpManager | None]:
    """Start a session, releasing everything it acquired if any step fails.

    ``echo_notices``: print startup notices as they happen (CLI). They are
    collected on ``session.startup_notices`` either way; the GUI passes
    ``False`` and shows them in the chat panel.

    The work is in ``_start_session``; this wrapper owns the
    ``AsyncExitStack``. The SQLite store, each knowledge base's connection
    and embeddings client, and the MCP subprocesses must be released if
    startup fails before a ``ChatSession`` owns them. Each release is
    registered right after its acquisition; ``pop_all()`` on success
    transfers ownership to the session.
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
            echo_notices=echo_notices,
        )
        # The session owns these now; unregister every cleanup.
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
    echo_notices: bool = True,
) -> tuple[ChatSession, McpManager | None]:
    """Shared session-startup logic for ``aida chat`` and
    ``aida conversations resume`` — resolves the workspace (if any), starts
    only the MCP servers that end up enabled, builds the
    ``ConversationRecorder`` (fresh or resumed), and returns a ready
    ``ChatSession``. Raises ``UnknownProfileError`` / ``UnknownWorkspaceError``
    / ``UnknownMcpServerError`` / ``ConversationNotFoundError`` — callers
    print and exit(1) rather than letting a traceback through.

    ``confirm_callback`` goes to the ``SafetyGuard``, MCP manager and OCR
    backend; ``None`` means ``cli_confirm`` (blocking terminal prompt). The
    GUI passes its own modal-dialog callback.

    ``origin`` tags a new conversation (e.g. created by
    ``aida.core.workflows.run_workflow``) so listings can tell it apart from
    an interactive chat (``None``). Ignored on resume.
    """
    notices = StartupNotices(echo=echo_notices)
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
    # Only the default is wrapped; an explicit callback (GUI, tests, headless)
    # is used as given. A fresh wrapper per call scopes remembered approvals
    # to this session.
    confirm_callback = confirm_callback or RememberingConfirm(cli_confirm)
    store = ConversationStore()
    # Registered once here so every later early exit releases it.
    stack.callback(store.close)
    artifact_store = ArtifactStore()
    # Explicit argument > $AIDA_USER > config.yaml active_user; "" = no user.
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

    # Global identity framing, kept separate from extra_context_texts because
    # it must precede the workspace's system_prompt. Per-user text falls back
    # to the install-wide one (see AppConfig.context_for_user).
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
        # Expand {user} before anything reads the folders (validation,
        # creation, guard roots, context block); a literal ".../{user}/" root
        # would make every real write prompt as outside the workspace.
        workspace = resolve_workspace_for_user(workspace, active_user)
        validation = validate_workspace(settings, workspace)
        for warning in validation.warnings:
            notices.warning("workspace", str(warning))
            logger.warning("workspace %r: %s", workspace.name, warning)
        if not validation.ok:
            raise UnknownWorkspaceError(
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
        _ensure_workspace_folders(workspace, notices)

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
    # One well-known scratch folder agents and MCP servers can write to
    # without confirmation, so temporary files don't land in random places.
    scratch = ensure_scratch_dir(settings.app.scratch_dir)
    # Single source for both the guard roots and the model's folder block,
    # kept on the session for live rebuilds (see WorkspaceFolderState).
    # Artifacts and scratch dirs are always allowed — but not the rest of
    # ~/.aida (config, secrets refs, DB stay gated) — and are not advertised
    # to the model: they are AIDA plumbing.
    folder_state = WorkspaceFolderState(
        source_folders=list(workspace.source_folders) if workspace else [],
        target_folder=workspace.target_folder if workspace else None,
        guard_extra_roots=[*settings.app.allowed_folders, str(artifacts_dir()), str(scratch)],
        context_allowed_folders=list(settings.app.allowed_folders),
        sidecar_dirname=sidecar_dirname,
        safety_mode=effective_safety_mode,
        scratch_dir=str(scratch),
        internal_roots=[str(artifacts_dir()), str(scratch)],
        in_workspace=workspace is not None,
    )
    guard = SafetyGuard.for_workspace(
        source_folders=folder_state.source_folders,
        target_folder=folder_state.target_folder,
        global_allowed_folders=folder_state.guard_extra_roots,
        mode=effective_safety_mode,
        confirm_callback=confirm_callback,
        # Global allowlist plus the workspace's own.
        command_allowlist=settings.app.command_allowlist
        + (workspace.command_allowlist if workspace else []),
        # Skill scripts/reference files are readable without asking; writes
        # are always confirmed (SafetyGuard.read_only_roots, docs/guides/skills.md).
        read_only_folders=[str(skills_dir())],
    )

    # Tell the model the workspace's actual paths (skills are static and
    # shared, so they can't carry per-workspace data).
    extra_context_texts: list[str] = []
    folder_context = build_workspace_context_block(
        source_folders=folder_state.source_folders,
        target_folder=folder_state.target_folder,
        global_allowed_folders=folder_state.context_allowed_folders,
        sidecar_dirname=folder_state.sidecar_dirname,
        safety_mode=folder_state.safety_mode,
        scratch_dir=folder_state.scratch_dir,
    )
    if folder_context:
        extra_context_texts.append(folder_context)

    # Code templates as a compact name+docstring list, so generated code
    # follows the workspace's conventions.
    if workspace and workspace.templates_dir:
        templates_context = templates_context_text(load_templates(Path(workspace.templates_dir)))
        if templates_context:
            extra_context_texts.append(templates_context)

    # Tell the model its interpreter and allowlist so it doesn't probe the
    # environment with confirmation-gated shell commands.
    coding_context = build_coding_context_block(
        python_interpreter=workspace.python_interpreter if workspace else None,
        command_allowlist=settings.app.command_allowlist
        + (workspace.command_allowlist if workspace else []),
        scripting_enabled=bool(workspace and workspace.scripting_enabled),
    )
    if coding_context:
        extra_context_texts.append(coding_context)

    # Open each configured knowledge base once (index connection +
    # embeddings provider); retrieval runs per turn in send(). A
    # misconfigured reference warns and is skipped, never aborts startup.
    active_knowledge_bases: list[ActiveKnowledgeBase] = []
    for kb_name in workspace.knowledge_bases if workspace else []:
        kb_config = settings.knowledge.knowledge_bases.get(kb_name)
        if kb_config is None:
            notices.warning(
                "knowledge", f"workspace references unknown knowledge base {kb_name!r} — skipping"
            )
            logger.warning(
                "workspace %r references unknown knowledge base %r",
                effective_workspace_name,
                kb_name,
            )
            continue
        if not kb_config.embedding_profile:
            notices.warning(
                "knowledge",
                f"knowledge base {kb_name!r} has no embedding_profile configured — skipping",
            )
            continue
        embedding_profile = settings.providers.embedding_profiles.get(kb_config.embedding_profile)
        if embedding_profile is None:
            notices.warning(
                "knowledge",
                f"knowledge base {kb_name!r} references unknown embedding profile "
                f"{kb_config.embedding_profile!r} — skipping",
            )
            continue
        try:
            embeddings_provider = build_embeddings_provider(embedding_profile)
        except UnknownProviderKindError as exc:
            notices.warning("knowledge", f"knowledge base {kb_name!r}: {exc} — skipping")
            continue
        conn = kb_index.connect(knowledge_db_path(kb_name))
        # Registered immediately; note the embeddings provider holds an HTTP
        # client that must be closed too.
        stack.push_async_callback(_close_knowledge_base, conn, embeddings_provider)
        active_knowledge_bases.append(
            ActiveKnowledgeBase(
                name=kb_name,
                connection=conn,
                embeddings_provider=embeddings_provider,
                embedding_profile_name=kb_config.embedding_profile,
            )
        )
        notices.info("knowledge", f"{kb_name}: {kb_index.chunk_count(conn)} chunk(s) indexed")
        logger.debug(
            "knowledge base %r ready with %d chunk(s)", kb_name, kb_index.chunk_count(conn)
        )

    mcp_manager: McpManager | None = None
    tools = default_native_tools()
    tools.update(default_file_tools(guard))
    tools.update(default_document_tools(guard, artifact_store, sidecar_dirname=sidecar_dirname))
    tools.update(default_coding_tools(guard, workspace=workspace, scratch_dir=scratch))
    tools.update(default_web_tools(guard))
    if mcp_servers:
        # Same confirm_callback as SafetyGuard, so confirm-flagged MCP tools
        # reach the real prompt/dialog; omitting it would make them silently
        # always refuse.
        mcp_manager = McpManager(
            mcp_servers,
            artifact_store=artifact_store,
            confirm_callback=confirm_callback,
            scratch_dir=scratch,
        )
        # Registered *before* start_all: a non-McpServerError failure partway
        # through must not orphan already-launched subprocesses.
        stack.push_async_callback(mcp_manager.aclose)
        mcp_tools = await mcp_manager.start_all()
        tools.update(mcp_tools)
        for skill in mcp_manager.skills():
            if skill not in all_skill_names:
                all_skill_names.append(skill)
        # Pass each server's `instructions=` (e.g. pyirena-mcp's workflow
        # guide) to the model.
        for name, instructions in mcp_manager.server_instructions().items():
            extra_context_texts.append(f"# {name} — server instructions\n\n{instructions}")
        for name in mcp_manager.running_server_names:
            count = sum(1 for t in mcp_tools if t.startswith(f"{name}{NAMESPACE_SEPARATOR}"))
            notices.info("mcp", f"{name}: {count} tool(s)")
            logger.debug("mcp server %r started with %d tool(s)", name, count)
        for name, error in mcp_manager.start_errors.items():
            notices.warning("mcp", f"{name}: FAILED to start — {error}")
            logger.warning("mcp server %r failed to start: %s", name, error)

    # Warn before the request: providers with a tools-array cap (ANL Argo:
    # 128) fail with an opaque 400 'array_above_max_length'. Disabled tools
    # (McpServerConfig.disabled_tools) are never sent and don't count.
    if len(tools) > _LARGE_TOOL_COUNT_WARNING_THRESHOLD:
        notices.warning(
            "mcp",
            f"{len(tools)} tool(s) enabled this session — some providers "
            "(e.g. the ANL Argo gateway, at 128) reject the whole request once the tools "
            "array is too long; disable unused tools per-server (Settings -> Manage MCP "
            "Servers, or McpServerConfig.disabled_tools) if a chat message fails with "
            "'array_above_max_length'",
        )
        logger.warning(
            "session has %d tools enabled, above the %d-tool advisory threshold — some "
            "providers reject requests past a fixed tools-array length (128 via the ANL "
            "Argo proxy); trim with a server's disabled_tools if you hit that",
            len(tools),
            _LARGE_TOOL_COUNT_WARNING_THRESHOLD,
        )

    if resume_conversation_id:
        recorder = ConversationRecorder(
            store,
            artifact_store,
            records_dir,
            conversation_id=resume_conversation_id,
            resume=True,
            transcript_tool_results=settings.app.transcript_tool_results,
        )
        initial_messages: list[Message] | None = recorder.load_history()
        notices.info("conversations", f"resumed with {len(initial_messages)} prior message(s)")
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
            transcript_tool_results=settings.app.transcript_tool_results,
        )
        initial_messages = None

    # Figure tools are registered after the recorder because they need its
    # attachments folder (passed as a callable, re-read per call).
    # OCR is triple opt-in: workspace use_ocr, an API key, and per-document
    # user approval (OcrBackend.approved_for). A None backend means "off" —
    # nothing downstream has to check a flag.
    ocr_backend = None
    if workspace is not None and workspace.use_ocr:
        ocr_key = get_secret(OCR_SECRET_REF)
        if ocr_key:
            ocr_backend = OcrBackend(api_key=ocr_key, confirm=confirm_callback)
        else:
            notices.warning(
                "ocr",
                f"workspace {workspace.name!r} has use_ocr enabled but no API key is set "
                f"(Settings, or ${env_var_name(OCR_SECRET_REF)}) — falling back to the built-in "
                f"figure extractor.",
            )
            logger.warning(
                "workspace %r: use_ocr enabled but no %r secret", workspace.name, OCR_SECRET_REF
            )
    tools.update(default_figure_tools(recorder.attachments_dir, ocr=ocr_backend))

    # No try/except: failures here (UnknownProviderKindError, unreadable
    # skill files, locked/corrupt DB above) are cleaned up by the caller's
    # exit stack.
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
        guard=guard,
        # Kept for workspace-less chats too, so global allowed folders can
        # still be changed live; in_workspace gates the workspace-only edits.
        folder_state=folder_state,
    )
    session.startup_notices = notices.items
    return session, mcp_manager


async def _close_knowledge_base(conn, embeddings_provider) -> None:
    """Release one knowledge base's two resources, each independently.

    Suppressed individually so a failing ``aclose`` can't leave the SQLite
    connection open."""
    with contextlib.suppress(Exception):
        await embeddings_provider.aclose()
    with contextlib.suppress(Exception):
        conn.close()


__all__ = [
    "ChatSession",
    "NoWorkspaceFoldersError",
    "StartupNotice",
    "StartupNotices",
    "WorkspaceFolderState",
    "UnknownMcpServerError",
    "UnknownProfileError",
    "UnknownWorkspaceError",
    "cli_confirm",
    "resolve_mcp_servers",
    "resolve_profile",
    "start_session",
]
