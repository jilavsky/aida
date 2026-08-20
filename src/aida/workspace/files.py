"""Native workspace file tools (PLAN.md Phase 6): ``list_directory``,
``find_files``, ``search_text``, ``read_file``, ``write_file``,
``create_directory``, ``copy_file``, ``move_file``, ``delete_file``,
``get_file_metadata`` — exposed to the LLM the same way MCP tools are
(``aida.core.tools.NativeTool``, merged into the ``tools`` dict passed to
``AgentLoop`` exactly like ``aida.mcp.manager``'s tools are).

Every tool is safety-checked through a ``SafetyGuard`` captured at
construction time (the same "closure captures state at tool-build time"
pattern ``aida.mcp.manager`` uses for its own tool closures) — no tool here
touches disk without going through ``guard.authorize_read`` /
``authorize_write`` / ``delete`` first. Listing/search results come back as
typed ``TableArtifact``s (PLAN.md: "Tool results are typed... not prose"),
capped at a bounded number of rows so a huge directory can't context-bomb
the model.

Bulk filesystem scans (``list_directory`` recursive, ``find_files``,
``search_text``) run under a timeout via ``asyncio.wait_for`` +
``asyncio.to_thread`` — PLAN.md: "Graceful handling of slow/missing network
mounts (timeout + clear error)". Narrower point calls (a single
``stat()``/``exists()`` inside ``SafetyGuard``'s own path resolution) aren't
separately timeout-wrapped; wrapping every individual blocking call would
be its own project and isn't attempted here.

Every tool function is wrapped with ``@_tool`` (``aida.core.tools.
wrap_tool_errors``), which catches the *expected*, named failure modes — a
declined/denied confirmation, a missing/unreadable file, a network-mount
timeout, an unsupported document format — and turns them into a normal
``ToolResult(is_error=True, ...)`` instead of a raised exception.
``aida.core.agent.AgentLoop`` already has its own blanket ``try/except
Exception`` one level up for anything unexpected, so this isn't required
for correctness in production — it's here so each tool's contract is
"returns a ToolResult, full stop" and is directly unit-testable (as
``tests/test_workspace_files.py`` does) without needing a full
``AgentLoop`` harness just to see an expected denial/error turned into a
result.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aida.artifacts.base import FileArtifact, JsonArtifact, TableArtifact
from aida.artifacts.policy import describe_for_model
from aida.core.tools import NativeTool, ToolResult, wrap_tool_errors
from aida.documents.readers import UnsupportedDocumentFormatError, read_document
from aida.providers.base import ToolSchema
from aida.workspace.safety import ConfirmationDenied, SafetyGuard

DEFAULT_MAX_LIST_ENTRIES = 500
DEFAULT_MAX_SEARCH_MATCHES = 100
DEFAULT_SEARCH_FILE_SIZE_CAP = 2_000_000  # skip scanning files bigger than this for search_text

_tool = wrap_tool_errors(ConfirmationDenied, OSError, TimeoutError, UnsupportedDocumentFormatError, ValueError)
FS_TIMEOUT_SECONDS = 15.0

_TRASH_DIRNAME = "_trash"


async def _run_blocking(func, *args, timeout: float = FS_TIMEOUT_SECONDS, **kwargs):
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(
            f"Timed out after {timeout}s waiting on the filesystem — the path may be on a slow "
            "or unresponsive network mount."
        ) from exc


def _entry_row(path: Path, *, base: Path) -> list[Any]:
    try:
        is_dir = path.is_dir()
        size = None if is_dir else path.stat().st_size
    except OSError:
        is_dir, size = False, None
    return [str(path.relative_to(base)), "dir" if is_dir else "file", size]


def _list_directory_sync(root: Path, *, recursive: bool, max_entries: int) -> list[list[Any]]:
    rows: list[list[Any]] = []
    truncated = False

    if not recursive:
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.name == _TRASH_DIRNAME:
                continue
            if len(rows) >= max_entries:
                truncated = True
                break
            rows.append(_entry_row(entry, base=root))
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != _TRASH_DIRNAME)
            current = Path(dirpath)
            for name in sorted(filenames):
                if len(rows) >= max_entries:
                    truncated = True
                    break
                rows.append(_entry_row(current / name, base=root))
            if truncated:
                break

    if truncated:
        rows.append([f"... [more entries truncated at {max_entries}]", "", None])
    return rows


def _find_files_sync(root: Path, pattern: str, *, recursive: bool, max_entries: int) -> list[list[Any]]:
    matches = root.rglob(pattern) if recursive else root.glob(pattern)
    rows: list[list[Any]] = []
    truncated = False
    for path in sorted(matches):
        if _TRASH_DIRNAME in path.relative_to(root).parts:
            continue
        if len(rows) >= max_entries:
            truncated = True
            break
        rows.append(_entry_row(path, base=root))
    if truncated:
        rows.append([f"... [more matches truncated at {max_entries}]", "", None])
    return rows


def _search_text_sync(
    root: Path, query: str, *, recursive: bool, case_sensitive: bool, max_matches: int
) -> list[list[Any]]:
    needle = query if case_sensitive else query.lower()
    rows: list[list[Any]] = []
    truncated = False
    candidates = root.rglob("*") if recursive else root.glob("*")

    for path in sorted(candidates):
        if len(rows) >= max_matches:
            truncated = True
            break
        if _TRASH_DIRNAME in path.relative_to(root).parts:
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > DEFAULT_SEARCH_FILE_SIZE_CAP:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable or binary — skip, don't fail the whole search
        for line_no, line in enumerate(text.splitlines(), start=1):
            haystack = line if case_sensitive else line.lower()
            if needle in haystack:
                rows.append([str(path.relative_to(root)), line_no, line.strip()[:300]])
                if len(rows) >= max_matches:
                    truncated = True
                    break

    # Appended once, here, rather than at the top of the outer loop: filling
    # the quota on the *last* candidate file left the loop with nothing
    # after it to notice, so the model was handed a silently-capped result
    # set that looked complete. Matches _list_directory_sync/_find_files_sync.
    if truncated:
        rows.append([f"... [more matches truncated at {max_matches}]", "", ""])
    return rows


def default_file_tools(
    guard: SafetyGuard,
    *,
    max_list_entries: int = DEFAULT_MAX_LIST_ENTRIES,
    max_search_matches: int = DEFAULT_MAX_SEARCH_MATCHES,
) -> dict[str, NativeTool]:
    """Builds the ten native file tools, each closing over ``guard`` (and,
    for the listing tools, the size caps) — the exact merge-into-``tools``-
    dict pattern ``aida.mcp.manager``'s tools already use."""

    @_tool
    async def list_directory(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        recursive = bool(arguments.get("recursive", False))
        candidate = await guard.authorize_read(path)
        if not candidate.is_dir():
            return ToolResult(content=f"Not a directory: {candidate}", is_error=True)
        rows = await _run_blocking(_list_directory_sync, candidate, recursive=recursive, max_entries=max_list_entries)
        table = TableArtifact(columns=["path", "type", "size_bytes"], rows=rows)
        return ToolResult(content=describe_for_model(table), artifacts=[table])

    @_tool
    async def find_files(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        pattern = arguments["pattern"]
        recursive = bool(arguments.get("recursive", True))
        candidate = await guard.authorize_read(path)
        if not candidate.is_dir():
            return ToolResult(content=f"Not a directory: {candidate}", is_error=True)
        rows = await _run_blocking(
            _find_files_sync, candidate, pattern, recursive=recursive, max_entries=max_list_entries
        )
        table = TableArtifact(columns=["path", "type", "size_bytes"], rows=rows)
        return ToolResult(content=describe_for_model(table), artifacts=[table])

    @_tool
    async def search_text(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        query = arguments["query"]
        recursive = bool(arguments.get("recursive", True))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        candidate = await guard.authorize_read(path)
        if not candidate.is_dir():
            return ToolResult(content=f"Not a directory: {candidate}", is_error=True)
        rows = await _run_blocking(
            _search_text_sync,
            candidate,
            query,
            recursive=recursive,
            case_sensitive=case_sensitive,
            max_matches=max_search_matches,
        )
        table = TableArtifact(columns=["file", "line", "text"], rows=rows)
        return ToolResult(content=describe_for_model(table), artifacts=[table])

    @_tool
    async def read_file(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        candidate = await guard.authorize_read(path)
        if not candidate.is_file():
            return ToolResult(content=f"Not a file: {candidate}", is_error=True)
        artifacts = await _run_blocking(read_document, candidate)
        content = "\n\n".join(describe_for_model(a) for a in artifacts)
        return ToolResult(content=content, artifacts=artifacts)

    @_tool
    async def write_file(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        content = arguments["content"]
        overwrite = bool(arguments.get("overwrite", False))
        candidate = await guard.authorize_write(path)
        if candidate.exists() and not overwrite:
            return ToolResult(
                content=f"{candidate} already exists — pass overwrite=true to replace it.", is_error=True
            )

        def _write() -> None:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(content, encoding="utf-8")

        await _run_blocking(_write)
        mime_type = mimetypes.guess_type(str(candidate))[0]
        artifact = FileArtifact(path=str(candidate), mime_type=mime_type)
        return ToolResult(content=f"Wrote {len(content)} character(s) to {candidate}", artifacts=[artifact])

    @_tool
    async def create_directory(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        candidate = await guard.authorize_write(path)
        await _run_blocking(lambda: candidate.mkdir(parents=True, exist_ok=True))
        return ToolResult(content=f"Created directory {candidate}")

    @_tool
    async def copy_file(arguments: dict[str, Any]) -> ToolResult:
        source = await guard.authorize_read(arguments["source"])
        destination = await guard.authorize_write(arguments["destination"])
        if not source.is_file():
            return ToolResult(content=f"Not a file: {source}", is_error=True)

        def _copy() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        await _run_blocking(_copy)
        mime_type = mimetypes.guess_type(str(destination))[0]
        artifact = FileArtifact(path=str(destination), mime_type=mime_type)
        return ToolResult(content=f"Copied {source} -> {destination}", artifacts=[artifact])

    @_tool
    async def move_file(arguments: dict[str, Any]) -> ToolResult:
        # Moving mutates the source location too (the file is removed from
        # there), so the source goes through authorize_delete, not
        # authorize_read — same confirmation gating a delete would get.
        source = await guard.authorize_delete(arguments["source"])
        destination = await guard.authorize_write(arguments["destination"])
        if not source.exists():
            return ToolResult(content=f"Not found: {source}", is_error=True)

        def _move() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))

        await _run_blocking(_move)
        mime_type = mimetypes.guess_type(str(destination))[0]
        artifact = FileArtifact(path=str(destination), mime_type=mime_type)
        return ToolResult(content=f"Moved {source} -> {destination}", artifacts=[artifact])

    @_tool
    async def delete_file(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        destination = await guard.delete(path)
        if guard.trash_enabled:
            return ToolResult(content=f"Moved to trash: {destination}")
        return ToolResult(content=f"Deleted: {destination}")

    @_tool
    async def get_file_metadata(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        candidate = await guard.authorize_read(path)
        if not candidate.exists():
            return ToolResult(content=f"Not found: {candidate}", is_error=True)

        def _stat() -> dict[str, Any]:
            info = candidate.stat()
            return {
                "path": str(candidate),
                "is_dir": candidate.is_dir(),
                "size_bytes": None if candidate.is_dir() else info.st_size,
                "modified_iso": datetime.fromtimestamp(info.st_mtime, tz=UTC).isoformat(),
                "mime_type": mimetypes.guess_type(str(candidate))[0],
            }

        data = await _run_blocking(_stat)
        artifact = JsonArtifact(data=data)
        return ToolResult(content=describe_for_model(artifact), artifacts=[artifact])

    tools = [
        NativeTool(
            schema=ToolSchema(
                name="list_directory",
                description="List the files and subdirectories under a directory.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory to list."},
                        "recursive": {"type": "boolean", "description": "List subdirectories too. Default false."},
                    },
                    "required": ["path"],
                },
            ),
            func=list_directory,
        ),
        NativeTool(
            schema=ToolSchema(
                name="find_files",
                description="Find files under a directory matching a glob pattern (e.g. '*.pdf').",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory to search under."},
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.csv'."},
                        "recursive": {
                            "type": "boolean",
                            "description": "Search subdirectories too. Default true.",
                        },
                    },
                    "required": ["path", "pattern"],
                },
            ),
            func=find_files,
        ),
        NativeTool(
            schema=ToolSchema(
                name="search_text",
                description="Search for a text query inside files under a directory; returns matching file/line/text rows.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory to search under."},
                        "query": {"type": "string", "description": "Text to search for."},
                        "recursive": {"type": "boolean", "description": "Search subdirectories too. Default true."},
                        "case_sensitive": {"type": "boolean", "description": "Default false."},
                    },
                    "required": ["path", "query"],
                },
            ),
            func=search_text,
        ),
        NativeTool(
            schema=ToolSchema(
                name="read_file",
                description=(
                    "Read a file's contents, with format-appropriate extraction for PDF/DOCX/XLSX/PPTX/CSV/"
                    "JSON/images and plain text otherwise."
                ),
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "File to read."}},
                    "required": ["path"],
                },
            ),
            func=read_file,
        ),
        NativeTool(
            schema=ToolSchema(
                name="write_file",
                description="Write text content to a file. Fails if the file already exists unless overwrite=true.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File to write."},
                        "content": {"type": "string", "description": "Text content to write."},
                        "overwrite": {"type": "boolean", "description": "Replace an existing file. Default false."},
                    },
                    "required": ["path", "content"],
                },
            ),
            func=write_file,
        ),
        NativeTool(
            schema=ToolSchema(
                name="create_directory",
                description="Create a directory (and any missing parent directories).",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Directory to create."}},
                    "required": ["path"],
                },
            ),
            func=create_directory,
        ),
        NativeTool(
            schema=ToolSchema(
                name="copy_file",
                description="Copy a file to a new location.",
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
            ),
            func=copy_file,
        ),
        NativeTool(
            schema=ToolSchema(
                name="move_file",
                description="Move (rename) a file to a new location.",
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
            ),
            func=move_file,
        ),
        NativeTool(
            schema=ToolSchema(
                name="delete_file",
                description="Delete a file (moved to a recoverable '_trash' folder, not permanently erased, unless trash is disabled).",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            func=delete_file,
        ),
        NativeTool(
            schema=ToolSchema(
                name="get_file_metadata",
                description="Get a file or directory's size, type, and last-modified time.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            func=get_file_metadata,
        ),
    ]
    return {tool.schema.name: tool for tool in tools}


__all__ = [
    "DEFAULT_MAX_LIST_ENTRIES",
    "DEFAULT_MAX_SEARCH_MATCHES",
    "DEFAULT_SEARCH_FILE_SIZE_CAP",
    "default_file_tools",
]
