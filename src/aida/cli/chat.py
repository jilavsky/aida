"""``aida chat`` — interactive streaming chat with a provider (Phase 2),
now workspace-aware and persisted (Phase 4).

The reference frontend implementation (PLAN.md §10 Phase 2 row): every
printed line maps 1:1 to an event from ``aida.core.events``, so this file
doubles as the spec any future frontend (Qt in Phase 5, ...) needs to match.

B8 (planning/improvement_plan_2026-08.md): the session engine itself
(``ChatSession``, ``start_session``, and their supporting pieces) now lives
in ``aida.core.session`` — this module re-exports every one of those names
below so nothing that used to import them from here (``aida.cli.conversations``,
every test in this suite, external callers) needs to change. What's left in
this file is genuinely CLI-frontend code: rendering events to a real
terminal (``print_event``), the ``input()``-driven REPL loop, Ctrl-C
handling, ``argparse`` wiring, and ``main``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
from collections.abc import Iterator

from aida.config.settings import Settings, load_settings
from aida.core.cost import estimate_cost_usd
from aida.core.events import (
    AgentError,
    AgentEvent,
    ContextTrimmed,
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
from aida.core.session import (
    ChatSession,
    UnknownMcpServerError,
    UnknownProfileError,
    UnknownWorkspaceError,
    cli_confirm,
    resolve_mcp_servers,
    resolve_profile,
    start_session,
)

__all__ = [
    "ChatSession",
    "UnknownMcpServerError",
    "UnknownProfileError",
    "UnknownWorkspaceError",
    "cli_confirm",
    "resolve_mcp_servers",
    "resolve_profile",
    "start_session",
    "print_event",
    "main",
]


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
        # TextFinished already closed the reply visually; the one case
        # worth a separate line is "length" — the reply was cut off
        # mid-sentence by max_tokens and, without this, there is no
        # indication of why (bug report: truncated replies are silent).
        if event.stop_reason == "length":
            print("[notice] reply hit the max-tokens limit and was cut off — raise max_tokens in the profile settings")
    elif isinstance(event, UsageInfo):
        # Bug report: "Can we get cost estimate... token use may be better
        # ... it is a black box." Printed unconditionally now (was
        # previously swallowed, "for a future --verbose flag") — a provider
        # that doesn't report usage just never emits this event at all, so
        # there's nothing spurious to gate behind a flag.
        # B3: cache_read_input_tokens is "the savings are visible" —
        # appended only when a provider actually reports it (Anthropic with
        # caching active; always 0 otherwise), so a non-caching turn's line
        # is unchanged from before.
        cache_note = f", {event.cache_read_input_tokens} cached" if event.cache_read_input_tokens else ""
        if event.output_tokens and event.duration_seconds:
            rate = event.output_tokens / event.duration_seconds
            print(
                f"[usage] {event.input_tokens} in / {event.output_tokens} out tokens{cache_note}, "
                f"{event.duration_seconds:.1f}s ({rate:.1f} tok/s)"
            )
        elif event.input_tokens or event.output_tokens:
            print(f"[usage] {event.input_tokens} in / {event.output_tokens} out tokens{cache_note}")
    elif isinstance(event, ContextTrimmed):
        # B7: trimming used to be a log line only — nothing told the user
        # at the terminal that earlier turns had just been dropped to stay
        # under budget, so a suddenly-forgetful-seeming model looked like a
        # bug rather than an expected trade-off. PLAN.md §1.3: distinguishes
        # a compaction summary from a plain drop — the two are very
        # different outcomes for "will the model still remember this".
        turn_word = "turn" if event.dropped_turns == 1 else "turns"
        if event.summarized:
            print(
                f"[context] summarized {event.dropped_turns} old {turn_word} into ~{event.summary_tokens} "
                f"tokens (~{event.estimated_tokens} tokens now)"
            )
        else:
            print(f"[context] trimmed {event.dropped_turns} old {turn_word} to fit budget (~{event.estimated_tokens} tokens now)")
    elif isinstance(event, AgentError):
        detail = f" ({event.detail})" if event.detail else ""
        print(f"\n[error:{event.layer}] {event.message}{detail}")


async def _run_repl(session: ChatSession) -> None:
    print(
        f"AIDA chat — profile: {session.profile_name} "
        f"({session.profile.kind}, model={session.profile.model})"
    )
    if session.recorder is not None:
        print(f"[conversations] recording as {session.recorder.conversation_id}")
    print(
        "Type /exit to quit, /profile NAME to switch profiles mid-session, "
        "/max-iterations N to raise the per-turn tool-call cap, "
        "/compact to summarize older turns now.\n"
    )

    try:
        await _repl_loop(session)
    finally:
        # Always close the (possibly swapped) provider's HTTP client before
        # asyncio.run() tears down the event loop — see ChatSession.aclose.
        await session.aclose()


@contextlib.contextmanager
def _cancel_turn_on_sigint(session: ChatSession) -> Iterator[None]:
    """Make Ctrl-C during a streaming reply cancel *the turn* instead of the
    process, for as long as this context is active.

    Python's default SIGINT handler raises ``KeyboardInterrupt`` wherever
    the interpreter happens to be executing — with the main thread parked
    inside ``loop.run_forever()``, that is ``asyncio.run`` itself, not the
    coroutine below it. So the ``except KeyboardInterrupt`` wrapped around
    ``async for ... session.send(...)`` never actually fired: Ctrl-C
    mid-reply killed the process rather than cancelling the turn, even
    though ``AgentLoop.cancel``'s whole design assumes a caller reaches it.
    An event-loop signal handler runs as a normal loop callback instead, so
    it can just set the cancel flag and let the turn unwind through its own
    cleanup (which now includes answering any tool calls it had already
    announced — see ``aida.core.agent``).

    Installed only around the streaming section and removed again at the
    prompt, deliberately: ``input()`` blocks the loop, so a loop-level
    handler would sit queued instead of interrupting it, and Ctrl-C at the
    ``>`` prompt (which works today, raising inside ``input()``) has to keep
    working. Platforms without ``add_signal_handler`` (Windows' proactor
    loop) fall back to the previous behavior rather than failing to start."""
    loop = asyncio.get_running_loop()

    def _request_cancel() -> None:
        print("\n[cancelling — finishing the step in flight...]", flush=True)
        session.cancel()

    try:
        loop.add_signal_handler(signal.SIGINT, _request_cancel)
    except (NotImplementedError, RuntimeError, ValueError, AttributeError):
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            loop.remove_signal_handler(signal.SIGINT)


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
        if line == "/compact":
            # PLAN.md §1.3 / context_management.md §3.4: manual compaction
            # at a natural task boundary, rather than only ever triggering
            # automatically mid-turn once the budget is already exceeded.
            event = await session.compact_now()
            if event is None:
                print("[compact] nothing to compact yet — not enough history.")
            else:
                print_event(event)
            continue

        try:
            with _cancel_turn_on_sigint(session):
                async for event in session.send(line):
                    print_event(event)
        except KeyboardInterrupt:
            # Retained as a backstop for the platforms/paths where the
            # loop-level handler above isn't available — see its docstring.
            session.cancel()
            print("\n[cancelled]")
            continue

        if session.total_input_tokens or session.total_output_tokens:
            # B2: priced at the active profile's own rate when it has one
            # (usd_per_m_input/usd_per_m_output) — falls back to the fixed
            # default rate for a profile that doesn't set them, same as
            # before B2.
            cost = estimate_cost_usd(
                session.total_input_tokens,
                session.total_output_tokens,
                input_usd_per_million=session.profile.usd_per_m_input,
                output_usd_per_million=session.profile.usd_per_m_output,
            )
            print(
                f"[session total] {session.total_input_tokens} in / {session.total_output_tokens} out "
                f"tokens, ~${cost:.4f} est."
            )

        # planning/context_management.md §3.5: fullness (how close to the
        # wall the *next* request is), not the ever-growing cumulative
        # total printed just above — computed the same way ChatSession's
        # own trim/compaction budget is, so the two can never disagree.
        # Omitted entirely when trimming/compaction is disabled (budget 0).
        used, budget = session.context_fullness()
        if budget:
            pct = round(100 * used / budget)
            print(f"[context] {used:,} / {budget:,} tokens ({pct}%) — /compact to summarize older turns")


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
