"""Persist binary artifacts to ``~/.aida/artifacts/`` and track lightweight
metadata about them.

Phase 3 task: "Artifact store: binaries written under ``~/.aida/artifacts/``,
metadata kept for Phase 4 DB; helper to also save a copy into a target
folder." The metadata list here is exactly that groundwork — an in-memory
list for now; Phase 4 persists the same shape into SQLite rather than
inventing a new one.
"""

from __future__ import annotations

import filecmp
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path

from aida.artifacts.base import FileArtifact, ImageArtifact
from aida.config.paths import artifacts_dir, unique_destination


def _safe_filename(name: str | None, fallback: str) -> str:
    """Reduce an artifact's suggested filename to a bare, harmless basename.

    An artifact's ``filename`` is **not** trusted input: it can come
    straight from an MCP server's content block (``ResourceLink.name``, or
    the mime-derived name ``aida.mcp.results`` builds for audio), and a
    third-party server is free to put ``../../..`` or an absolute path in
    it. Verified: without this, ``filename="../../escaped.txt"`` wrote
    outside ``~/.aida/artifacts/`` entirely. Anything that doesn't reduce to
    a usable single path component falls back to the artifact id.
    """
    if not name:
        return fallback
    # PureWindowsPath-style separators too: an MCP server on Windows may
    # hand back "dir\\file.png", which POSIX Path treats as one component.
    candidate = Path(name.replace("\\", "/")).name.strip()
    if not candidate or candidate in (".", ".."):
        return fallback
    return candidate


@dataclass
class ArtifactMetadata:
    """What Phase 4's persistence layer will want to store per artifact."""

    id: str
    kind: str
    path: str | None
    mime_type: str | None


class ArtifactStore:
    """Writes image/file artifacts to disk and remembers what it wrote."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or artifacts_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._metadata: list[ArtifactMetadata] = []

    def save_image(self, artifact: ImageArtifact) -> ImageArtifact:
        """Write image bytes to disk (unless already saved) and record it.
        Mutates and returns the same artifact with ``path`` set.

        The destination is uniquified rather than overwritten: two artifacts
        can easily arrive with the same suggested ``filename`` (the audio
        name ``aida.mcp.results`` derives is a fixed ``audio.<subtype>``, and
        an MCP server may reuse ``plot.png`` for every figure), and silently
        replacing the first one's bytes would make an earlier artifact in
        the same conversation render as a later, unrelated image.
        """
        if artifact.path is None:
            ext = mimetypes.guess_extension(artifact.mime_type) or ".bin"
            filename = _safe_filename(artifact.filename, f"{artifact.id}{ext}")
            dest = unique_destination(self.base_dir / filename)
            dest.write_bytes(artifact.data)
            artifact.path = str(dest)
        self._record(artifact.id, "ImageArtifact", artifact.path, artifact.mime_type)
        return artifact

    def save_file(self, artifact: FileArtifact) -> FileArtifact:
        """Write file bytes to disk if not already on disk, and record it.
        Same sanitized, collision-safe naming as ``save_image``."""
        if artifact.path is None and artifact.data is not None:
            filename = _safe_filename(artifact.filename, artifact.id)
            dest = unique_destination(self.base_dir / filename)
            dest.write_bytes(artifact.data)
            artifact.path = str(dest)
        self._record(artifact.id, "FileArtifact", artifact.path, artifact.mime_type)
        return artifact

    def adopt_image_file(self, source: Path, *, mime_type: str | None = None) -> ImageArtifact:
        """Take a copy of an image that already exists somewhere else on
        disk, and record it as this store's own.

        For user *attachments*. A GUI attachment used to be carried as an
        ``ImageRef`` pointing straight at whatever path the file picker
        returned — a Desktop screenshot, a file inside a mounted share, a
        temp file — so the conversation's only reference to those pixels was
        a path the user was free to move, rename, or delete, and which might
        not resolve at all on another machine. Owning a copy is what makes
        an attachment survive being resumed later; the store's existing
        collision-safe naming means two files called ``Screenshot.png`` do
        not overwrite each other.

        Bytes are read here rather than deferred: this is the moment the
        original is known to exist.
        """
        resolved = Path(source).expanduser()
        guessed = mime_type or mimetypes.guess_type(str(resolved))[0] or "image/png"
        artifact = ImageArtifact(
            data=resolved.read_bytes(), mime_type=guessed, filename=resolved.name
        )
        return self.save_image(artifact)

    def copy_to_target(self, artifact: ImageArtifact | FileArtifact, target_dir: Path) -> Path:
        """Copy an already-saved artifact into a workspace's target folder
        (PLAN.md §6: generated files should land where the user asked).

        Collision handling has to satisfy two callers with opposite needs,
        which is why it keys off content rather than just the name:

        - ``aida.persistence.records.write_transcript`` re-copies *the same*
          artifacts into the same sidecar folder on every single transcript
          export (i.e. after every message). It must stay idempotent —
          uniquifying unconditionally would grow ``fig (1).png``,
          ``fig (2).png``, ... without bound as a conversation runs.
        - ``aida.documents.writers.md_obsidian`` embeds *different* images
          into one report. Two of them can share a basename, and
          overwriting there silently drops a figure from the report.

        So: an existing destination with identical content is reused as-is;
        an existing destination with *different* content gets a fresh
        collision-safe name.
        """
        if artifact.path is None:
            raise ValueError(f"artifact {artifact.id!r} has no path to copy — save() it first")
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / _safe_filename(Path(artifact.path).name, artifact.id)
        if dest.exists() and filecmp.cmp(artifact.path, dest, shallow=False):
            return dest  # same bytes already there — re-export, not a collision
        dest = unique_destination(dest)
        shutil.copy2(artifact.path, dest)
        return dest

    def _record(self, artifact_id: str, kind: str, path: str | None, mime_type: str | None) -> None:
        self._metadata.append(
            ArtifactMetadata(id=artifact_id, kind=kind, path=path, mime_type=mime_type)
        )

    def list_metadata(self) -> list[ArtifactMetadata]:
        return list(self._metadata)


__all__ = ["ArtifactMetadata", "ArtifactStore"]
