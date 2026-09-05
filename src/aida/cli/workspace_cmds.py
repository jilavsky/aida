"""``aida workspace`` — list/show/new/edit named workspaces (Phase 4,
PLAN.md §10 Phase 4 row: "Workspace CRUD from CLI (`aida workspace
list/show/new/edit`)"; GUI equivalent arrives in Phase 5).

``new`` and ``edit`` both end up calling
``aida.workspace.workspaces.save_workspace`` (which itself is "create or
replace" — see its docstring), but they differ in intent: ``new`` refuses to
clobber an existing workspace by accident, ``edit`` requires one to already
exist and only overwrites the fields the caller actually passed, leaving
everything else as it was.
"""

from __future__ import annotations

import argparse
import dataclasses

from aida.config.settings import Settings, WorkspaceConfig, load_settings
from aida.workspace.safety import relaxed_mode_warning_if_newly_enabled
from aida.workspace.workspaces import (
    get_workspace,
    list_workspace_names,
    save_workspace,
    validate_workspace,
)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _print_workspace(ws: WorkspaceConfig) -> None:
    print(f"name:               {ws.name}")
    print(f"profile:            {ws.profile or '(none)'}")
    print(f"mcp_group:          {ws.mcp_group}")
    print(f"skills:             {', '.join(ws.skills) or '(none)'}")
    print(f"knowledge_bases:    {', '.join(ws.knowledge_bases) or '(none)'}")
    print(f"source_folders:     {', '.join(ws.source_folders) or '(none)'}")
    print(f"target_folder:      {ws.target_folder or '(none)'}")
    print(f"sidecar_folder_name: {ws.sidecar_folder_name}")
    print(f"safety:             {ws.safety}")
    print(f"system_prompt:      {ws.system_prompt or '(none)'}")
    print(f"command_allowlist:  {', '.join(ws.command_allowlist) or '(none)'}")
    print(f"python_interpreter: {ws.python_interpreter or '(default: sys.executable)'}")
    print(f"scripting_enabled:  {ws.scripting_enabled}")
    print(f"templates_dir:      {ws.templates_dir or '(none)'}")
    print(
        f"saved_scripts_dir:  {ws.saved_scripts_dir or '(default: <target_folder>/saved_scripts)'}"
    )


def _print_validation(settings: Settings, ws: WorkspaceConfig) -> None:
    result = validate_workspace(settings, ws)
    if not result.ok:
        print(f"[invalid] {result.detail}")
        return
    if result.warnings:
        for warning in result.warnings:
            print(f"[warning] {warning}")
    else:
        print("[ok] no warnings")


