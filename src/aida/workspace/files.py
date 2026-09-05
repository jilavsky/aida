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

Blocking work runs on a worker thread under a deadline — PLAN.md: "Graceful
handling of slow/missing network mounts (timeout + clear error)" — but
*reads and writes are treated differently*, because ``asyncio.wait_for``
cancels an await and never the thread underneath it:

- ``_run_scan`` (``list_directory`` recursive, ``find_files``,
  ``search_text``) hands the worker a ``threading.Event`` it checks between
  directory entries, so giving up on the wait actually ends the walk. Those
  walks are also lazy and bounded — see ``_walk_files``: they stop
  traversing once the result cap is reached, rather than enumerating and
  sorting the whole tree first.
- ``_run_blocking`` covers the remaining read-only calls, which are safe to
  simply abandon.
- ``_run_mutation`` (``write_file``, ``create_directory``, ``copy_file``,
  ``move_file``) does *not* claim the work stopped, because it did not: it
  reports ``FilesystemOperationPending`` and refuses further operations on
  the same path until the worker settles.

Narrower point calls (a single ``stat()``/``exists()`` inside
``SafetyGuard``'s own path resolution) aren't separately timeout-wrapped;
wrapping every individual blocking call would be its own project and isn't
attempted here.

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
import threading
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from aida.artifacts.base import FileArtifact, JsonArtifact, TableArtifact
from aida.artifacts.policy import describe_for_model
from aida.core.tools import NativeTool, ToolResult, wrap_tool_errors
from aida.documents.readers import (
    INTERACTIVE_MAX_CHARS,
    INTERACTIVE_MAX_PDF_PAGES,
    UnsupportedDocumentFormatError,
    read_document,
)
from aida.providers.base import ToolSchema
from aida.workspace.safety import ConfirmationDenied, SafetyGuard

DEFAULT_MAX_LIST_ENTRIES = 500
DEFAULT_MAX_SEARCH_MATCHES = 100
DEFAULT_SEARCH_FILE_SIZE_CAP = 2_000_000  # skip scanning files bigger than this for search_text

FS_TIMEOUT_SECONDS = 15.0

#: How long a *mutating* operation is given before the caller stops waiting.
#: Longer than the read-only budget on purpose: a large copy across a slow
#: share is legitimately slow, and — unlike a scan — giving up on the wait
#: does not stop the work, so a short deadline here buys nothing but a
#: misleading message. See ``_run_mutation``.
FS_MUTATION_TIMEOUT_SECONDS = 120.0

_TRASH_DIRNAME = "_trash"


class ScanCancelled(Exception):
    """Raised inside a scan worker thread once its caller has stopped
    waiting. Never reaches the caller — by the time it is raised, the
    future the worker was running under has already been abandoned — it
    exists purely to unwind the traversal promptly instead of letting a
    thread keep walking a directory tree nobody will read the result of."""


class FilesystemOperationPending(Exception):
    """A mutating filesystem operation outlived its deadline and, crucially,
    is *still running*.

    This is deliberately not a ``TimeoutError``. A timeout normally means
    "it didn't happen"; here the underlying ``shutil.copy2`` /
    ``Path.write_text`` / ``shutil.move`` is executing on a worker thread
    that nothing can interrupt — ``asyncio.wait_for`` cancels the *await*,
    not the thread. Reporting that as a plain timeout told the model a
    mutation had failed while it was in fact still writing to the target,
    which invites a retry that races the operation still in progress. The
    message says so explicitly, and ``_run_mutation`` refuses a second
    operation on the same path until the first settles."""


_tool = wrap_tool_errors(
    ConfirmationDenied,
    FilesystemOperationPending,
    OSError,
    TimeoutError,
    UnsupportedDocumentFormatError,
    ValueError,
)


#: Resolved target paths with a mutation currently running on a worker
#: thread that outlived its deadline. Module-level rather than per-guard
#: because the thread outlives the tool call, the session's guard, and any
#: retry: what matters is the path on disk, not who asked.
_PENDING_MUTATIONS: dict[Path, str] = {}


async def _run_blocking(func, *args, timeout: float = FS_TIMEOUT_SECONDS, **kwargs):
    """Run a *read-only* blocking filesystem call with a deadline.

    Safe to abandon: everything routed through here only reads, so a worker
    that outlives the wait leaves nothing half-written. Scans that support
    it also get cooperative cancellation — see ``_run_scan``. Mutations use
    ``_run_mutation`` instead, which does not pretend the work stopped.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(
            f"Timed out after {timeout}s waiting on the filesystem — the path may be on a slow "
            "or unresponsive network mount."
        ) from exc


async def _run_scan(func, *args, timeout: float = FS_TIMEOUT_SECONDS, **kwargs):
    """``_run_blocking`` for a tree walk, with cooperative cancellation.

    ``asyncio.wait_for`` abandons the await but cannot stop the worker
    thread, so a recursive scan of an unresponsive mount used to keep
    walking — burning a thread-pool slot and the mount's patience — for as
    long as the filesystem took, long after its result had been discarded.
    The scan worker is handed a ``threading.Event`` it checks between
    directory entries; setting it on timeout is what actually ends the walk.
    """
    cancel = threading.Event()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args, cancel=cancel, **kwargs), timeout=timeout
        )
    except TimeoutError as exc:
        cancel.set()
        raise TimeoutError(
            f"Timed out after {timeout}s scanning the filesystem — the path may be on a slow "
            "or unresponsive network mount. Try a narrower path, or a non-recursive scan."
        ) from exc


async def _run_mutation(
    func,
    *args,
    target: Path,
    description: str,
    timeout: float = FS_MUTATION_TIMEOUT_SECONDS,
    **kwargs,
):
    """Run a blocking filesystem *mutation* with an honest deadline.

    The distinction from ``_run_blocking`` is the whole point. Cancelling an
    ``asyncio.to_thread`` await does not stop the thread: on a hung network
    share, a copy or move reported "timed out" to the model while it was
    still writing the destination. The model would then reasonably retry —
    a second write racing the first, against the same target, with the
    original still able to land afterwards and overwrite the retry's result.

    So: a generous deadline (a large copy over a slow share is not an
    error), and if it passes, a ``FilesystemOperationPending`` that says the
    operation is still running rather than that it failed. The path is
    recorded as pending until the worker actually finishes, and a second
    mutation on the same target is refused for as long as it is — which is
    the part that actually prevents the race, rather than trusting the model
    to read the message and behave.
    """
    if target in _PENDING_MUTATIONS:
        raise FilesystemOperationPending(
            f"An earlier {_PENDING_MUTATIONS[target]} on {target} has not finished yet and cannot be "
            "cancelled. Do not retry it — wait, then check the file, before touching this path again."
        )

    task = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except TimeoutError as exc:
        # shield() above means the task itself keeps running — which is
        # simply the truth about a thread that cannot be interrupted. Mark
        # the path pending and clear it whenever the worker does finish, so
        # the block lifts on its own without anything having to poll.
        _PENDING_MUTATIONS[target] = description
        task.add_done_callback(lambda _t, p=target: _PENDING_MUTATIONS.pop(p, None))
        raise FilesystemOperationPending(
            f"{description.capitalize()} on {target} has taken longer than {timeout}s and is STILL "
            "RUNNING — it could not be cancelled, and may still complete. The file may be "
            "partially or fully written. Do not retry; wait and check the file instead."
        ) from exc


def _entry_row(path: Path, *, base: Path) -> list[Any]:
    try:
        is_dir = path.is_dir()
        size = None if is_dir else path.stat().st_size
    except OSError:
        is_dir, size = False, None
    return [str(path.relative_to(base)), "dir" if is_dir else "file", size]


def _walk_files(root: Path, *, recursive: bool, cancel: threading.Event | None = None):
    """Yield files under ``root`` lazily, in a deterministic order, checking
    ``cancel`` as it goes.

    Laziness is the point. ``find_files`` and ``search_text`` used to build
    their candidate list with ``sorted(root.rglob(...))``, and ``sorted``
    consumes its whole input before returning even one element — so the
    entry cap, applied afterwards, bounded the *response size* while the
    traversal itself remained unbounded in both time and memory. A search
    for the first match in a directory of a million files walked all million
    of them first; on a slow mount it hit the timeout having produced
    nothing at all, when a handful of directories would have answered the
    question.

    Ordering is kept deterministic (sorted within each directory, directories
    visited in sorted order) so the same call twice returns the same page,
    which a global ``sorted`` gave for free and a bare ``rglob`` — whose
    order is whatever ``os.scandir`` reports — would not.
    """
    if not recursive:
        with os.scandir(root) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if cancel is not None and cancel.is_set():
                    raise ScanCancelled
                if entry.name == _TRASH_DIRNAME:
                    continue
                yield Path(entry.path)
        return

    for dirpath, dirnames, filenames in os.walk(root):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled
        dirnames[:] = sorted(d for d in dirnames if d != _TRASH_DIRNAME)
        current = Path(dirpath)
        for name in sorted(filenames):
            if cancel is not None and cancel.is_set():
                raise ScanCancelled
            yield current / name


def _list_directory_sync(
    root: Path, *, recursive: bool, max_entries: int, cancel: threading.Event | None = None
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    truncated = False

    if not recursive:
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if cancel is not None and cancel.is_set():
                raise ScanCancelled
            if entry.name == _TRASH_DIRNAME:
                continue
            if len(rows) >= max_entries:
                truncated = True
                break
            rows.append(_entry_row(entry, base=root))
    else:
        for path in _walk_files(root, recursive=True, cancel=cancel):
            if len(rows) >= max_entries:
                truncated = True
                break
            rows.append(_entry_row(path, base=root))

    if truncated:
        rows.append([f"... [more entries truncated at {max_entries}]", "", None])
    return rows


def _matches_pattern(path: Path, root: Path, pattern: str) -> bool:
    """``glob``/``rglob`` semantics for the patterns that actually reach
    this tool. A pattern with no separator matches the file *name* at any
    depth (``*.csv``), exactly as ``rglob`` does; one containing a separator
    is matched against the path relative to ``root``."""
    relative = path.relative_to(root)
    if "/" in pattern or os.sep in pattern:
        return fnmatch(str(relative), pattern)
    return fnmatch(path.name, pattern)


def _find_files_sync(
    root: Path,
    pattern: str,
    *,
    recursive: bool,
    max_entries: int,
    cancel: threading.Event | None = None,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    truncated = False
    # Bounded: the walk stops the moment the cap is reached, instead of
    # materializing and sorting every match in the tree first. See
    # _walk_files.
    for path in _walk_files(root, recursive=recursive, cancel=cancel):
        if not _matches_pattern(path, root, pattern):
            continue
        if len(rows) >= max_entries:
            truncated = True
            break
        rows.append(_entry_row(path, base=root))
    if truncated:
        rows.append([f"... [more matches truncated at {max_entries}]", "", None])
    return rows


def _search_text_sync(
    root: Path,
    query: str,
    *,
    recursive: bool,
    case_sensitive: bool,
    max_matches: int,
    cancel: threading.Event | None = None,
) -> list[list[Any]]:
    needle = query if case_sensitive else query.lower()
    rows: list[list[Any]] = []
    truncated = False

    # Bounded and lazy, for the same reason as _find_files_sync: the search
    # stops walking as soon as it has enough matches.
    for path in _walk_files(root, recursive=recursive, cancel=cancel):
        if len(rows) >= max_matches:
            truncated = True
            break
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


def _resolve_destination(source: Path, destination: Path) -> Path:
    """The path a copy/move will actually write to.

    ``shutil.copy2``/``shutil.move`` accept a *directory* as the
    destination and place the file inside it under its own name — so
    "destination" as the model passes it isn't necessarily the file that
    ends up on disk, and an existence check against the raw argument would
    ask the wrong question."""
    return destination / source.name if destination.is_dir() else destination


def _refuse_existing_destination(target: Path, *, overwrite: bool) -> ToolResult | None:
    """``ToolResult`` refusing to clobber ``target``, or ``None`` if the
    write may proceed.

    ``write_file`` has always refused to replace an existing file without
    an explicit ``overwrite=true``, but ``copy_file``/``move_file`` sitting
    right beside it went straight through ``shutil`` and overwrote without
    asking — and unlike ``delete_file`` there was no ``_trash`` copy to
    recover from, so an agent told to "copy the reduced data over" could
    destroy a file in the target folder with nothing to undo it. Same flag,
    same default, same wording as ``write_file`` now."""
    if not target.exists() or overwrite:
        return None
    return ToolResult(
        content=f"{target} already exists — pass overwrite=true to replace it.", is_error=True
    )


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
        rows = await _run_scan(
            _list_directory_sync, candidate, recursive=recursive, max_entries=max_list_entries
        )
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
        rows = await _run_scan(
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
        rows = await _run_scan(
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
        artifacts = await _run_blocking(
            read_document,
            candidate,
            max_chars=INTERACTIVE_MAX_CHARS,
            max_pdf_pages=INTERACTIVE_MAX_PDF_PAGES,
        )
        content = "\n\n".join(
            describe_for_model(a, max_chars=INTERACTIVE_MAX_CHARS) for a in artifacts
        )
        return ToolResult(content=content, artifacts=artifacts)

    @_tool
    async def write_file(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        content = arguments["content"]
        overwrite = bool(arguments.get("overwrite", False))
        candidate = await guard.authorize_write(path)
        if candidate.exists() and not overwrite:
            return ToolResult(
                content=f"{candidate} already exists — pass overwrite=true to replace it.",
                is_error=True,
            )

        def _write() -> None:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(content, encoding="utf-8")

        await _run_mutation(_write, target=candidate, description="write")
        mime_type = mimetypes.guess_type(str(candidate))[0]
        artifact = FileArtifact(path=str(candidate), mime_type=mime_type)
        return ToolResult(
            content=f"Wrote {len(content)} character(s) to {candidate}", artifacts=[artifact]
        )

    @_tool
    async def create_directory(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        candidate = await guard.authorize_write(path)
        await _run_mutation(
            lambda: candidate.mkdir(parents=True, exist_ok=True),
            target=candidate,
            description="directory creation",
        )
        return ToolResult(content=f"Created directory {candidate}")

    @_tool
    async def copy_file(arguments: dict[str, Any]) -> ToolResult:
        source = await guard.authorize_read(arguments["source"])
        destination = await guard.authorize_write(arguments["destination"])
        overwrite = bool(arguments.get("overwrite", False))
        if not source.is_file():
            return ToolResult(content=f"Not a file: {source}", is_error=True)
        target = _resolve_destination(source, destination)
        refusal = _refuse_existing_destination(target, overwrite=overwrite)
        if refusal is not None:
            return refusal

        def _copy() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        await _run_mutation(_copy, target=target, description="copy")
        mime_type = mimetypes.guess_type(str(target))[0]
        artifact = FileArtifact(path=str(target), mime_type=mime_type)
        return ToolResult(content=f"Copied {source} -> {target}", artifacts=[artifact])

    @_tool
    async def move_file(arguments: dict[str, Any]) -> ToolResult:
        # Moving mutates the source location too (the file is removed from
        # there), so the source goes through authorize_delete, not
        # authorize_read — same confirmation gating a delete would get.
        source = await guard.authorize_delete(arguments["source"])
        destination = await guard.authorize_write(arguments["destination"])
        overwrite = bool(arguments.get("overwrite", False))
        if not source.exists():
            return ToolResult(content=f"Not found: {source}", is_error=True)
        target = _resolve_destination(source, destination)
        refusal = _refuse_existing_destination(target, overwrite=overwrite)
        if refusal is not None:
            return refusal

        def _move() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                # shutil.move onto an existing *directory* target moves the
                # source inside it instead of replacing it; the target here
                # is already fully resolved, so remove first to make
                # overwrite mean overwrite in every case.
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(source), str(target))

        await _run_mutation(_move, target=target, description="move")
        mime_type = mimetypes.guess_type(str(target))[0]
        artifact = FileArtifact(path=str(target), mime_type=mime_type)
        return ToolResult(content=f"Moved {source} -> {target}", artifacts=[artifact])

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
                        "recursive": {
                            "type": "boolean",
                            "description": "List subdirectories too. Default false.",
                        },
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
                        "recursive": {
                            "type": "boolean",
                            "description": "Search subdirectories too. Default true.",
                        },
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
                        "overwrite": {
                            "type": "boolean",
                            "description": "Replace an existing file. Default false.",
                        },
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
                    "properties": {
                        "path": {"type": "string", "description": "Directory to create."}
                    },
                    "required": ["path"],
                },
            ),
            func=create_directory,
        ),
        NativeTool(
            schema=ToolSchema(
                name="copy_file",
                description=(
                    "Copy a file to a new location. Fails if the destination already exists "
                    "unless overwrite=true."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "description": "Replace an existing destination file. Default false.",
                        },
                    },
                    "required": ["source", "destination"],
                },
            ),
            func=copy_file,
        ),
        NativeTool(
            schema=ToolSchema(
                name="move_file",
                description=(
                    "Move (rename) a file to a new location. Fails if the destination already "
                    "exists unless overwrite=true."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "description": "Replace an existing destination file. Default false.",
                        },
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
