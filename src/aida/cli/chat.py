"""``aida chat`` — interactive streaming chat with a provider (Phase 2),
now workspace-aware and persisted (Phase 4).

The reference frontend implementation (PLAN.md §10 Phase 2 row): every
printed line maps 1:1 to an event from ``aida.core.events``, so this file
doubles as the spec any future frontend (Qt in Phase 5, ...) needs to match.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.config.paths import ensure_records_dir, skills_dir
from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    Settings,
    WorkspaceConfig,
    load_settings,
)
from aida.core.agent import AgentLoop
from aida.core.context import build_system_message, load_skill_texts
from aida.core.events import (
    AgentError,
    AgentEvent,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
    UsageInfo,
)
from aida.core.tools import NativeTool, default_native_tools
from aida.documents.tools import default_document_tools
from aida.mcp.groups import resolve_explicit, resolve_group
from aida.mcp.manager import McpManager
from aida.persistence.recorder import ConversationNotFoundError, ConversationRecorder
from aida.persistence.store import ConversationStore
from aida.providers.base import CompletionSettings, Message
from aida.providers.profiles import build_provider
from aida.workspace.files import default_file_tools
from aida.workspace.safety import ConfirmationRequest, ConfirmCallback, SafetyGuard
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
        pass  # available for a future --verbose flag; not printed by default
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
    ) -> None:
        self.settings = settings
        self.tools = tools if tools is not None else default_native_tools()
        self.profile_name = profile_name
        self.profile = resolve_profile(settings, profile_name)
        self.provider = build_provider(self.profile)
        self.completion_settings = CompletionSettings(model=self.profile.model)
        self.loop = AgentLoop(self.provider, self.completion_settings, self.tools)
        self.recorder = recorder

        skill_texts = load_skill_texts(skills_dir(), skill_names or [])
        system_message = build_system_message(system_prompt, skill_texts)
        history = list(initial_messages) if initial_messages else []
        self.messages: list[Message] = ([system_message] if system_message.content else []) + history

    async def switch_profile(self, name: str) -> None:
        new_profile = resolve_profile(self.settings, name)  # validate before tearing anything down
        old_provider = self.provider
        self.profile = new_profile
        self.profile_name = name
        self.provider = build_provider(self.profile)
        self.completion_settings = CompletionSettings(model=self.profile.model)
        self.loop = AgentLoop(self.provider, self.completion_settings, self.tools)
        # self.messages is intentionally left untouched: history carries over.
        await old_provider.aclose()

    def cancel(self) -> None:
        self.loop.cancel()

    async def send(self, user_text: str):
        user_message = Message(role="user", content=user_text)
        self.messages.append(user_message)
        if self.recorder is not None:
            self.recorder.record_message(user_message)
        persisted = len(self.messages)

        async for event in self.loop.run(self.messages):
            yield event
            if self.recorder is None:
                continue
            # Persist each finalized message the instant it lands in
            # self.messages (agent.py appends assistant/tool messages
            # in-place as the turn progresses) — this is the "crash-safe
            # enough" write path: everything up to the currently-streaming
            # text is already durable. See aida.persistence.recorder's
            # module docstring for the full trade-off.
            while persisted < len(self.messages):
                self.recorder.record_message(self.messages[persisted])
                persisted += 1
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
        if self.recorder is not None:
            while persisted < len(self.messages):
                self.recorder.record_message(self.messages[persisted])
                persisted += 1

    async def aclose(self) -> None:
        """Release the current provider's connections and the recorder's DB
        connection. Must be called when the session ends (see
        ``_run_repl``'s try/finally) — otherwise ``asyncio.run()`` can close
        the event loop out from under an open HTTP client and print a
        spurious traceback on exit."""
        await self.provider.aclose()
        if self.recorder is not None:
            self.recorder.store.close()


async def _run_repl(session: ChatSession) -> None:
    print(
        f"AIDA chat — profile: {session.profile_name} "
        f"({session.profile.kind}, model={session.profile.model})"
    )
    if session.recorder is not None:
        print(f"[conversations] recording as {session.recorder.conversation_id}")
    print("Type /exit to quit, /profile NAME to switch profiles mid-session.\n")

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

    guard = SafetyGuard.for_workspace(
        source_folders=workspace.source_folders if workspace else [],
        target_folder=workspace.target_folder if workspace else None,
        global_allowed_folders=settings.app.allowed_folders,
        mode=workspace.safety if workspace else settings.app.default_safety_mode,
        confirm_callback=confirm_callback,
    )

    mcp_manager: McpManager | None = None
    tools = default_native_tools()
    tools.update(default_file_tools(guard))
    tools.update(default_document_tools(guard, artifact_store, sidecar_dirname=sidecar_dirname))
    if mcp_servers:
        mcp_manager = McpManager(mcp_servers, artifact_store=artifact_store)
        mcp_tools = await mcp_manager.start_all()
        tools.update(mcp_tools)
        for skill in mcp_manager.skills():
            if skill not in all_skill_names:
                all_skill_names.append(skill)
        for name in mcp_manager.running_server_names:
            count = sum(1 for t in mcp_tools if t.startswith(f"{name}."))
            print(f"[mcp] {name}: {count} tool(s)")
        for name, error in mcp_manager.start_errors.items():
            print(f"[mcp] {name}: FAILED to start — {error}")

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
        )
    except UnknownProfileError:
        if mcp_manager is not None:
            await mcp_manager.aclose()
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
