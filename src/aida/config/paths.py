"""Resolve and create AIDA's on-device directories.

Two roots (PLAN.md §4):

- ``~/.aida/`` — app state: config files, secrets refs, the SQLite DB,
  binary artifacts, and logs. Never inside a repo; never contains secrets.
- ``~/Documents/Aida/`` (configurable via ``config.yaml``'s ``records_dir``)
  — human-readable conversation records / exported transcripts. Safe to
  browse, safe to delete.

Every function here is idempotent: calling it repeatedly, or on first run
with nothing on disk yet, must succeed and create what's missing.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_ENV_VAR = "AIDA_HOME"
DEFAULT_RECORDS_DIRNAME = "Aida"


def app_dir() -> Path:
    """Return ``~/.aida/`` (or ``$AIDA_HOME`` if set), creating it if needed.

    ``AIDA_HOME`` override exists for tests and for headless/CI use so tests
    never touch a developer's real ``~/.aida``.
    """
    override = os.environ.get(APP_DIR_ENV_VAR)
    base = Path(override).expanduser() if override else Path.home() / ".aida"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_dir() -> Path:
    """Directory holding config.yaml, providers.yaml, workspaces.yaml, mcp.json."""
    d = app_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifacts_dir() -> Path:
    """Directory for binary tool-result artifacts (PNGs, etc.) — files, not DB blobs."""
    d = app_dir() / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    """Directory for rotating log files."""
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def skills_dir() -> Path:
    """Directory holding the user's own skills markdown files."""
    d = app_dir() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workflows_dir() -> Path:
    """Directory holding stored named workflows (Phase 10)."""
    d = app_dir() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """Path to the SQLite database file (created lazily by aida.persistence)."""
    return app_dir() / "aida.db"


def default_records_dir() -> Path:
    """Default location for human-readable conversation records.

    Overridable per-install via ``config.yaml``'s ``records_dir`` — callers
    that need the *effective* records dir should read it from settings, not
    call this directly, except as the fallback default.
    """
    return Path.home() / "Documents" / DEFAULT_RECORDS_DIRNAME


def ensure_records_dir(path: Path | None = None) -> Path:
    """Create (if needed) and return the records dir, honoring an override."""
    target = Path(path).expanduser() if path else default_records_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target


def unique_destination(path: Path) -> Path:
    """Collision-safe destination: ``name.ext`` -> ``name (1).ext`` ->
    ``name (2).ext`` ... — used by every writer that must never silently
    clobber an existing file (trash moves, report/transcript writers, the
    artifact store).

    Lives in this leaf module rather than next to its most obvious caller
    (``aida.workspace.safety``, which still re-exports it for compatibility)
    only because ``aida.artifacts.store`` needs it too, and
    ``artifacts -> workspace`` is a cycle: ``aida.workspace``'s package
    ``__init__`` reaches ``aida.mcp``, which imports ``ArtifactStore``.
    ``aida.config.paths`` imports nothing from AIDA at all, so everyone can
    depend on it.
    """
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