def cmd_list(_args: argparse.Namespace) -> int:
    settings = load_settings()
    names = list_workspace_names(settings)
    if not names:
        print("No workspaces configured.")
        return 0
    for name in names:
        ws = get_workspace(settings, name)
        print(f"{name:<20} profile={ws.profile or '(none)':<16} mcp_group={ws.mcp_group}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    settings = load_settings()
    ws = get_workspace(settings, args.name)
    if ws is None:
        print(
            f"Unknown workspace {args.name!r}. Configured: {', '.join(list_workspace_names(settings)) or '(none)'}"
        )
        return 1
    _print_workspace(ws)
    _print_validation(settings, ws)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    settings = load_settings()
    if get_workspace(settings, args.name) is not None:
        print(f"Workspace {args.name!r} already exists — use `aida workspace edit` to change it.")
        return 1

    ws = WorkspaceConfig(
        name=args.name,
        profile=args.profile or None,
        source_folders=_split_csv(args.source_folders),
        target_folder=args.target_folder or None,
        sidecar_folder_name=args.sidecar_folder_name,
        mcp_group=args.mcp_group,
        skills=_split_csv(args.skills),
        knowledge_bases=_split_csv(args.knowledge_bases),
        system_prompt=args.system_prompt or None,
        safety=args.safety,
        command_allowlist=_split_csv(args.command_allowlist),
        python_interpreter=args.python_interpreter or None,
        scripting_enabled=args.scripting_enabled,
        templates_dir=args.templates_dir or None,
        saved_scripts_dir=args.saved_scripts_dir or None,
    )
    save_workspace(settings, ws)
    print(f"Created workspace {args.name!r}.")
    warning = relaxed_mode_warning_if_newly_enabled(None, ws.safety)
    if warning:
        print(f"[warning] {warning}")
    _print_validation(settings, ws)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    settings = load_settings()
    existing = get_workspace(settings, args.name)
    if existing is None:
        print(f"Unknown workspace {args.name!r} — use `aida workspace new` to create it.")
        return 1

    # `dataclasses.replace(existing, ...)` rather than rebuilding a
    # WorkspaceConfig from the flags: a from-scratch rebuild silently reset
    # every field `edit` has no flag for back to its dataclass default, so
    # `aida workspace edit W --profile other` also wiped W's `quick_tasks`
    # (up to ten saved prompt templates) and reset `script_timeout_seconds`
    # to 30 — an unrelated one-flag edit destroying user data with no
    # warning. Carrying `existing` forward and overriding only the flags
    # actually passed also means a field added to WorkspaceConfig later is
    # preserved here automatically instead of becoming the same bug again.
    updates: dict[str, object] = {}
    if args.profile is not None:
        updates["profile"] = args.profile
    if args.source_folders is not None:
        updates["source_folders"] = _split_csv(args.source_folders)
    if args.target_folder is not None:
        updates["target_folder"] = args.target_folder
    if args.sidecar_folder_name is not None:
        updates["sidecar_folder_name"] = args.sidecar_folder_name
    if args.mcp_group is not None:
        updates["mcp_group"] = args.mcp_group
    if args.skills is not None:
        updates["skills"] = _split_csv(args.skills)
    if args.knowledge_bases is not None:
        updates["knowledge_bases"] = _split_csv(args.knowledge_bases)
    if args.system_prompt is not None:
        updates["system_prompt"] = args.system_prompt
    if args.safety is not None:
        updates["safety"] = args.safety
    if args.command_allowlist is not None:
        updates["command_allowlist"] = _split_csv(args.command_allowlist)
    if args.python_interpreter is not None:
        updates["python_interpreter"] = args.python_interpreter
    if args.scripting_enabled is not None:
        updates["scripting_enabled"] = args.scripting_enabled
    if args.templates_dir is not None:
        updates["templates_dir"] = args.templates_dir
    if args.saved_scripts_dir is not None:
        updates["saved_scripts_dir"] = args.saved_scripts_dir

    ws = dataclasses.replace(existing, name=args.name, **updates)
    save_workspace(settings, ws)
    print(f"Updated workspace {args.name!r}.")
    warning = relaxed_mode_warning_if_newly_enabled(existing.safety, ws.safety)
    if warning:
        print(f"[warning] {warning}")
    _print_validation(settings, ws)
    return 0


def _add_field_args(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    """Shared flags between ``new`` (fields default to "empty"/None so an
    unset flag means "leave the field at its normal default") and ``edit``
    (fields default to ``None`` so an unset flag means "leave the field
    unchanged" — the two are distinguished by ``defaults``)."""
    profile_kwargs = {"default": "" if defaults else None}
    parser.add_argument(
        "--profile", help="Provider profile name from providers.yaml", **profile_kwargs
    )
    parser.add_argument(
        "--source-folders",
        default="" if defaults else None,
        help="Comma-separated folders this workspace reads from",
    )
    parser.add_argument(
        "--target-folder", default=None, help="Folder this workspace writes results into"
    )
    parser.add_argument(
        "--sidecar-folder-name",
        default="figures" if defaults else None,
        help="Subfolder name (under the records dir) for this workspace's image sidecar files",
    )
    parser.add_argument(
        "--mcp-group",
        default="none" if defaults else None,
        help="Named MCP server group from mcp.json",
    )
    parser.add_argument(
        "--skills", default="" if defaults else None, help="Comma-separated skill names"
    )
    parser.add_argument(
        "--knowledge-bases",
        default="" if defaults else None,
        help="Comma-separated knowledge base names from knowledge.yaml (Phase 8 RAG)",
    )
    parser.add_argument(
        "--system-prompt", default=None, help="Extra system prompt text for this workspace"
    )
    parser.add_argument(
        "--safety",
        default="confirm" if defaults else None,
        choices=["confirm", "relaxed"],
        help="'confirm' (ask before every write/delete) or 'relaxed' (only asks for actions outside the "
        "workspace's allowed folders)",
    )
    parser.add_argument(
        "--command-allowlist",
        default="" if defaults else None,
        help="Comma-separated safe shell commands (Phase 9), e.g. 'git status,git log *'",
    )
    parser.add_argument(
        "--python-interpreter",
        default=None,
        help="Path to a conda/venv env's python executable for run_python_script (Phase 9); "
        "defaults to whatever AIDA itself runs under",
    )
    parser.add_argument(
        "--scripting-enabled",
        action=argparse.BooleanOptionalAction,
        default=True if defaults else None,
        help="Enable run_python_script/run_command for this workspace (Phase 9, default: enabled)",
    )
    parser.add_argument(
        "--templates-dir",
        default=None,
        help="Folder of .py code templates for this workspace (Phase 9)",
    )
    parser.add_argument(
        "--saved-scripts-dir",
        default=None,
        help="Where the code editor saves scripts (Phase 9); defaults to <target_folder>/saved_scripts",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida workspace")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List all configured workspaces")

    show = sub.add_parser(
        "show", help="Show one workspace's full configuration and validation status"
    )
    show.add_argument("name")

    new = sub.add_parser("new", help="Create a new workspace")
    new.add_argument("name")
    _add_field_args(new, defaults=True)

    edit = sub.add_parser(
        "edit", help="Update fields of an existing workspace (unset flags are left as-is)"
    )
    edit.add_argument("name")
    _add_field_args(edit, defaults=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    if args.subcommand == "list":
        return cmd_list(args)
    if args.subcommand == "show":
        return cmd_show(args)
    if args.subcommand == "new":
        return cmd_new(args)
    return cmd_edit(args)
