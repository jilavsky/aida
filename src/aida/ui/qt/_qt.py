"""Single Qt import shim (pyIrena pattern).

Every Qt import anywhere in AIDA must go through this module, so that:

- normalizing between PySide6/PyQt6 (if ever needed) touches one file, and
- the layering contract test (``tests/test_contract_layering.py``) can
  reliably assert "no Qt import outside aida.ui" by checking for direct
  ``PySide6``/``PyQt6`` imports anywhere else in the tree.

Phase 1 ships no GUI, so this shim is currently unused scaffolding for
Phase 5. It intentionally does not import PySide6 yet — doing so would add
a hard runtime dependency to a package whose ``gui`` extra is optional.
"""

from __future__ import annotations

try:  # pragma: no cover - exercised once PySide6 is an installed extra
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False
