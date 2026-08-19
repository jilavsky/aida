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
from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.config.paths import ensure_records_dir
from aida.config.settings import Settings, load_settings
from aida.persistence.cleanup import delete_conversation
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
    return f"{summary.id[:8]}  {summary.updated_at}  {workspace:<16}  {summary.message_count:>4} msgs  {title}"


def cmd_list(_args: argparse.Namespace) -> int:
    store = ConversationStore()
    try:
        summaries = store.list_conversations()
        if not summaries:
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida conversations")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List all conversations, most recently updated first")

    resume = sub.add_parser("resume", help="Resume a conversation in an interactive chat session")
    resume.add_argument("id", help="Conversation id, or an unambiguous prefix (e.g. the first 8 chars)")
    resume.add_argument("--profile", default="", help="Override the profile stored with this conversation")
    resume.add_argument("--workspace", default="", help="Override the workspace stored with this conversation")
    resume.add_argument("--skills", default="", help="Comma-separated skill names to add")
    resume.add_argument(
        "--mcp-group", default="", help="Named MCP server group to enable (overrides the stored workspace's group)"
    )
    resume.add_argument("--mcp", default="", help="Comma-separated MCP server names to enable directly")

    delete = sub.add_parser("delete", help="Delete a conversation: DB rows, artifact files, and its Markdown record")
    delete.add_argument("id", help="Conversation id, or an unambiguous prefix")
    delete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

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
    return cmd_export(args)
