"""Allowed-folders safety model (PLAN.md Phase 6): every native file
operation goes through a ``SafetyGuard`` before it touches disk.

Allowed folders are the union of a workspace's ``source_folders`` +
``target_folder`` (implicitly allowed — that's the whole point of
configuring them) and any globally-allowed folders in
``AppConfig.allowed_folders``. A path outside that union always requires
confirmation, regardless of the workspace's ``safety`` mode ("Always-confirm
regardless of mode: paths outside allowed folders" — PLAN.md). Inside the
allowed set, ``relaxed`` mode proceeds without asking; ``confirm`` mode asks
before every write/delete (reads inside allowed folders are never gated —
the per-mutating-action confirmation PLAN.md describes is about writes and
deletes, not about looking at files the workspace was already configured to
read).

Confirmation flow — a deliberate design choice, not a shortcut: rather than
adding a new bidirectional request/reply protocol to ``aida.core.events``
(today's event stream is a strictly one-directional async generator — core
yields, frontend consumes, with no "pause and wait for a reply" support to
build on without a larger generator-protocol refactor), a ``SafetyGuard`` is
handed a plain ``async def confirm(ConfirmationRequest) -> bool`` callback
at construction time — the same "closure captured at tool-build time"
pattern ``aida.mcp.manager`` already uses for its own tool closures. The
CLI's callback (``aida.cli.chat``) blocks on a real terminal prompt; the
GUI's (``aida.ui.qt.bridge``/``main_window``) shows a real modal
``QMessageBox`` on the Qt thread and bridges the answer back to the
background asyncio loop thread via ``asyncio.wrap_future``. Both reach the
user in the exact interface they're already looking at, which is the actual
goal "flows through the event stream (GUI dialog / CLI prompt)" is after.

Delete = move into a ``_trash`` folder at the root of whichever allowed
folder contains the file, not a hard delete (``SafetyGuard.trash_enabled``
turns this off).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from aida.config.logging_setup import get_logger
from aida.config.paths import unique_destination
from aida.core.confirmation import (
    ConfirmationDenied,
    ConfirmationRequest,
    ConfirmCallback,
    deny_all,
)

logger = get_logger("safety")

# ConfirmationDenied/ConfirmationRequest/ConfirmCallback/deny_all are
# imported above and re-exported here (still in this module's __all__) so
# existing importers — aida.workspace.files, aida.documents.tools,
# aida.cli.chat, aida.ui.qt.bridge/main_window, tests — keep working
# unchanged. The definitions moved to the dependency-free
# aida.core.confirmation so aida.mcp.manager can share them too, for a
# per-tool "confirm before run" flag, without an import cycle; see that
# module's docstring for why the cycle is real, not hypothetical.


def normalize_path(path: str | Path) -> Path:
    """Resolves symlinks and collapses ``..`` (PLAN.md: "path normalization
    incl. network mounts & symlinks") so containment checks can't be
    defeated by either. ``strict=False`` so a not-yet-existing target folder
    ("created on first write") or a not-yet-existing new file still
    normalizes instead of raising ``FileNotFoundError``."""
    return Path(path).expanduser().resolve(strict=False)


# ``unique_destination`` is imported above and re-exported here (it stays in
# this module's __all__) so existing importers — aida.documents.writers.*,
# the safety tests — keep working unchanged. The definition moved to the
# dependency-free aida.config.paths so aida.artifacts.store can use it too
# without an import cycle; see its docstring there.


@dataclass
class SafetyGuard:
    """Owns one session's allowed-folders set + confirmation policy. Native
    file tools (``aida.workspace.files``) call ``authorize_read`` /
    ``authorize_write`` / ``delete`` before touching disk; nothing in this
    class does I/O itself beyond ``delete``'s trash-move (reads/writes stay
    the tool's own job — this class only decides whether they're allowed)."""

    allowed_roots: list[Path]
    mode: str = "confirm"  # "relaxed" | "confirm"
    trash_enabled: bool = True
    trash_dirname: str = "_trash"
    confirm_callback: ConfirmCallback = field(default=deny_all)

    def __post_init__(self) -> None:
        self.allowed_roots = [normalize_path(p) for p in self.allowed_roots]

    @classmethod
    def for_workspace(
        cls,
        *,
        source_folders: list[str] | None = None,
        target_folder: str | None = None,
        global_allowed_folders: list[str] | None = None,
        mode: str = "confirm",
        confirm_callback: ConfirmCallback | None = None,
        trash_enabled: bool = True,
    ) -> SafetyGuard:
        """Builds the allowed-roots union PLAN.md describes: a workspace's
        own source/target folders plus whatever's globally allowed —
        convenience constructor over the raw dataclass for the common case
        (``start_session`` uses this directly)."""
        roots: list[str] = list(source_folders or [])
        if target_folder:
            roots.append(target_folder)
        roots.extend(global_allowed_folders or [])
        return cls(
            allowed_roots=[Path(r) for r in roots],
            mode=mode,
            trash_enabled=trash_enabled,
            confirm_callback=confirm_callback or deny_all,
        )

    def _containing_root(self, candidate: Path) -> Path | None:
        for root in self.allowed_roots:
            if candidate == root or root in candidate.parents:
                return root
        return None

    def is_allowed(self, path: str | Path) -> bool:
        return self._containing_root(normalize_path(path)) is not None

    async def _authorize(self, action: str, path: str | Path, *, always_confirm_in_bounds: bool) -> Path:
        candidate = normalize_path(path)
        inside = self._containing_root(candidate) is not None
        logger.debug(
            "authorize %s %s (inside_allowed_roots=%s mode=%s allowed_roots=%s)",
            action,
            candidate,
            inside,
            self.mode,
            [str(r) for r in self.allowed_roots],
        )

        if not inside:
            logger.info("%s outside allowed folders, requesting confirmation: %s", action, candidate)
            approved = await self.confirm_callback(
                ConfirmationRequest(
                    action=action,
                    path=str(candidate),
                    detail=f"{action} outside the allowed folders: {candidate}",
                )
            )
            logger.info("%s outside allowed folders %s: %s", action, "approved" if approved else "denied", candidate)
            if not approved:
                raise ConfirmationDenied(f"{action} outside allowed folders declined: {candidate}")
            return candidate

        if always_confirm_in_bounds or self.mode == "confirm":
            approved = await self.confirm_callback(
                ConfirmationRequest(action=action, path=str(candidate), detail=f"{action.capitalize()} {candidate}?")
            )
            logger.debug("%s inside allowed folders %s: %s", action, "approved" if approved else "denied", candidate)
            if not approved:
                raise ConfirmationDenied(f"{action} declined: {candidate}")

        return candidate

    async def authorize_read(self, path: str | Path) -> Path:
        """Reads inside allowed folders are never gated, in either mode —
        only reads *outside* the allowed set need confirmation (handled by
        ``_authorize``'s inside/outside branch)."""
        candidate = normalize_path(path)
        if self._containing_root(candidate) is None:
            return await self._authorize("read", candidate, always_confirm_in_bounds=False)
        return candidate

    async def authorize_write(self, path: str | Path) -> Path:
        return await self._authorize("write", path, always_confirm_in_bounds=False)

    async def authorize_delete(self, path: str | Path) -> Path:
        return await self._authorize("delete", path, always_confirm_in_bounds=False)

    async def delete(self, path: str | Path) -> Path:
        """Authorizes, then moves to ``_trash`` (or hard-deletes if
        ``trash_enabled`` is ``False``). Returns the file's final path: the
        trash destination, or the (now-gone) original path if hard-deleted."""
        candidate = await self.authorize_delete(path)
        if not candidate.exists():
            raise FileNotFoundError(str(candidate))

        if not self.trash_enabled:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
            return candidate

        root = self._containing_root(candidate) or candidate.parent
        trash_root = root / self.trash_dirname
        trash_root.mkdir(parents=True, exist_ok=True)
        destination = unique_destination(trash_root / candidate.name)
        shutil.move(str(candidate), str(destination))
        return destination


RELAXED_MODE_WARNING = (
    "Relaxed mode: the agent may create, modify, move, or delete files in this "
    "workspace's allowed folders without asking first. Make sure those folders "
    "are backed up (deletions still go to a recoverable '_trash' folder unless "
    "that's disabled, but overwrites are not undo-able)."
)


def relaxed_mode_warning_if_newly_enabled(previous_safety: str | None, new_safety: str) -> str | None:
    """The "one-time clear warning when enabling relaxed mode" task item:
    returns the warning text the moment a workspace's mode actually
    *changes* to ``"relaxed"`` (including first creation with
    ``safety="relaxed"``, where ``previous_safety`` is ``None``) — ``None``
    otherwise, so a caller (CLI/GUI) only shows it at that transition, not
    on every subsequent load of an already-relaxed workspace."""
    if new_safety == "relaxed" and previous_safety != "relaxed":
        return RELAXED_MODE_WARNING
    return None


__all__ = [
    "RELAXED_MODE_WARNING",
    "ConfirmCallback",
    "ConfirmationDenied",
    "ConfirmationRequest",
    "SafetyGuard",
    "deny_all",
    "normalize_path",
    "relaxed_mode_warning_if_newly_enabled",
    "unique_destination",
]
