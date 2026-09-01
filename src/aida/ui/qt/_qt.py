"""The one module allowed to import PySide6 (pyIrena pattern, PLAN.md §10
Phase 5 row: "``_qt.py`` shim: all Qt imports go through it; contract test
fails the build on any direct PySide6/PyQt import elsewhere").

Why: ``aida.core``/``aida.providers``/``aida.persistence``/etc. must stay
importable and testable with no GUI toolkit installed at all (PLAN.md hard
rule: "core remains importable and testable without Qt") — the CLI
(Phase 2) and headless `aida run` (Phase 10) never need PySide6 on the
machine. Funneling every Qt import through this single module also means a
future toolkit swap (or PyQt6 instead of PySide6) touches one file.

Nothing here has any AIDA-specific behavior — it is a pure re-export list.
``aida.ui.qt.test_contract`` (see ``tests/ui/test_qt_contract.py``) enforces
that every other module under ``src/aida`` gets its Qt symbols from here
rather than importing PySide6 directly.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QObject,
    QPoint,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QClipboard,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

__all__ = [
    "QAbstractItemView",
    "QAction",
    "QApplication",
    "QCheckBox",
    "QClipboard",
    "QCloseEvent",
    "QColor",
    "QComboBox",
    "QDesktopServices",
    "QDialog",
    "QDialogButtonBox",
    "QDoubleSpinBox",
    "QFileDialog",
    "QFont",
    "QFormLayout",
    "QFrame",
    "QGroupBox",
    "QGuiApplication",
    "QHBoxLayout",
    "QIcon",
    "QImage",
    "QInputDialog",
    "QKeySequence",
    "QLabel",
    "QLineEdit",
    "QListWidget",
    "QListWidgetItem",
    "QMainWindow",
    "QMenu",
    "QMessageBox",
    "QObject",
    "QPixmap",
    "QPlainTextEdit",
    "QPoint",
    "QPushButton",
    "QScrollArea",
    "QSize",
    "QSizePolicy",
    "QSpinBox",
    "QSplitter",
    "QStatusBar",
    "QSyntaxHighlighter",
    "Qt",
    "QTabWidget",
    "QTextBrowser",
    "QTextCharFormat",
    "QTextCursor",
    "QTextDocument",
    "QTextEdit",
    "QThread",
    "QTimer",
    "QToolBar",
    "QToolButton",
    "QUrl",
    "QVBoxLayout",
    "QWidget",
    "Signal",
    "Slot",
]
