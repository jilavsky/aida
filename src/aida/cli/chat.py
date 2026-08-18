"""``aida chat`` — interactive streaming chat with a provider (Phase 2).

The reference frontend implementation (PLAN.md §10 Phase 2 row): every
printed line maps 1:1 to an event from ``aida.core.events``, so this file
doubles as the spec any future frontend (Qt in Phase 5, ...) needs to match.
"""

from __future__ import annotations

import argparse
import asyncio

from aida.config.paths import skills_dir
from aida.config.settings import Settings, load_settings
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
from aida.providers.base import CompletionSettings, Message
from aida.providers.profiles import build_provider


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    skill_names = [s.strip() for s in args.skills.split(",") if s.strip()]

    settings = load_settings()
    try:
        session = ChatSession(settings, args.profile, skill_names=skill_names)
    except UnknownProfileError as exc:
        print(str(exc))
        return 1

    asyncio.run(_run_repl(session))
    return 0
