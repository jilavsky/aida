"""Contract tests guarding PLAN.md §3's layering rules.

1. Nothing outside ``aida.ui`` may import Qt (PySide6/PyQt6), anywhere,
   including transitively-obvious direct imports (a grep-style static check,
   not a runtime import check, so it works even without PySide6 installed).
2. All Qt imports inside ``aida.ui.qt`` go through ``_qt.py`` — no other
   module under ``aida.ui.qt`` may import PySide6/PyQt6 directly.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "aida"
QT_IMPORT_RE = re.compile(r"^\s*(from|import)\s+(PySide6|PyQt6)\b", re.MULTILINE)


def _python_files(root: Path):
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_qt_import_outside_ui():
    offenders = []
    for path in _python_files(SRC_ROOT):
        rel = path.relative_to(SRC_ROOT)
        if rel.parts[0] == "ui":
            continue
        text = path.read_text(encoding="utf-8")
        if QT_IMPORT_RE.search(text):
            offenders.append(str(rel))
    assert not offenders, f"Qt imported outside aida.ui in: {offenders}"


def test_qt_imports_go_through_shim():
    qt_dir = SRC_ROOT / "ui" / "qt"
    offenders = []
    for path in _python_files(qt_dir):
        if path.name == "_qt.py":
            continue
        text = path.read_text(encoding="utf-8")
        if QT_IMPORT_RE.search(text):
            offenders.append(str(path.relative_to(SRC_ROOT)))
    assert not offenders, f"Qt imported outside the _qt.py shim in: {offenders}"
