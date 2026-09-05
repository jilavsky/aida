"""``aida kb`` — manage RAG knowledge bases and their SQLite indexes
(Phase 8, planning/phase08_rag.md) without hand-editing ``knowledge.yaml``.

Mirrors ``aida.cli.mcp_cmds``'s exact pattern: each subcommand handler loads
settings, mutates/saves (or, for build/update/query, just reads the config
and drives the index directly), prints a plain result, and returns 0/1 — no
exceptions escape ``main()``. ``add``/``edit`` share the same "defaults=True
for add, defaults=False (unset flag = unchanged) for edit" flag-building
helper as ``mcp_cmds``/``workspace_cmds``.
"""

from __future__ import annotations

import argparse
import asyncio

from aida.config.paths import knowledge_db_path
from aida.config.settings import (
    KnowledgeBaseConfig,
    Settings,
    load_settings,
    save_knowledge_config,
)
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.ingest import normalize_source_folder
from aida.knowledge.rag.ingest import rebuild as ingest_rebuild
from aida.knowledge.rag.ingest import update as ingest_update
from aida.knowledge.rag.retrieval import EmbeddingProfileMismatchError, retrieve
from aida.providers.profiles import UnknownProviderKindError, build_embeddings_provider


def _split_folders_csv(value: str) -> list[str]:
    # Normalizes each entry — a folder pasted as a `file://` URI (a real
    # bug report: Obsidian's "Copy as URI" action) used to silently ingest
    # zero files with no error anywhere. See ingest.normalize_source_folder.
    return [normalize_source_folder(item) for item in value.split(",") if item.strip()]


def _get_kb(settings: Settings, name: str) -> KnowledgeBaseConfig | None:
    return settings.knowledge.knowledge_bases.get(name)


def _print_kb(kb: KnowledgeBaseConfig) -> None:
    print(f"name:              {kb.name}")
    print(f"source_folders:    {', '.join(kb.source_folders) or '(none)'}")
    print(f"embedding_profile: {kb.embedding_profile or '(none)'}")
    print(f"chunk_size:        {kb.chunk_size}")
    print(f"chunk_overlap:     {kb.chunk_overlap}")


# --- list / show / add / edit / remove ---------------------------------------


