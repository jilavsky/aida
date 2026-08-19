"""Persisted window geometry + font size (Phase 5).

The actual storage is ``aida.config.settings.AppConfig`` — read/written via
the same ``load_app_config``/``save_app_config`` every other config value
uses. This module is only the Qt-specific glue: apply a loaded
``AppConfig`` to a live window/application, and capture a live window's
current geometry back into one before saving.
"""

from __future__ import annotations

from aida.config.settings import AppConfig
from aida.ui.qt._qt import QApplication, QMainWindow, QPoint, QSize

DEFAULT_WINDOW_SIZE = QSize(1100, 800)


def apply_window_state(window: QMainWindow, app_config: AppConfig) -> None:
    """Restore a previously-saved size/position, or fall back to a sane
    default size and let Qt/the window manager place the window (first run,
    or a saved position that no longer matches any connected monitor —
    nothing here validates that the saved position is still on-screen;
    every desktop window manager already handles an off-screen window
    reasonably, and guessing at "still visible" here would just be another
    way to get it wrong)."""
    width = app_config.window_width or DEFAULT_WINDOW_SIZE.width()
    height = app_config.window_height or DEFAULT_WINDOW_SIZE.height()
    window.resize(QSize(width, height))
    if app_config.window_x is not None and app_config.window_y is not None:
        window.move(QPoint(app_config.window_x, app_config.window_y))


def capture_window_state(window: QMainWindow, app_config: AppConfig) -> AppConfig:
    """Mutate and return ``app_config`` with the window's current geometry —
    call right before ``save_app_config`` on close."""
    geometry = window.geometry()
    app_config.window_width = geometry.width()
    app_config.window_height = geometry.height()
    app_config.window_x = geometry.x()
    app_config.window_y = geometry.y()
    return app_config


def apply_font_size(app: QApplication, app_config: AppConfig) -> None:
    """Set the application-wide default font's point size from config. Any
    widget created after this reflects it immediately; widgets that
    override their own font explicitly are unaffected, same as any Qt app."""
    font = app.font()
    font.setPointSize(app_config.font_size)
    app.setFont(font)


__all__ = ["apply_font_size", "apply_window_state", "capture_window_state"]
