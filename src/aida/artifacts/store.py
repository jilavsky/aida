"""Persist binary artifacts to ``~/.aida/artifacts/`` and track lightweight
metadata about them.

Phase 3 task: "Artifact store: binaries written under ``~/.aida/artifacts/``,
metadata kept for Phase 4 DB; helper to also save a copy into a target
folder." The metadata list here is exactly that groundwork — an in-memory
list for now; Phase 4 persists the same shape into SQLite rather than
inventing a new one.
"""

from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path

from aida.artifacts.base import FileArtifact, ImageArtifact
from aida.config.paths import artifacts_dir


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
        Mutates and returns the same artifact with ``path`` set."""
        if artifact.path is None:
            ext = mimetypes.guess_extension(artifact.mime_type) or ".bin"
            filename = artifact.filename or f"{artifact.id}{ext}"
            dest = self.base_dir / filename
            dest.write_bytes(artifact.data)
            artifact.path = str(dest)
        self._record(artifact.id, "ImageArtifact", artifact.path, artifact.mime_type)
        return artifact

    def save_file(self, artifact: FileArtifact) -> FileArtifact:
        """Write file bytes to disk if not already on disk, and record it."""
        if artifact.path is None and artifact.data is not None:
            filename = artifact.filename or artifact.id
            dest = self.base_dir / filename
            dest.write_bytes(artifact.data)
            artifact.path = str(dest)
        self._record(artifact.id, "FileArtifact", artifact.path, artifact.mime_type)
        return artifact

    def copy_to_target(self, artifact: ImageArtifact | FileArtifact, target_dir: Path) -> Path:
        """Copy an already-saved artifact into a workspace's target folder
        (PLAN.md §6: generated files should land where the user asked)."""
        if artifact.path is None:
            raise ValueError(f"artifact {artifact.id!r} has no path to copy — save() it first")
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / Path(artifact.path).name
        shutil.copy2(artifact.path, dest)
        return dest

    def _record(self, artifact_id: str, kind: str, path: str | None, mime_type: str | None) -> None:
        self._metadata.append(ArtifactMetadata(id=artifact_id, kind=kind, path=path, mime_type=mime_type))

    def list_metadata(self) -> list[ArtifactMetadata]:
        return list(self._metadata)


__all__ = ["ArtifactMetadata", "ArtifactStore"]
