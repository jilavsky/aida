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
    print(f"source_folders:     {', '.join(ws.source_folders) or '(none)'}")
    print(f"target_folder:      {ws.target_folder or '(none)'}")
    print(f"sidecar_folder_name: {ws.sidecar_folder_name}")
    print(f"safety:             {ws.safety}")
    print(f"system_prompt:      {ws.system_prompt or '(none)'}")


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
        print(f"Unknown workspace {args.name!r}. Configured: {', '.join(list_workspace_names(settings)) or '(none)'}")
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
        system_prompt=args.system_prompt or None,
        safety=args.safety,
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

    ws = WorkspaceConfig(
        name=args.name,
        profile=args.profile if args.profile is not None else existing.profile,
        source_folders=_split_csv(args.source_folders) if args.source_folders is not None else existing.source_folders,
        target_folder=args.target_folder if args.target_folder is not None else existing.target_folder,
        sidecar_folder_name=args.sidecar_folder_name
        if args.sidecar_folder_name is not None
        else existing.sidecar_folder_name,
        mcp_group=args.mcp_group if args.mcp_group is not None else existing.mcp_group,
        skills=_split_csv(args.skills) if args.skills is not None else existing.skills,
        system_prompt=args.system_prompt if args.system_prompt is not None else existing.system_prompt,
        safety=args.safety if args.safety is not None else existing.safety,
    )
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
    parser.add_argument("--profile", help="Provider profile name from providers.yaml", **profile_kwargs)
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
        "--mcp-group", default="none" if defaults else None, help="Named MCP server group from mcp.json"
    )
    parser.add_argument("--skills", default="" if defaults else None, help="Comma-separated skill names")
    parser.add_argument("--system-prompt", default=None, help="Extra system prompt text for this workspace")
    parser.add_argument(
        "--safety",
        default="confirm" if defaults else None,
        choices=["confirm", "relaxed"],
        help="'confirm' (ask before every write/delete) or 'relaxed' (only asks for actions outside the "
        "workspace's allowed folders)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida workspace")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List all configured workspaces")

    show = sub.add_parser("show", help="Show one workspace's full configuration and validation status")
    show.add_argument("name")

    new = sub.add_parser("new", help="Create a new workspace")
    new.add_argument("name")
    _add_field_args(new, defaults=True)

    edit = sub.add_parser("edit", help="Update fields of an existing workspace (unset flags are left as-is)")
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
