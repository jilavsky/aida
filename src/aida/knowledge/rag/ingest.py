"""Walk a knowledge base's source folders, chunk, embed, and store — full
rebuild or incremental-by-mtime (Phase 8).

Reuses ``aida.documents.readers.read_document`` (Phase 6) for text
extraction rather than reimplementing PDF/DOCX/PPTX parsing — this module
only adds chunking + embedding + storage on top of what that already
returns.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from aida.artifacts.base import JsonArtifact, TableArtifact, TextArtifact
from aida.config.settings import KnowledgeBaseConfig
from aida.documents.readers import read_document
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.chunking import Chunk, chunk_markdown, chunk_plain_text
from aida.providers.embeddings_base import EmbeddingsProvider

_MD_SUFFIXES = {".md", ".markdown"}

#: Extensions ingest actually walks. PLAN.md's own list ("MD, PDF, TXT,
#: RST, PY, DOCX — reuse Phase 6 readers"), plus .pptx and .markdown as a
#: harmless superset since the same readers already support them. A
#: knowledge base indexes *text* content — the image/spreadsheet formats
#: ``read_document`` also supports (for the agent's own ``read_file`` tool)
#: have nothing for an embedding to act on and are deliberately excluded.
INGESTIBLE_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".py", ".pdf", ".docx", ".pptx"}

#: read_document()'s own defaults (DEFAULT_MAX_CHARS=20_000,
#: DEFAULT_MAX_PDF_PAGES=50) exist to keep a single tool-result response
#: bounded for a model's context window — exactly the constraint RAG
#: ingestion must *not* inherit, since chunking's whole job is covering a
#: long document across many chunks. These overrides are "large enough
#: that no real single document hits the ceiling," not "unlimited".
_INGEST_MAX_CHARS = 2_000_000
_INGEST_MAX_PDF_PAGES = 2000

#: How many chunk texts go into a single ``embeddings.create`` request.
#:
#: Every chunk of a file used to go in *one* call: a 200-page PDF at
#: chunk_size 1000 is roughly a thousand inputs in a single request — past
#: Ollama's practical batch limit and close to OpenAI's, so the request
#: failed and, because ingest treats a failing file as skipped, the entire
#: document was silently dropped from the index. 64 is a conservative batch
#: size every embeddings backend AIDA talks to handles comfortably.
EMBED_BATCH_SIZE = 64


@dataclass
class IngestResult:
    """What one build/update pass did — the GUI's progress display and
    ``aida kb build/update`` print this."""

    added_files: list[str] = field(default_factory=list)
    updated_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    #: Unreadable files are recorded, not silently dropped — but don't
    #: abort the rest of the pass over one bad file.
    skipped_files: list[str] = field(default_factory=list)
    #: Real-use bug: a configured source folder that doesn't resolve to a
    #: real, listable directory — a typo, a folder later deleted/unmounted,
    #: a permissions error on a cloud-synced folder (iCloud Drive/OneDrive
    #: placeholder mounts are the common case), or — the case that actually
    #: surfaced this — a `file://` URI pasted from a file manager's "Copy
    #: as URI"/"Copy Path" action. All used to fail completely silently:
    #: `_discover_files` just skipped the folder, so a build reported
    #: "added 0, updated 0" with no indication why. Recorded here so the
    #: CLI/GUI can print an actionable warning instead.
    missing_folders: list[str] = field(default_factory=list)
    chunk_count: int = 0


def _extract_text(path: Path) -> str:
    """Flatten a file's ``read_document()`` artifacts into one text blob
    for chunking."""
    artifacts = read_document(path, max_chars=_INGEST_MAX_CHARS, max_pdf_pages=_INGEST_MAX_PDF_PAGES)
    parts: list[str] = []
    for artifact in artifacts:
        if isinstance(artifact, TextArtifact):
            parts.append(artifact.text)
        elif isinstance(artifact, TableArtifact):
            parts.append(" | ".join(artifact.columns))
            parts.extend(" | ".join(str(cell) for cell in row) for row in artifact.rows)
        elif isinstance(artifact, JsonArtifact):
            parts.append(json.dumps(artifact.data, default=str))
    return "\n\n".join(parts)


_WINDOWS_DRIVE_URI_PATH = re.compile(r"^/[A-Za-z]:/")


def normalize_source_folder(raw: str) -> str:
    """Accept a plain filesystem path or a ``file://`` URI. Several file
    managers' "Copy as URI"/"Copy Path" actions (Obsidian's among them)
    produce a ``file://...`` string rather than a plain path — pasted
    verbatim into a source-folders field, ``Path("file:///Users/...")``
    silently fails ``.is_dir()`` (it's not a valid relative path, but it's
    not an error either), so the whole folder was skipped with zero
    indication why. Percent-decoded too, since a real OS-generated URI
    encodes spaces/unicode in the path.

    A Windows file URI (``file:///C:/Users/...``) needs one more step:
    ``urlparse().path`` keeps the URI's leading slash, giving
    ``/C:/Users/...`` — ``PureWindowsPath`` parses that as a *relative*
    path with a folder literally named ``C:``, not the ``C:`` drive, since
    the leading slash isn't a drive marker. Stripped here so a Windows
    user's pasted URI round-trips to a real absolute path instead of
    silently becoming un-discoverable the same way the un-normalized URI
    used to be."""
    raw = raw.strip()
    if raw.startswith("file://"):
        path = unquote(urlparse(raw).path)
        if _WINDOWS_DRIVE_URI_PATH.match(path):
            path = path[1:]
        # A malformed file:// string can leave urlparse with an empty
        # path — found via a Windows CI test bug (f"file://{windows_path}"
        # glues backslashes straight onto the scheme with no "/" boundary,
        # so urlparse reads the whole remainder as netloc, not path).
        # Path("") silently resolves to the current working directory —
        # returning the original string instead means it fails
        # _folder_is_usable and gets reported as missing, rather than
        # silently ingesting whatever directory the process happens to be
        # running in.
        return path or raw
    return raw


def _resolved_folder(folder: str) -> Path:
    return Path(normalize_source_folder(folder)).expanduser()


def _folder_is_usable(root: Path) -> bool:
    """A source entry is usable if it's a listable directory *or* an
    individual readable file — ``source_folders`` accepts either (a real
    request: "index just this one file" shouldn't require making a folder
    for it). A cloud-synced folder (iCloud Drive, OneDrive, ...) can pass
    ``is_dir()`` while still raising a permission error the moment
    something tries to enumerate it — a placeholder-mount quirk, or a
    folder the OS hasn't granted this process access to (macOS TCC being
    the common case). Treated the same as "doesn't exist" — both end in
    "nothing was indexed from this entry", and the fix (grant access /
    check the path) is the same shape either way."""
    if root.is_file():
        return True
    if not root.is_dir():
        return False
    try:
        next(root.iterdir(), None)
    except OSError:
        return False
    return True


def _missing_source_folders(source_folders: list[str]) -> list[str]:
    """Which configured source entries don't resolve to a real, listable
    directory or readable file — see ``IngestResult.missing_folders``'s
    docstring."""
    return [folder for folder in source_folders if not _folder_is_usable(_resolved_folder(folder))]


def _discover_files(source_folders: list[str]) -> list[Path]:
    files: list[Path] = []
    for folder in source_folders:
        root = _resolved_folder(folder)
        if root.is_file():
            if root.suffix.lower() in INGESTIBLE_SUFFIXES:
                files.append(root)
            continue
        if not _folder_is_usable(root):
            continue
        try:
            candidates = sorted(root.rglob("*"))
        except OSError:
            continue  # a subfolder lost access mid-walk — reported via missing_folders, not raised here
        files.extend(
            path for path in candidates if path.is_file() and path.suffix.lower() in INGESTIBLE_SUFFIXES
        )
    return files


def _chunk_file(path: Path, text: str, *, chunk_size: int, overlap: int) -> list[Chunk]:
    if path.suffix.lower() in _MD_SUFFIXES:
        return chunk_markdown(text, chunk_size=chunk_size, overlap=overlap)
    return chunk_plain_text(text, chunk_size=chunk_size, overlap=overlap)


async def _embed_in_batches(
    embeddings_provider: EmbeddingsProvider, texts: list[str], *, batch_size: int = EMBED_BATCH_SIZE
) -> list[list[float]]:
    """Embed ``texts`` in fixed-size batches, preserving order — see
    ``EMBED_BATCH_SIZE``. A failure in any batch propagates, so the caller's
    per-file error handling still records the whole file as skipped rather
    than storing a half-embedded document."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(await embeddings_provider.embed(texts[start : start + batch_size]))
    return vectors


