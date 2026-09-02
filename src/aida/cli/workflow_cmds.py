"""``aida workflow`` — list/show/validate/run stored named workflows
(Phase 10, planning/phase10_scheduling_design.md §4).

Deliberately no ``add``/``edit`` subcommand: a workflow is a hand-edited or
GUI-"save conversation as workflow"-produced YAML document (one file per
workflow under ``~/.aida/workflows/``), not a row in a shared config list —
same "no visual builder" stance the design doc states explicitly. Mirrors
``aida.cli.kb_cmds``'s ``_HANDLERS``-dict dispatch pattern.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from aida.config.settings import WorkflowConfig, load_settings, load_workflow
from aida.core.headless import build_headless_confirm_callback
from aida.core.workflows import WorkflowConfigError, render_step_prompt, run_workflow

#: Same three exit codes as ``aida run`` — see that module's docstring for
#: why there is no separate "confirmation declined" code.
EXIT_OK = 0
EXIT_STEP_FAILED = 1
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
    from aida.config.settings import list_workflow_names

    names = list_workflow_names()
    if not names:
        print("No workflows stored.")
        return EXIT_OK
    for name in names:
        try:
            workflow = load_workflow(name)
        except FileNotFoundError:
            continue  # deleted between the list and load calls
        print(f"{name:<24} workspace={workflow.workspace or '(none)':<16} steps={len(workflow.steps)}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    try:
        workflow = load_workflow(args.name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    print(f"name:              {workflow.name}")
    print(f"description:       {workflow.description or '(none)'}")
    print(f"workspace:         {workflow.workspace or '(none)'}")
    print(f"profile:           {workflow.profile or '(workspace default)'}")
    print(f"mcp_group:         {workflow.mcp_group or '(workspace default)'}")
    print(f"vars:              {workflow.vars or '(none)'}")
    print(f"preapproved_tools: {', '.join(workflow.preapproved_tools) or '(none)'}")
    print(f"steps ({len(workflow.steps)}):")
    for index, step in enumerate(workflow.steps):
        print(f"  {index}. {step.prompt}")
        if step.expect_files:
            print(f"     expect_files: {', '.join(step.expect_files)}")
    return EXIT_OK


def _resolved_vars(workflow: WorkflowConfig, args: argparse.Namespace) -> dict[str, str]:
    return {**workflow.vars, **_parse_vars(args.var)}


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        workflow = load_workflow(args.name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    problems: list[str] = []
    if not workflow.workspace:
        problems.append("no workspace configured")
    else:
        settings = load_settings()
        if workflow.workspace not in settings.workspaces.workspaces:
            problems.append(f"unknown workspace {workflow.workspace!r}")
    if not workflow.steps:
        problems.append("no steps")

    try:
        resolved_vars = _resolved_vars(workflow, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    for index, step in enumerate(workflow.steps):
        try:
            render_step_prompt(step, resolved_vars)
        except WorkflowConfigError as exc:
            problems.append(f"step {index}: {exc}")

    if problems:
        for problem in problems:
            print(f"[invalid] {problem}")
        return EXIT_CONFIG_ERROR
    print(f"{workflow.name}: OK ({len(workflow.steps)} step(s))")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    try:
        workflow = load_workflow(args.name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        var_overrides = _parse_vars(args.var)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    settings = load_settings()
    confirm_callback = build_headless_confirm_callback(
        yes_in_allowed=args.yes_in_allowed,
        preapproved_tools=set(args.preapproved_tools) | set(workflow.preapproved_tools),
    )

    def _on_event(index: int, event) -> None:
        if args.json:
            return
        from aida.cli.chat import print_event

        print(f"\n[step {index}] ", end="")
        print_event(event)

    try:
        result = asyncio.run(
            run_workflow(
                settings,
                workflow,
                var_overrides=var_overrides,
                confirm_callback=confirm_callback,
                origin="workflow",
                on_event=None if args.json else _on_event,
            )
        )
    except WorkflowConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "workflow": result.workflow_name,
                    "conversation_id": result.conversation_id,
                    "error": result.error,
                    "manifest_path": result.manifest_path,
                    "steps": [s.to_dict() for s in result.steps],
                },
                indent=2,
            )
        )
    else:
        print(f"\n[{'ok' if result.ok else 'failed'}] {result.workflow_name}")
        if result.error:
            print(f"[error] {result.error}", file=sys.stderr)
        if result.manifest_path:
            print(f"[manifest] {result.manifest_path}")

    return EXIT_OK if result.ok else EXIT_STEP_FAILED


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida workflow")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List every stored workflow")

    show = sub.add_parser("show", help="Print one workflow's steps and settings")
    show.add_argument("name")

    validate = sub.add_parser("validate", help="Check a workflow without running it")
    validate.add_argument("name")
    validate.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")

    run = sub.add_parser("run", help="Run a stored workflow now")
    run.add_argument("name")
    run.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    run.add_argument(
        "--yes-in-allowed",
        action="store_true",
        help="Auto-approve writes/deletes inside the workspace's own allowed folders",
    )
    run.add_argument(
        "--preapprove-tool",
        action="append",
        default=[],
        metavar="SERVER__TOOL",
        dest="preapproved_tools",
        help="A namespaced MCP tool name to approve, in addition to the workflow's own preapproved_tools",
    )
    run.add_argument("--json", action="store_true", help="Emit a machine-readable JSON result summary")

    return parser


_HANDLERS = {
    "list": cmd_list,
    "show": cmd_show,
    "validate": cmd_validate,
    "run": cmd_run,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    return _HANDLERS[args.subcommand](args)
