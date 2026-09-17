"""Shared GUI handling for ``aida.config.settings.ConfigConflictError``
(PLAN.md's "two AIDA instances sharing ``~/.aida``" gap): a dialog that
holds a config file open in memory for a while — the real collision window,
unlike a CLI command's short load-mutate-save-exit — should tell the user
another AIDA window changed the file instead of silently overwriting their
edit. Kept out of ``aida.config.settings`` itself since that module is core
config and must stay Qt-free (PLAN.md §3 layering)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from aida.config.settings import ConfigConflictError
from aida.ui.qt._qt import QMessageBox, QWidget

T = TypeVar("T")


def save_or_warn_conflict(parent: QWidget, save: Callable[[], T]) -> T | None:
    """Runs ``save`` (a zero-arg closure over the real ``save_*_config``
    call); on ``ConfigConflictError`` shows an explanatory message instead of
    letting the exception propagate, and returns ``None`` so the caller can
    tell "conflict, don't proceed" apart from a real result (e.g. the
    ``Path`` every ``save_*_config`` returns, which is always truthy)."""
    try:
        return save()
    except ConfigConflictError:
        QMessageBox.warning(
            parent,
            "Changed by Another AIDA Window",
            "This file was changed by another AIDA window since this dialog "
            "was opened. Close and reopen the dialog to see the latest "
            "version, then reapply your change.",
        )
        return None
