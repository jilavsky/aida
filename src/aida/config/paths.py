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
import shutil
from collections.abc import Sequence
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


def bundled_skills_dir() -> Path | None:
    """Where AIDA's own sample skills live, or ``None`` if they aren't
    there.

    They are authored once, at `skills/` in the repo — editable,
    reviewable, and linked from the docs — and `pyproject.toml`
    force-includes that folder into the wheel as
    `aida/resources/skills`, so there is one source file and two places it
    can be found depending on how AIDA was installed. Checked in that
    order: the packaged copy (a real `pip install`), then the repo folder
    two levels above `src/aida` (an editable/`-e` install, where
    force-include does not apply).

    Before this existed, `skills/` was repo-only: a `pip install
    aida-workbench` user had no skills at all, while the `workspaces.yaml`
    examples and the one-click pyIrena MCP setup both reference them by
    name — so a new user got "skill file(s) not found (will be skipped)"
    and nothing to copy from.
    """
    packaged = Path(__file__).resolve().parent.parent / "resources" / "skills"
    if packaged.is_dir():
        return packaged
    repo = Path(__file__).resolve().parents[3] / "skills"
    return repo if repo.is_dir() else None


def install_bundled_skills(names: Sequence[str] | None = None) -> list[str]:
    """Copy AIDA's bundled sample skills into `~/.aida/skills/`, skipping
    any that already exist, and return the names actually installed.

    Never overwrites: once a skill is in the user's skills folder it is
    *theirs* — edited, tailored to their beamline — and a later AIDA
    upgrade silently replacing it would be the worst kind of data loss.
    `names` limits the copy to specific skills (the pyIrena setup path
    installs only the two it attaches); `None` installs all of them.
    """
    source_dir = bundled_skills_dir()
    if source_dir is None:
        return []
    target_dir = skills_dir()
    installed: list[str] = []
    for source in sorted(source_dir.glob("*.md")):
        if source.stem == "README" or (names is not None and source.stem not in names):
            continue
        destination = target_dir / source.name
        if destination.exists():
            continue
        try:
            shutil.copyfile(source, destination)
        except OSError:
            continue  # a read-only or full home directory must not break setup
        installed.append(source.stem)
    return installed


def workflows_dir() -> Path:
    """Directory holding stored named workflows (Phase 10)."""
    d = app_dir() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """Path to the SQLite database file (created lazily by aida.persistence)."""
    return app_dir() / "aida.db"


def schedules_path() -> Path:
    """Path to the schedule store (``~/.aida/schedules.yaml``, Phase 10) —
    one shared YAML file, unlike ``workflows_dir()``'s one-file-per-workflow
    layout, since a schedule is a short config row rather than an editable
    document (planning/phase10_scheduling_design.md §5)."""
    return app_dir() / "schedules.yaml"


def scheduler_lock_path() -> Path:
    """Path to the scheduler's cross-process advisory lock file (Phase 10)
    — see ``aida.core.proc_lock``. Not the DB and not a config file: purely
    a lock, so it lives at the top level of ``~/.aida/`` next to ``aida.db``
    rather than under any of the directories above."""
    return app_dir() / "scheduler.lock"


def knowledge_dir() -> Path:
    """Directory holding one SQLite file per RAG knowledge base
    (``<kb_name>.db``, created lazily by ``aida.knowledge.rag.index``) —
    PLAN.md Phase 8: "index stored under ``~/.aida/`` per knowledge base"."""
    d = app_dir() / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def knowledge_db_path(kb_name: str) -> Path:
    """Path to one knowledge base's own SQLite index file. A deliberately
    separate file per KB (not shared with ``aida.db`` or with each other)
    so deleting/rebuilding one knowledge base can never touch another's
    data, or the conversations DB — same isolation reasoning as the
    per-conversation sidecar folders in ``aida.persistence.records``."""
    return knowledge_dir() / f"{kb_name}.db"


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


def default_scratch_dir() -> Path:
    """Default location for transient working files (scripts, downloads,
    intermediate MCP-tool output) that agents and MCP servers need
    *somewhere* to write.

    Lives under ``~/.aida/`` rather than under ``~/Documents/Aida/`` on
    purpose: this folder churns (many small files written and deleted in
    quick succession), and ``~/Documents/Aida/`` may be inside a
    cloud-synced folder (iCloud Drive, OneDrive, ...) — the same class of
    problem already seen with cloud-synced Obsidian vaults raising
    ``PermissionError`` on rapid writes (see ``test_knowledge_ingest.py``).
    Overridable per-install via ``config.yaml``'s ``scratch_dir``; callers
    that need the *effective* scratch dir should read it from settings, not
    call this directly, except as the fallback default.
    """
    return app_dir() / "tmp"


def ensure_scratch_dir(path: Path | None = None) -> Path:
    """Create (if needed) and return the scratch dir, honoring an override."""
    target = Path(path).expanduser() if path else default_scratch_dir()
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
