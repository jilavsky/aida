"""``aida mcp`` — manage MCP servers, groups, and per-tool permissions
without hand-editing ``mcp.json`` (Phase 7, planning/phase07_mcp_management.md).

Mirrors ``aida.cli.workspace_cmds``'s exact pattern: each subcommand handler
loads settings, mutates, saves, prints a plain result, and returns 0/1 —
no exceptions escape ``main()``. ``new``/``edit`` (here: ``add``/``edit``)
share the same "``defaults=True`` for add, ``defaults=False`` (unset flag =
unchanged) for edit" flag-building helper as workspaces.

One CLI-specific limitation, not present in the GUI dialog that will also
edit these fields: ``--arg``/``--env`` are repeatable flags, so there is no
way to explicitly set a server's ``args``/``env`` to *empty* via ``edit`` —
only to leave them unchanged (no flags given) or replace them (one or more
given). Not required by any Phase 7 acceptance criterion; the GUI's
multi-line text fields have no such ambiguity (zero lines = empty).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aida.config.settings import McpServerConfig, Settings, load_settings, save_mcp_config
from aida.mcp.config_io import merge_mcp_config
from aida.mcp.groups import add_group, delete_group, known_group_names, rename_group, resolve_group
from aida.mcp.manager import McpManager


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_env(pairs: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"--env expects KEY=VALUE, got {pair!r}")
        env[key] = value
    return env


def _get_server(settings: Settings, name: str) -> McpServerConfig | None:
    return settings.mcp.servers.get(name)


def _print_server(server: McpServerConfig) -> None:
    print(f"name:           {server.name}")
    print(f"command:        {server.command}")
    print(f"args:           {' '.join(server.args) or '(none)'}")
    print(f"env:            {', '.join(f'{k}=***' for k in server.env) or '(none)'}")
    print(f"groups:         {', '.join(server.groups) or '(none)'}")
    print(f"skills:         {', '.join(server.skills) or '(none)'}")
    print(f"disabled_tools: {', '.join(server.disabled_tools) or '(none)'}")
    print(f"confirm_tools:  {', '.join(server.confirm_tools) or '(none)'}")
    if server.extra:
        print(f"extra (unrecognized keys preserved as-is): {', '.join(server.extra)}")


# --- server subcommands ------------------------------------------------------


def cmd_server_list(_args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.mcp.servers:
        print("No MCP servers configured.")
        return 0
    for name, server in sorted(settings.mcp.servers.items()):
        print(
            f"{name:<20} command={server.command or '(none)':<30} "
            f"groups={','.join(server.groups) or '(none)':<20} "
            f"disabled_tools={len(server.disabled_tools)} confirm_tools={len(server.confirm_tools)}"
        )
    return 0


def cmd_server_show(args: argparse.Namespace) -> int:
    settings = load_settings()
    server = _get_server(settings, args.name)
    if server is None:
        print(f"Unknown MCP server {args.name!r}. Configured: {', '.join(sorted(settings.mcp.servers)) or '(none)'}")
        return 1
    _print_server(server)
    return 0


def cmd_server_add(args: argparse.Namespace) -> int:
    settings = load_settings()
    if _get_server(settings, args.name) is not None:
        print(f"MCP server {args.name!r} already exists — use `aida mcp server edit` to change it.")
        return 1
    try:
        env = _parse_env(args.env_list or [])
    except ValueError as exc:
        print(str(exc))
        return 1

    server = McpServerConfig(
        name=args.name,
        command=args.command or "",
        args=list(args.args_list or []),
        env=env,
        groups=_split_csv(args.groups or ""),
        skills=_split_csv(args.skills or ""),
    )
    settings.mcp.servers[args.name] = server
    save_mcp_config(settings.mcp)
    print(f"Added MCP server {args.name!r}.")
    return 0


def cmd_server_edit(args: argparse.Namespace) -> int:
    settings = load_settings()
    existing = _get_server(settings, args.name)
    if existing is None:
        print(f"Unknown MCP server {args.name!r} — use `aida mcp server add` to create it.")
        return 1
    try:
        env = _parse_env(args.env_list) if args.env_list is not None else existing.env
    except ValueError as exc:
        print(str(exc))
        return 1

    updated = McpServerConfig(
        name=args.name,
        command=args.command if args.command is not None else existing.command,
        args=list(args.args_list) if args.args_list is not None else existing.args,
        env=env,
        groups=_split_csv(args.groups) if args.groups is not None else existing.groups,
        skills=_split_csv(args.skills) if args.skills is not None else existing.skills,
        disabled_tools=existing.disabled_tools,
        confirm_tools=existing.confirm_tools,
        extra=existing.extra,
    )
    settings.mcp.servers[args.name] = updated
    save_mcp_config(settings.mcp)
    print(f"Updated MCP server {args.name!r}.")
    return 0


def cmd_server_remove(args: argparse.Namespace) -> int:
    settings = load_settings()
    if _get_server(settings, args.name) is None:
        print(f"Unknown MCP server {args.name!r}.")
        return 1
    if not args.yes:
        answer = input(f"Remove MCP server {args.name!r}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1
    del settings.mcp.servers[args.name]
    save_mcp_config(settings.mcp)
    print(f"Removed MCP server {args.name!r}.")
    return 0


def _toggle_tool(args: argparse.Namespace, *, field: str, add: bool, verb: str) -> int:
    settings = load_settings()
    server = _get_server(settings, args.name)
    if server is None:
        print(f"Unknown MCP server {args.name!r}.")
        return 1
    tools: list[str] = getattr(server, field)
    if add:
        if args.tool not in tools:
            tools.append(args.tool)
    else:
        if args.tool in tools:
            tools.remove(args.tool)
    save_mcp_config(settings.mcp)
    print(f"{verb} {args.tool!r} on {args.name!r}.")
    return 0


def cmd_server_disable_tool(args: argparse.Namespace) -> int:
    return _toggle_tool(args, field="disabled_tools", add=True, verb="Disabled tool")


def cmd_server_enable_tool(args: argparse.Namespace) -> int:
    return _toggle_tool(args, field="disabled_tools", add=False, verb="Enabled tool")


def cmd_server_confirm_tool(args: argparse.Namespace) -> int:
    return _toggle_tool(args, field="confirm_tools", add=True, verb="Marked confirm-before-run for tool")


def cmd_server_unconfirm_tool(args: argparse.Namespace) -> int:
    return _toggle_tool(args, field="confirm_tools", add=False, verb="Cleared confirm-before-run for tool")


# --- group subcommands -------------------------------------------------------


def cmd_group_list(_args: argparse.Namespace) -> int:
    settings = load_settings()
    names = known_group_names(settings.mcp)
    if not names:
        print("No groups referenced by any configured server.")
        return 0
    for name in names:
        members = sorted(s.name for s in resolve_group(settings.mcp, name))
        print(f"{name:<20} servers: {', '.join(members)}")
    return 0


def cmd_group_add(args: argparse.Namespace) -> int:
    settings = load_settings()
    server_names = _split_csv(args.servers)
    if not server_names:
        print("--servers must name at least one existing server — a group with zero members can't be created.")
        return 1
    unknown = [name for name in server_names if name not in settings.mcp.servers]
    if unknown:
        print(f"Unknown server name(s): {', '.join(unknown)}")
        return 1
    updated = add_group(settings.mcp, args.name, server_names)
    save_mcp_config(settings.mcp)
    if updated == 0:
        print(f"Group {args.name!r} already includes all of: {', '.join(server_names)} — nothing to do.")
        return 0
    print(f"Added group {args.name!r} to {updated} of {len(server_names)} requested server(s).")
    return 0


def cmd_group_rename(args: argparse.Namespace) -> int:
    settings = load_settings()
    updated = rename_group(settings.mcp, args.old, args.new)
    if updated == 0:
        print(f"Group {args.old!r} isn't referenced by any server — nothing to rename.")
        return 1
    save_mcp_config(settings.mcp)
    print(f"Renamed group {args.old!r} -> {args.new!r} on {updated} server(s).")
    return 0


def cmd_group_delete(args: argparse.Namespace) -> int:
    settings = load_settings()
    updated = delete_group(settings.mcp, args.name)
    if updated == 0:
        print(f"Group {args.name!r} isn't referenced by any server — nothing to delete.")
        return 1
    save_mcp_config(settings.mcp)
    print(f"Deleted group {args.name!r} from {updated} server(s).")
    return 0


# --- import / test ------------------------------------------------------------


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Could not read {path}: {exc}")
        return 1
    except ValueError as exc:
        print(f"{path} is not valid JSON: {exc}")
        return 1

    settings = load_settings()
    overwrite = set(_split_csv(args.overwrite or ""))
    result = merge_mcp_config(settings.mcp, raw, overwrite=overwrite)
    save_mcp_config(result.config)

    print(f"Imported from {path}:")
    print(f"  added:       {', '.join(result.added) or '(none)'}")
    print(f"  overwritten: {', '.join(result.overwritten) or '(none)'}")
    if result.skipped:
        print(f"  skipped (already configured — pass --overwrite {','.join(result.skipped)} to replace):")
        print(f"    {', '.join(result.skipped)}")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    settings = load_settings()
    server = _get_server(settings, args.name)
    if server is None:
        print(f"Unknown MCP server {args.name!r}.")
        return 1

    manager = McpManager([])
    result = asyncio.run(manager.test_connection(server))
    if result.ok:
        print(f"OK — {result.tool_count} tool(s), {result.elapsed_seconds:.2f}s")
        return 0
    print(f"FAILED — {result.error}")
    return 1


# --- argparse wiring -----------------------------------------------------------


def _add_server_field_args(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    parser.add_argument("--command", default="" if defaults else None, help="Executable to launch (stdio transport)")
    parser.add_argument(
        "--arg", dest="args_list", action="append", default=None,
        help="One command-line argument, in order (repeatable, e.g. --arg --stdio)",
    )
    parser.add_argument(
        "--env", dest="env_list", action="append", default=None,
        help="KEY=VALUE environment variable (repeatable)",
    )
    parser.add_argument("--groups", default="" if defaults else None, help="Comma-separated group names")
    parser.add_argument("--skills", default="" if defaults else None, help="Comma-separated skill names")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida mcp")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    server = sub.add_parser("server", help="Manage MCP server configs and per-tool permissions")
    server_sub = server.add_subparsers(dest="server_subcommand", required=True)

    server_sub.add_parser("list", help="List configured MCP servers")

    show = server_sub.add_parser("show", help="Show one server's full configuration")
    show.add_argument("name")

    add = server_sub.add_parser("add", help="Add a new MCP server")
    add.add_argument("name")
    _add_server_field_args(add, defaults=True)

    edit = server_sub.add_parser("edit", help="Update fields of an existing server (unset flags are left as-is)")
    edit.add_argument("name")
    _add_server_field_args(edit, defaults=False)

    remove = server_sub.add_parser("remove", help="Remove a server")
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    for sub_name, handler_desc in [
        ("disable-tool", "the model never sees this tool's schema"),
        ("enable-tool", "undo disable-tool"),
        ("confirm-tool", "require approval before every call to this tool, even in a relaxed workspace"),
        ("unconfirm-tool", "undo confirm-tool"),
    ]:
        p = server_sub.add_parser(sub_name, help=handler_desc)
        p.add_argument("name", help="Server name")
        p.add_argument("tool", help="Tool name (unnamespaced — as the server itself calls it)")

    group = sub.add_parser("group", help="Manage MCP server groups (derived from each server's own groups: list)")
    group_sub = group.add_subparsers(dest="group_subcommand", required=True)
    group_sub.add_parser("list", help="List every group and its member servers")
    group_add = group_sub.add_parser(
        "add", help="Create (or add members to) a group — a group with zero members can't exist"
    )
    group_add.add_argument("name")
    group_add.add_argument("--servers", required=True, help="Comma-separated names of existing servers to add")
    rename = group_sub.add_parser("rename", help="Rename a group across every server that references it")
    rename.add_argument("old")
    rename.add_argument("new")
    delete = group_sub.add_parser("delete", help="Remove a group from every server that references it")
    delete.add_argument("name")

    import_parser = sub.add_parser(
        "import", help="Import servers from a standard mcp.json-shaped file (merge without clobbering)"
    )
    import_parser.add_argument("path", help="Path to a mcp.json (or Claude Desktop config) file")
    import_parser.add_argument(
        "--overwrite", default="", help="Comma-separated server names to replace on conflict (default: skip conflicts)"
    )

    test = sub.add_parser("test", help="Test-connect to one configured server: initialize + list tools, report timing")
    test.add_argument("name")

    return parser


_SERVER_HANDLERS = {
    "list": cmd_server_list,
    "show": cmd_server_show,
    "add": cmd_server_add,
    "edit": cmd_server_edit,
    "remove": cmd_server_remove,
    "disable-tool": cmd_server_disable_tool,
    "enable-tool": cmd_server_enable_tool,
    "confirm-tool": cmd_server_confirm_tool,
    "unconfirm-tool": cmd_server_unconfirm_tool,
}

_GROUP_HANDLERS = {
    "list": cmd_group_list,
    "add": cmd_group_add,
    "rename": cmd_group_rename,
    "delete": cmd_group_delete,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])

    if args.subcommand == "server":
        return _SERVER_HANDLERS[args.server_subcommand](args)
    if args.subcommand == "group":
        return _GROUP_HANDLERS[args.group_subcommand](args)
    if args.subcommand == "import":
        return cmd_import(args)
    return cmd_test(args)