async def _ingest_file(
    conn: sqlite3.Connection, path: Path, *, kb: KnowledgeBaseConfig, embeddings_provider: EmbeddingsProvider
) -> int:
    """Chunk, embed, and store one file. Returns the chunk count written.
    Raises on an unreadable file (an ``OSError``, a document-reader-
    specific parse error, ...) — callers record it as skipped rather than
    aborting the pass."""
    # _extract_text -> read_document() is synchronous (PDF/DOCX/PPTX
    # parsing, disk I/O) and this runs on the one shared background loop
    # that also drives chat turns and MCP calls — a large PDF parse would
    # otherwise freeze the whole app for its duration. Embedding calls are
    # already async (embeddings_provider.embed); this is the one remaining
    # blocking step in the ingest path.
    text = await asyncio.to_thread(_extract_text, path)
    chunks = _chunk_file(path, text, chunk_size=kb.chunk_size, overlap=kb.chunk_overlap)
    if not chunks:
        kb_index.delete_source(conn, str(path))
        return 0
    embeddings = await _embed_in_batches(embeddings_provider, [chunk.text for chunk in chunks])
    kb_index.replace_file_chunks(
        conn,
        source_path=str(path),
        mtime=path.stat().st_mtime,
        chunks_with_embeddings=list(zip(chunks, embeddings, strict=True)),
        embedding_profile=kb.embedding_profile or "",
    )
    return len(chunks)


