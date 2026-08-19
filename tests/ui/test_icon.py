"""Tests for aida.ui.qt.icon — PLAN.md Phase 5 task "app icon"."""

from __future__ import annotations

from aida.ui.qt._qt import QIcon
from aida.ui.qt.icon import _ICON_PATH, app_icon


def test_icon_file_exists_and_is_bundled_under_the_package():
    assert _ICON_PATH.exists()
    assert "aida/ui/qt/resources" in str(_ICON_PATH).replace("\\", "/")


def test_app_icon_returns_a_non_null_qicon(qapp):
    icon = app_icon()
    assert isinstance(icon, QIcon)
    assert not icon.isNull()
