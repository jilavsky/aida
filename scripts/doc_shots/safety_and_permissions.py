"""Shots for docs/reference/safety-and-permissions.md.

The GUI's confirmation dialog for a write inside an allowed folder in a
``confirm``-mode workspace. The request comes from a real ``SafetyGuard``
(``authorize_write`` with a capturing callback); the dialog is built by
the real ``MainWindow._ask_confirmation``, with ``QMessageBox.exec``
replaced for the duration of the shot by "show, grab, return" so nothing
blocks waiting for a click.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ._common import Context, shot
from ._package import reset_docs_tmp, shown_folder


def _write_request():
    from aida.workspace.safety import SafetyGuard

    captured = []

    async def capture(request):
        captured.append(request)
        return True

    target = shown_folder("results", "usaxs-2026-11")
    guard = SafetyGuard.for_workspace(
        target_folder=target, mode="confirm", confirm_callback=capture
    )
    asyncio.run(guard.authorize_write(Path(target) / "unified-rg-table.csv"))
    return captured[0]


@shot("safety-and-permissions-01-confirm-dialog")
def confirm_dialog(ctx: Context):
    from aida.ui.qt._qt import QMessageBox, QWidget
    from aida.ui.qt.main_window import MainWindow

    reset_docs_tmp()
    request = _write_request()
    parent = QWidget()
    grabbed = []

    def show_and_grab(box):
        box.show()
        ctx.settle()
        grabbed.append(box.grab())
        box.close()
        return 0

    original = QMessageBox.exec
    QMessageBox.exec = show_and_grab
    try:
        MainWindow._ask_confirmation(parent, request)
    finally:
        QMessageBox.exec = original
    return grabbed[0]
