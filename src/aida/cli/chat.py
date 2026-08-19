"""``aida chat`` — interactive streaming chat with a provider (Phase 2).

The reference frontend implementation (PLAN.md §10 Phase 2 row): every
printed line maps 1:1 to an event from ``aida.core.events``, so this file
doubles as the spec any future frontend (Qt in Phase 5, ...) needs to match.
"""

from __future__ import annotations

import argparse
import asyncio

from aida.config.paths import skills_dir
from aida.config.settings import McpConfig, McpServerConfig, Settings, load_settings
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
from aida.mcp.groups import resolve_explicit, resolve_group
from aida.mcp.manager import McpManager
from aida.providers.base import CompletionSettings, Message
from aida.providers.profiles import build_provider


class UnknownMcpServerError(Exception):
    """Raised when ``--mcp`` names a server that isn't in ``mcp.json``."""


class UnknownProfileError(Exception):
    """Raised when ``--profile``/``/profile`` names a profile that isn't
    configured in ``providers.yaml``."""


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
    message history, and native tools. Mid-session ``/profile`` switches
    swap the provider + loop but keep ``messages`` (and thus history)."""

    def __init__(
        self,
        settings: Settings,
        profile_name: str,
        tools: dict[str, NativeTool] | None = None,
        skill_names: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.tools = tools if tools is not None else default_native_tools()
        self.profile_name = profile_name
        self.profile = resolve_profile(settings, profile_name)
        self.provider = build_provider(self.profile)
        self.completion_settings = CompletionSettings(model=self.profile.model)
        self.loop = AgentLoop(self.provider, self.completion_settings, self.tools)

        skill_texts = load_skill_texts(skills_dir(), skill_names or [])
        system_message = build_system_message(None, skill_texts)
        self.messages: list[Message] = [system_message] if system_message.content else []

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
        self.messages.append(Message(role="user", content=user_text))
        async for event in self.loop.run(self.messages):
            yield event

    async def aclose(self) -> None:
        """Release the current provider's connections. Must be called when
        the session ends (see ``_run_repl``'s try/finally) — otherwise
        ``asyncio.run()`` can close the event loop out from under an open
        HTTP client and print a spurious traceback on exit."""
        await self.provider.aclose()


async def _run_repl(session: ChatSession) -> None:
    print(
        f"AIDA chat — profile: {session.profile_name} "
        f"({session.profile.kind}, model={session.profile.model})"
    )
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida chat")
    parser.add_argument("--profile", required=True, help="Provider profile name from providers.yaml")
    parser.add_argument(
        "--skills", default="", help="Comma-separated skill names to load into the system context"
    )
    parser.add_argument(
        "--mcp-group",
        default="",
        help="Named MCP server group from mcp.json to enable (see each server's 'groups' key)",
    )
    parser.add_argument(
        "--mcp",
        default="",
        help="Comma-separated MCP server names to enable directly, bypassing groups",
    )
    return parser


async def _async_main(
    settings: Settings,
    profile_name: str,
    skill_names: list[str],
    mcp_servers: list[McpServerConfig],
) -> int:
    mcp_manager: McpManager | None = None
    tools = default_native_tools()
    all_skill_names = list(skill_names)

    if mcp_servers:
        mcp_manager = McpManager(mcp_servers)
        mcp_tools = await mcp_manager.start_all()
        tools.update(mcp_tools)
        for skill in mcp_manager.skills():
            if skill not in all_skill_names:
                all_skill_names.append(skill)

        for name in mcp_manager.running_server_names:
            server_tool_count = sum(1 for t in mcp_tools if t.startswith(f"{name}."))
            print(f"[mcp] {name}: {server_tool_count} tool(s)")
        for name, error in mcp_manager.start_errors.items():
            print(f"[mcp] {name}: FAILED to start — {error}")

    try:
        session = ChatSession(settings, profile_name, tools=tools, skill_names=all_skill_names)
    except UnknownProfileError as exc:
        print(str(exc))
        if mcp_manager is not None:
            await mcp_manager.aclose()
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
    try:
        mcp_servers = resolve_mcp_servers(settings.mcp, group=args.mcp_group, names=mcp_names)
    except UnknownMcpServerError as exc:
        print(str(exc))
        return 1

    return asyncio.run(_async_main(settings, args.profile, skill_names, mcp_servers))