def cmd_list(_args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.knowledge.knowledge_bases:
        print("No knowledge bases configured.")
        return 0
    for name, kb in sorted(settings.knowledge.knowledge_bases.items()):
        conn = kb_index.connect(knowledge_db_path(name))
        try:
            count = kb_index.chunk_count(conn)
        finally:
            conn.close()
        print(
            f"{name:<20} folders={len(kb.source_folders):<3} "
            f"embedding_profile={kb.embedding_profile or '(none)':<20} chunks={count}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    settings = load_settings()
    kb = _get_kb(settings, args.name)
    if kb is None:
        print(
            f"Unknown knowledge base {args.name!r}. Configured: {', '.join(sorted(settings.knowledge.knowledge_bases)) or '(none)'}"
        )
        return 1
    _print_kb(kb)
    return 0


def _validate_chunk_params(chunk_size: int, chunk_overlap: int) -> str | None:
    """Reject a chunk_size/chunk_overlap pair that can't terminate, naming
    the fix. Chunking advances by (chunk_size - chunk_overlap) characters
    per piece, so an overlap at or above the chunk size loops forever;
    ``aida.knowledge.rag.chunking.normalize_chunk_params`` clamps it as a
    backstop, but silently rewriting what someone typed on the command line
    is worse than telling them it's wrong. Returns an error message, or
    ``None`` if the pair is fine."""
    if chunk_size < 1:
        return f"--chunk-size must be at least 1 (got {chunk_size})."
    if chunk_overlap < 0:
        return f"--chunk-overlap must not be negative (got {chunk_overlap})."
    if chunk_overlap >= chunk_size:
        return (
            f"--chunk-overlap ({chunk_overlap}) must be smaller than --chunk-size "
            f"({chunk_size}) — chunking would never advance."
        )
    return None


def cmd_add(args: argparse.Namespace) -> int:
    settings = load_settings()
    if _get_kb(settings, args.name) is not None:
        print(f"Knowledge base {args.name!r} already exists — use `aida kb edit` to change it.")
        return 1

    error = _validate_chunk_params(args.chunk_size, args.chunk_overlap)
    if error:
        print(error)
        return 1

    kb = KnowledgeBaseConfig(
        name=args.name,
        source_folders=_split_folders_csv(args.source_folders or ""),
        embedding_profile=args.embedding_profile or None,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    settings.knowledge.knowledge_bases[args.name] = kb
    save_knowledge_config(settings.knowledge)
    print(f"Added knowledge base {args.name!r}.")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    settings = load_settings()
    existing = _get_kb(settings, args.name)
    if existing is None:
        print(f"Unknown knowledge base {args.name!r} — use `aida kb add` to create it.")
        return 1

    chunk_size = args.chunk_size if args.chunk_size is not None else existing.chunk_size
    chunk_overlap = args.chunk_overlap if args.chunk_overlap is not None else existing.chunk_overlap
    # Validated against the *resulting* pair, not just the flags given:
    # lowering only --chunk-size can just as easily land below the overlap
    # already stored in knowledge.yaml.
    error = _validate_chunk_params(chunk_size, chunk_overlap)
    if error:
        print(error)
        return 1

    updated = KnowledgeBaseConfig(
        name=args.name,
        source_folders=_split_folders_csv(args.source_folders)
        if args.source_folders is not None
        else existing.source_folders,
        embedding_profile=args.embedding_profile
        if args.embedding_profile is not None
        else existing.embedding_profile,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    settings.knowledge.knowledge_bases[args.name] = updated
    save_knowledge_config(settings.knowledge)
    print(f"Updated knowledge base {args.name!r}.")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    # Bug report: "when I delete source, is its data removed? Warning states
    # that 'its index file is left on disk' which is ambiguous and not
    # clear when and how will disk be cleaned up." --delete-index makes
    # cleanup an explicit, opt-in action instead of leaving the file behind
    # forever with no path to remove it.
    settings = load_settings()
    if _get_kb(settings, args.name) is None:
        print(f"Unknown knowledge base {args.name!r}.")
        return 1
    if not args.yes:
        suffix = (
            "and delete its index file from disk"
            if args.delete_index
            else "config only — pass --delete-index to also remove its index file"
        )
        answer = input(f"Remove knowledge base {args.name!r} ({suffix})? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1
    del settings.knowledge.knowledge_bases[args.name]
    save_knowledge_config(settings.knowledge)
    if args.delete_index:
        knowledge_db_path(args.name).unlink(missing_ok=True)
        print(
            f"Removed knowledge base {args.name!r} from configuration and deleted its index file."
        )
    else:
        print(f"Removed knowledge base {args.name!r} from configuration (index file left on disk).")
    return 0


# --- build / update / query ---------------------------------------------------


def _resolve_embeddings_provider(settings: Settings, kb: KnowledgeBaseConfig):
    """Shared setup for build/update/query: a knowledge base with no
    embedding_profile (or one that names a profile that isn't configured,
    or whose provider kind isn't implemented) can't be embedded against —
    every caller reports the same clear error rather than a bare
    ``KeyError``/``AttributeError``."""
    if not kb.embedding_profile:
        print(f"Knowledge base {kb.name!r} has no embedding_profile configured.")
        return None
    profile = settings.providers.embedding_profiles.get(kb.embedding_profile)
    if profile is None:
        print(
            f"Knowledge base {kb.name!r} references unknown embedding profile {kb.embedding_profile!r}. "
            f"Configured: {', '.join(sorted(settings.providers.embedding_profiles)) or '(none)'}"
        )
        return None
    try:
        return build_embeddings_provider(profile)
    except UnknownProviderKindError as exc:
        print(str(exc))
        return None


def _print_ingest_result(result) -> None:
    if result.missing_folders:
        print(
            f"  WARNING — source folder(s) not found, nothing indexed from them ({len(result.missing_folders)}):"
        )
        for folder in result.missing_folders:
            print(f"    {folder}")
    print(f"  added:   {len(result.added_files)}")
    print(f"  updated: {len(result.updated_files)}")
    print(f"  removed: {len(result.removed_files)}")
    if result.skipped_files:
        print(f"  skipped ({len(result.skipped_files)}):")
        for entry in result.skipped_files:
            print(f"    {entry}")
    if result.unverified_files:
        # Not "removed" and not "skipped": these are files already in the
        # index whose source folder could not be enumerated this pass, so
        # nothing could tell whether they still exist. Their chunks are kept
        # and stay queryable — reported so a stale answer later has a
        # visible explanation.
        print(
            f"  kept but not re-checked ({len(result.unverified_files)}) — "
            "their source folder was unavailable this pass:"
        )
        for entry in result.unverified_files:
            print(f"    {entry}")
    print(f"  total chunks written this pass: {result.chunk_count}")


def _run_ingest(args: argparse.Namespace, *, rebuild: bool) -> int:
    settings = load_settings()
    kb = _get_kb(settings, args.name)
    if kb is None:
        print(f"Unknown knowledge base {args.name!r}.")
        return 1

    embeddings_provider = _resolve_embeddings_provider(settings, kb)
    if embeddings_provider is None:
        return 1

    conn = kb_index.connect(knowledge_db_path(kb.name))
    try:
        ingest_fn = ingest_rebuild if rebuild else ingest_update
        result = asyncio.run(ingest_fn(conn, kb, embeddings_provider))
    finally:
        conn.close()
        asyncio.run(embeddings_provider.aclose())

    verb = "Rebuilt" if rebuild else "Updated"
    print(f"{verb} knowledge base {kb.name!r}:")
    _print_ingest_result(result)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    return _run_ingest(args, rebuild=True)


def cmd_update(args: argparse.Namespace) -> int:
    return _run_ingest(args, rebuild=False)


def cmd_query(args: argparse.Namespace) -> int:
    settings = load_settings()
    kb = _get_kb(settings, args.name)
    if kb is None:
        print(f"Unknown knowledge base {args.name!r}.")
        return 1

    embeddings_provider = _resolve_embeddings_provider(settings, kb)
    if embeddings_provider is None:
        return 1

    conn = kb_index.connect(knowledge_db_path(kb.name))
    try:
        results = asyncio.run(
            retrieve(
                conn,
                args.question,
                embeddings_provider=embeddings_provider,
                embedding_profile_name=kb.embedding_profile,
                top_k=args.top_k,
            )
        )
    except EmbeddingProfileMismatchError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()
        asyncio.run(embeddings_provider.aclose())

    if not results:
        print("No passages retrieved.")
        return 0
    for i, passage in enumerate(results, start=1):
        heading_suffix = f" — {passage.heading}" if passage.heading else ""
        print(f"[{i}] {passage.source_path}{heading_suffix} (score {passage.score:.3f})")
        print(passage.text)
        print()
    return 0


# --- argparse wiring -----------------------------------------------------------


def _add_kb_field_args(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    parser.add_argument(
        "--source-folders",
        default="" if defaults else None,
        help="Comma-separated folders and/or individual files to index "
        "(an Obsidian vault is just a folder of .md files)",
    )
    parser.add_argument(
        "--embedding-profile",
        default=None,
        help="Embedding profile name from providers.yaml (embedding_profiles:)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000 if defaults else None,
        help="Max characters per chunk",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=150 if defaults else None,
        help="Characters of trailing context carried into the next chunk",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida kb")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List configured knowledge bases and their indexed chunk counts")

    show = sub.add_parser("show", help="Show one knowledge base's full configuration")
    show.add_argument("name")

    add = sub.add_parser("add", help="Add a new knowledge base")
    add.add_argument("name")
    _add_kb_field_args(add, defaults=True)

    edit = sub.add_parser(
        "edit", help="Update fields of an existing knowledge base (unset flags are left as-is)"
    )
    edit.add_argument("name")
    _add_kb_field_args(edit, defaults=False)

    remove = sub.add_parser(
        "remove",
        help="Remove a knowledge base from configuration (add --delete-index to also remove its index file)",
    )
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    remove.add_argument(
        "--delete-index",
        action="store_true",
        help="Also delete the knowledge base's SQLite index file from disk",
    )

    build = sub.add_parser(
        "build", help="Full rebuild: re-chunk and re-embed every discovered file"
    )
    build.add_argument("name")

    update = sub.add_parser(
        "update", help="Incremental rebuild: only files changed since the last build/update"
    )
    update.add_argument("name")

    query = sub.add_parser(
        "query", help="Retrieval-only debugging tool: embed a question and print the top-k passages"
    )
    query.add_argument("name")
    query.add_argument("question")
    query.add_argument("--top-k", type=int, default=5)

    return parser


_HANDLERS = {
    "list": cmd_list,
    "show": cmd_show,
    "add": cmd_add,
    "edit": cmd_edit,
    "remove": cmd_remove,
    "build": cmd_build,
    "update": cmd_update,
    "query": cmd_query,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    return _HANDLERS[args.subcommand](args)
