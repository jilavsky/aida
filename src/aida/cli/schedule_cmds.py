"""``aida schedule`` — manage ``schedules.yaml`` and run the in-app
scheduler from a terminal (Phase 10,
planning/phase10_scheduling_design.md §6): ``watch`` is what lets a
headless control machine with no GUI host the exact same scheduler loop
the GUI runs in the background — same ``aida.core.scheduler_runtime.
scheduler_loop`` function either way.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aida.config.settings import (
    ScheduleEntry,
    load_schedules_config,
    load_settings,
    save_schedules_config,
)
from aida.core.scheduler_runtime import fire_schedule_now, scheduler_loop
from aida.core.scheduling import ScheduleConfigError, parse_schedule_timing

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


def _parse_vars(pairs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--var must be key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        result[key.strip()] = value
    return result


def cmd_list(_args: argparse.Namespace) -> int:
    config = load_schedules_config()
    if not config.schedules:
        print("No schedules configured.")
        return EXIT_OK
    for name, entry in sorted(config.schedules.items()):
        timing = f"at={entry.at}" if entry.at else f"every={entry.every}"
        state = "enabled" if entry.enabled else "disabled"
        print(f"{name:<20} workflow={entry.workflow:<20} {timing:<12} {state}")
    return EXIT_OK


def cmd_add(args: argparse.Namespace) -> int:
    try:
        parse_schedule_timing(at=args.at, every=args.every)
    except ScheduleConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        var_overrides = _parse_vars(args.var)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    config = load_schedules_config()
    if args.name in config.schedules:
        print(f"Schedule {args.name!r} already exists — use 'aida schedule remove' first to replace it.", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    config.schedules[args.name] = ScheduleEntry(
        name=args.name,
        workflow=args.workflow,
        at=args.at,
        every=args.every,
        vars=var_overrides,
        preapproved_tools=list(args.preapproved_tools),
        yes_in_allowed=args.yes_in_allowed,
        enabled=True,
    )
    save_schedules_config(config)
    print(f"Added schedule {args.name!r}.")
    return EXIT_OK


def _set_enabled(args: argparse.Namespace, *, enabled: bool) -> int:
    config = load_schedules_config()
    entry = config.schedules.get(args.name)
    if entry is None:
        print(f"No schedule named {args.name!r}.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    entry.enabled = enabled
    save_schedules_config(config)
    print(f"{'Enabled' if enabled else 'Disabled'} {args.name!r}.")
    return EXIT_OK


def cmd_enable(args: argparse.Namespace) -> int:
    return _set_enabled(args, enabled=True)


def cmd_disable(args: argparse.Namespace) -> int:
    return _set_enabled(args, enabled=False)


def cmd_remove(args: argparse.Namespace) -> int:
    config = load_schedules_config()
    if args.name not in config.schedules:
        print(f"No schedule named {args.name!r}.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    del config.schedules[args.name]
    save_schedules_config(config)
    print(f"Removed {args.name!r}.")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Fire one named schedule right now, regardless of whether it's
    "due" — the quick way to check a schedule actually works without
    waiting for its next slot (planning/phase10_scheduling_design.md §8)."""
    config = load_schedules_config()
    entry = config.schedules.get(args.name)
    if entry is None:
        print(f"No schedule named {args.name!r}.", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    settings = load_settings()
    settings.schedules = config

    def _on_finished(name: str, ok: bool, conversation_id: str | None, error: str | None) -> None:
        print(f"[{'ok' if ok else 'failed'}] {name}" + (f": {error}" if error else ""))
        if conversation_id:
            print(f"[conversation] {conversation_id}")

    asyncio.run(fire_schedule_now(args.name, entry, settings, on_run_finished=_on_finished))
    return EXIT_OK


def cmd_watch(args: argparse.Namespace) -> int:
    """Blocking: runs the exact same ``scheduler_loop`` the GUI drives on
    its background asyncio task, in this terminal, until Ctrl-C."""

    def _on_started(name: str) -> None:
        print(f"[running] {name}")

    def _on_finished(name: str, ok: bool, conversation_id: str | None, error: str | None) -> None:
        print(f"[{'ok' if ok else 'failed'}] {name}" + (f": {error}" if error else ""))

    print(f"aida schedule watch — polling every {args.poll_seconds}s. Ctrl-C to stop.")
    try:
        asyncio.run(
            scheduler_loop(
                poll_interval_seconds=args.poll_seconds, on_run_started=_on_started, on_run_finished=_on_finished
            )
        )
    except KeyboardInterrupt:
        print("\n[stopped]")
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida schedule")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List every configured schedule")

    add = sub.add_parser("add", help="Add a new schedule")
    add.add_argument("name")
    add.add_argument("--workflow", required=True, help="Name of a stored workflow (see 'aida workflow list')")
    timing = add.add_mutually_exclusive_group(required=True)
    timing.add_argument("--at", help='Daily local time, e.g. "07:00"')
    timing.add_argument("--every", help='A duration, e.g. "4h", "30m", "1d"')
    add.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    add.add_argument(
        "--yes-in-allowed",
        action="store_true",
        help="Auto-approve writes/deletes inside the workflow's own workspace allowed folders",
    )
    add.add_argument(
        "--preapprove-tool",
        action="append",
        default=[],
        metavar="SERVER__TOOL",
        dest="preapproved_tools",
        help="A namespaced MCP tool name to approve for this schedule's runs (repeatable)",
    )

    for name, help_text in (("enable", "Enable a schedule"), ("disable", "Disable a schedule"), ("remove", "Remove a schedule")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("name")

    run = sub.add_parser("run", help="Fire one schedule now, regardless of whether it's due")
    run.add_argument("name")

    watch = sub.add_parser("watch", help="Run the scheduler loop in this terminal until Ctrl-C")
    watch.add_argument("--poll-seconds", type=float, default=30.0, dest="poll_seconds")

    return parser


_HANDLERS = {
    "list": cmd_list,
    "add": cmd_add,
    "enable": cmd_enable,
    "disable": cmd_disable,
    "remove": cmd_remove,
    "run": cmd_run,
    "watch": cmd_watch,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    return _HANDLERS[args.subcommand](args)
