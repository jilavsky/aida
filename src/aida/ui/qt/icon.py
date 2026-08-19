"""AIDA's application icon (PLAN.md Phase 5 task: "``aida-gui`` entry
point; app icon; window state..."). One small bundled PNG
(``resources/app_icon.png``, included in the wheel automatically — hatchling
packages every file under ``src/aida``, not just ``*.py``, confirmed by
inspecting a built wheel) loaded once and shared by ``aida.ui.qt.app``
(``QApplication.setWindowIcon``, the taskbar/dock icon) and
``aida.ui.qt.main_window.MainWindow`` (``setWindowIcon``, the title bar
icon on platforms that show one per-window)."""

from __future__ import annotations

from pathlib import Path

from aida.ui.qt._qt import QIcon

_ICON_PATH = Path(__file__).resolve().parent / "resources" / "app_icon.png"


def app_icon() -> QIcon:
    return QIcon(str(_ICON_PATH))


__all__ = ["app_icon"]
