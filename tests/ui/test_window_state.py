"""Tests for aida.ui.qt.window_state — persisted window geometry/font size."""

from __future__ import annotations

from aida.config.settings import AppConfig
from aida.ui.qt._qt import QMainWindow
from aida.ui.qt.window_state import (
    DEFAULT_WINDOW_SIZE,
    apply_font_size,
    apply_window_state,
    capture_window_state,
)


def test_apply_window_state_uses_default_size_when_nothing_saved(qapp):
    window = QMainWindow()
    apply_window_state(window, AppConfig())
    assert window.size().width() == DEFAULT_WINDOW_SIZE.width()
    assert window.size().height() == DEFAULT_WINDOW_SIZE.height()


def test_apply_window_state_restores_saved_size():
    window = QMainWindow()
    cfg = AppConfig(window_width=900, window_height=650)
    apply_window_state(window, cfg)
    assert window.size().width() == 900
    assert window.size().height() == 650


def test_capture_then_apply_round_trips_geometry():
    window = QMainWindow()
    window.resize(777, 555)

    cfg = capture_window_state(window, AppConfig())
    assert cfg.window_width == 777
    assert cfg.window_height == 555

    restored = QMainWindow()
    apply_window_state(restored, cfg)
    assert restored.size().width() == 777
    assert restored.size().height() == 555


def test_apply_font_size_sets_application_font(qapp):
    cfg = AppConfig(font_size=16)
    apply_font_size(qapp, cfg)
    assert qapp.font().pointSize() == 16
