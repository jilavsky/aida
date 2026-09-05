"""``aida conversations`` — list/resume/delete/export persisted conversations
(Phase 4, PLAN.md §10 Phase 4 row: "CLI: `aida conversations
list/resume/delete/export`").

``resume`` reuses ``aida.cli.chat.start_session``/``_run_repl`` so a resumed
session behaves identically to a fresh ``aida chat`` one (same REPL, same
incremental persistence, same workspace/explicit-flag precedence) — the only
difference is history + workspace/profile default from the stored
conversation instead of being required on the command line.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.config.paths import ensure_records_dir
from aida.config.settings import Settings, load_settings
from aida.config.users import resolve_active_user
from aida.persistence.cleanup import (
    delete_conversation,
    delete_orphan_attachment_dirs,
    find_orphan_attachment_dirs,
)
from aida.persistence.recorder import ConversationNotFoundError, ConversationRecorder
from aida.persistence.store import ConversationStore, ConversationSummary


class UnknownConversationIdError(Exception):
    """Raised when an id/prefix matches no stored conversation."""


class AmbiguousConversationIdError(Exception):
    """Raised when an id prefix matches more than one stored conversation."""


def resolve_conversation_id(store: ConversationStore, id_or_prefix: str) -> str:
    """Conversations are addressed by a 32-character uuid4 hex id, but
    ``aida conversations list`` only prints the first 8 (same idea as a git
    short hash) — accept any unambiguous prefix here rather than forcing the
    full id to be typed/pasted. A full id is also accepted directly."""
    summary = store.get_conversation(id_or_prefix)
    if summary is not None:
        return summary.id
    matches = [c for c in store.list_conversations() if c.id.startswith(id_or_prefix)]
    if not matches:
        raise UnknownConversationIdError(f"no conversation matches id/prefix {id_or_prefix!r}")
    if len(matches) > 1:
        ids = ", ".join(m.id[:8] for m in matches)
        raise AmbiguousConversationIdError(
            f"{id_or_prefix!r} matches multiple conversations ({ids}) — use more characters"
        )
    return matches[0].id


def _records_dir(settings: Settings) -> Path:
    return ensure_records_dir(Path(settings.app.records_dir) if settings.app.records_dir else None)


def _format_row(summary: ConversationSummary) -> str:
    title = summary.title or "(untitled)"
    workspace = summary.workspace_name or "-"
    # The user column only appears once something in the DB actually uses
    # it, so a single-user install's listing is byte-for-byte unchanged.
    user = f"  [{summary.user}]" if summary.user else ""
    return (
        f"{summary.id[:8]}  {summary.updated_at}  {workspace:<16}  "
        f"{summary.message_count:>4} msgs  {title}{user}"
    )


def cmd_list(args: argparse.Namespace) -> int:
    # --all-users wins over --user, and both win over the configured
    # default: an explicit "show me everything" must never be narrowed by
    # an active_user someone left in config.yaml.
    active_user = (
        ""
        if getattr(args, "all_users", False)
        else resolve_active_user(getattr(args, "user", "") or None, app_config=load_settings().app)
    )
    store = ConversationStore()
    try:
        summaries = store.list_conversations(active_user or None)
        if not summaries:
            if active_user:
                print(f"No conversations for user {active_user!r} yet (--all-users to see every one).")
                return 0
            print("No conversations yet.")
            return 0
        for summary in summaries:
            print(_format_row(summary))
        return 0
    finally:
        store.close()


def cmd_delete(args: argparse.Namespace) -> int:
    store = ConversationStore()
    try:
        try:
            conv_id = resolve_conversation_id(store, args.id)
        except (UnknownConversationIdError, AmbiguousConversationIdError) as exc:
            print(str(exc))
            return 1

        if not args.yes:
            answer = input(f"Delete conversation {conv_id[:8]}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 1

        settings = load_settings()
        result = delete_conversation(store, conv_id, records_dir=_records_dir(settings))
        print(
            f"Deleted conversation {conv_id[:8]}: "
            f"{result.deleted_message_rows} message row(s), "
            f"{result.deleted_artifact_rows} artifact row(s), "
            f"{len(result.deleted_artifact_files)} artifact file(s), "
            f"record file {'removed' if result.deleted_record_file else 'not present'}, "
            f"sidecar dir {'removed' if result.deleted_sidecar_dir else 'not present'}"
        )
        if result.skipped_external_files:
            # Say so explicitly rather than leaving the user to wonder
            # whether their own files went with the conversation.
            print(
                f"Kept {len(result.skipped_external_files)} file(s) in your own folders "
                "(source documents and generated reports are never deleted with a conversation):"
            )
            for path in result.skipped_external_files:
                print(f"  {path}")
        return 0
    finally:
        store.close()


def cmd_rename(args: argparse.Namespace) -> int:
    """Bug report: "Can we have the chat list in the history column have
    some kind of names? ... these date/times are not very convenient to
    use." ``set_title`` already exists (``ConversationRecorder`` calls it
    once, for auto-titling from the first message) — this is the missing
    "rename it again" entry point."""
    store = ConversationStore()
    try:
        try:
            conv_id = resolve_conversation_id(store, args.id)
        except (UnknownConversationIdError, AmbiguousConversationIdError) as exc:
            print(str(exc))
            return 1
        store.set_title(conv_id, args.title, timestamp=datetime.now(UTC).isoformat())
        print(f"Renamed conversation {conv_id[:8]} to {args.title!r}.")
        return 0
    finally:
        store.close()


def cmd_export(args: argparse.Namespace) -> int:
    store = ConversationStore()
    try:
        try:
            conv_id = resolve_conversation_id(store, args.id)
        except (UnknownConversationIdError, AmbiguousConversationIdError) as exc:
            print(str(exc))
            return 1

        settings = load_settings()
        artifact_store = ArtifactStore()
        recorder = ConversationRecorder(
            store, artifact_store, _records_dir(settings), conversation_id=conv_id, resume=True
        )
        path = recorder.export_transcript()
        print(f"Exported transcript to {path}")
        return 0
    except ConversationNotFoundError as exc:
        print(str(exc))
        return 1
    finally:
        store.close()


async def _resume_async(
    settings: Settings,
    conv_id: str,
    *,
    profile_name: str | None,
    workspace_name: str | None,
    skill_names: list[str],
    mcp_group: str,
    mcp_names: list[str],
) -> int:
    # Imported lazily (not at module scope) to avoid a circular import:
    # aida.cli.chat doesn't import this module, but keeping the dependency
    # one-directional and load-on-demand is cheap insurance either way.
    from aida.cli.chat import (
        UnknownMcpServerError,
        UnknownProfileError,
        UnknownWorkspaceError,
        _run_repl,
        start_session,
    )

    try:
        session, mcp_manager = await start_session(
            settings,
            profile_name=profile_name,
            workspace_name=workspace_name,
            skill_names=skill_names,
            mcp_group=mcp_group,
            mcp_names=mcp_names,
            resume_conversation_id=conv_id,
        )
    except (
        UnknownProfileError,
        UnknownWorkspaceError,
        UnknownMcpServerError,
        ConversationNotFoundError,
    ) as exc:
        print(str(exc))
        return 1

    try:
        await _run_repl(session)
    finally:
        if mcp_manager is not None:
            await mcp_manager.aclose()
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    settings = load_settings()

    store = ConversationStore()
    try:
        try:
            conv_id = resolve_conversation_id(store, args.id)
        except (UnknownConversationIdError, AmbiguousConversationIdError) as exc:
            print(str(exc))
            return 1
    finally:
        store.close()  # start_session opens its own connection

    skill_names = [s.strip() for s in args.skills.split(",") if s.strip()]
    mcp_names = [s.strip() for s in args.mcp.split(",") if s.strip()]

    return asyncio.run(
        _resume_async(
            settings,
            conv_id,
            profile_name=args.profile or None,
            workspace_name=args.workspace or None,
            skill_names=skill_names,
            mcp_group=args.mcp_group,
            mcp_names=mcp_names,
        )
    )


def cmd_gc(args: argparse.Namespace) -> int:
    """Remove attachment folders whose conversation no longer exists.

    Separate from `doctor`, which only reports: a diagnostic command should
    never delete anything. This is the one that does, and it asks first
    unless told otherwise, because the folders hold the user's own
    documents even if the chat around them is gone.
    """
    settings = load_settings()
    records_dir = ensure_records_dir(settings.app.records_dir)
    store = ConversationStore()
    try:
        orphans = find_orphan_attachment_dirs(store, records_dir=records_dir)
        if not orphans:
            print("No leftover attachment folders.")
            return 0
        print(f"{len(orphans)} attachment folder(s) with no conversation:")
        for orphan in orphans:
            files = sorted(p.name for p in orphan.iterdir() if p.is_file())
            print(f"  {orphan}  ({', '.join(files) if files else 'empty'})")
        if not args.yes:
            answer = input("Delete these permanently? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 0
        removed = delete_orphan_attachment_dirs(store, records_dir=records_dir)
        print(f"Removed {len(removed)} folder(s).")
        return 0
    finally:
        store.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida conversations")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    listing = sub.add_parser("list", help="List all conversations, most recently updated first")
    listing.add_argument(
        "--user",
        default="",
        help="Show only this user's conversations, plus unlabelled ones (default: $AIDA_USER, "
        "else config.yaml's active_user; pass --all-users to ignore it)",
    )
    listing.add_argument(
        "--all-users",
        action="store_true",
        help="List every conversation regardless of its user label",
    )

    resume = sub.add_parser("resume", help="Resume a conversation in an interactive chat session")
    resume.add_argument("id", help="Conversation id, or an unambiguous prefix (e.g. the first 8 chars)")
    resume.add_argument("--profile", default="", help="Override the profile stored with this conversation")
    resume.add_argument("--workspace", default="", help="Override the workspace stored with this conversation")
    resume.add_argument("--skills", default="", help="Comma-separated skill names to add")
    resume.add_argument(
        "--mcp-group", default="", help="Named MCP server group to enable (overrides the stored workspace's group)"
    )
    resume.add_argument("--mcp", default="", help="Comma-separated MCP server names to enable directly")

    gc = sub.add_parser(
        "gc", help="Remove attachment folders left behind by conversations that no longer exist"
    )
    gc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    delete = sub.add_parser("delete", help="Delete a conversation: DB rows, artifact files, and its Markdown record")
    delete.add_argument("id", help="Conversation id, or an unambiguous prefix")
    delete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    rename = sub.add_parser("rename", help="Rename a conversation's title")
    rename.add_argument("id", help="Conversation id, or an unambiguous prefix")
    rename.add_argument("title", help="New title")

    export = sub.add_parser("export", help="Re-export a conversation's Markdown transcript on demand")
    export.add_argument("id", help="Conversation id, or an unambiguous prefix")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    if args.subcommand == "list":
        return cmd_list(args)
    if args.subcommand == "resume":
        return cmd_resume(args)
    if args.subcommand == "delete":
        return cmd_delete(args)
    if args.subcommand == "rename":
        return cmd_rename(args)
    if args.subcommand == "gc":
        return cmd_gc(args)
    return cmd_export(args)
