"""``aida-gui`` with a broken config file shows a dialog naming the file
and exits, instead of failing silently (a windowed script on Windows has
no console to print a traceback to). Review B5/P5."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from aida.ui.qt import app as app_module  # noqa: E402


def test_broken_config_shows_a_dialog_and_exits(qapp, aida_home: Path, monkeypatch):
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "config.yaml").write_text("key: [unclosed\n", encoding="utf-8")
    shown = []
    monkeypatch.setattr(
        app_module.QMessageBox, "critical", lambda parent, title, text: shown.append(text)
    )
    assert app_module.main([]) == 1
    assert shown and "config.yaml line 2" in shown[0]