async def _run_ingest(
    conn: sqlite3.Connection, kb: KnowledgeBaseConfig, embeddings_provider: EmbeddingsProvider, *, force: bool
) -> IngestResult:
    result = IngestResult()
    result.missing_folders = _missing_source_folders(kb.source_folders)
    indexed_mtimes = kb_index.indexed_source_mtimes(conn)
    seen_paths: set[str] = set()

    for path in _discover_files(kb.source_folders):
        key = str(path)
        already_indexed = key in indexed_mtimes
        # This stat() sat outside the try below, so a file deleted (or a
        # network share unmounted) between discovery and this check raised
        # straight out of _run_ingest and aborted the whole pass — the one
        # thing every other failure here is careful not to do. It's also
        # what decides whether the file still exists at all, so it happens
        # before seen_paths records it: a file that has genuinely vanished
        # should be pruned from the index below, not kept alive by having
        # been discovered a moment earlier.
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            result.skipped_files.append(f"{key}: {exc}")
            continue

        seen_paths.add(key)
        if not force and already_indexed and indexed_mtimes[key] == mtime:
            continue  # unchanged since the last build/update — skip re-embedding it

        try:
            count = await _ingest_file(conn, path, kb=kb, embeddings_provider=embeddings_provider)
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort a pass over many
            # Deliberately broad, not just (OSError, UnsupportedDocumentFormatError):
            # verified a real corrupt-but-.pdf-named file raises
            # pymupdf.FileDataError, a library-specific exception type that
            # isn't a subclass of either — the same "a crash from one item
            # must not take down the whole batch" reasoning
            # aida.core.agent.AgentLoop._run_turns already documents for
            # tool calls applies here across potentially hundreds of files.
            result.skipped_files.append(f"{key}: {exc}")
            continue

        (result.updated_files if already_indexed else result.added_files).append(key)
        result.chunk_count += count

    for stale_path in set(indexed_mtimes) - seen_paths:
        kb_index.delete_source(conn, stale_path)
        result.removed_files.append(stale_path)

    return result


async def rebuild(
    conn: sqlite3.Connection, kb: KnowledgeBaseConfig, embeddings_provider: EmbeddingsProvider
) -> IngestResult:
    """Full re-ingest: every discovered file is (re-)chunked and
    (re-)embedded regardless of whether its mtime already matches the
    index, and anything indexed but no longer discovered is pruned."""
    return await _run_ingest(conn, kb, embeddings_provider, force=True)


async def update(
    conn: sqlite3.Connection, kb: KnowledgeBaseConfig, embeddings_provider: EmbeddingsProvider
) -> IngestResult:
    """Incremental re-ingest: a file whose mtime matches what's already
    indexed is skipped entirely (no re-embedding cost); new/changed files
    are (re-)ingested; files no longer discovered are pruned."""
    return await _run_ingest(conn, kb, embeddings_provider, force=False)


__all__ = [
    "EMBED_BATCH_SIZE",
    "INGESTIBLE_SUFFIXES",
    "IngestResult",
    "normalize_source_folder",
    "rebuild",
    "update",
]
