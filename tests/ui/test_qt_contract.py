"""Contract tests (PLAN.md Phase 5): no module outside
``aida.ui.qt._qt`` may import PySide6/PyQt directly (checked anywhere in the
file, including inside function bodies — there is no legitimate lazy
exception to *that* rule), and no module under
``aida.core``/``aida.providers``/``aida.persistence``/``aida.artifacts``/
``aida.mcp``/``aida.workspace``/``aida.cli``/``aida.config`` may import
``aida.ui`` at *module level* — Qt is a frontend dependency, never a core
one, and PLAN.md's actual hard rule is "core remains importable and
testable without Qt": a plain ``import aida.cli`` must work with no
PySide6 installed. ``aida.cli.__main__.main_gui`` is the one deliberate
exception this allows for: it imports ``aida.ui.qt.app`` lazily inside its
own function body, guarded by ``try/except ImportError``, so it only
requires PySide6 if a user actually runs ``aida-gui`` — module-level-only
scanning is what lets that pass without weakening the rule itself.

Both checks work by scanning source text (an AST walk) rather than
actually importing every module, so these tests run and mean something
even in an environment with PySide6 not installed at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "aida"
QT_SHIM_PATH = SRC_ROOT / "ui" / "qt" / "_qt.py"

CORE_PACKAGES = (
    "core",
    "providers",
    "persistence",
    "artifacts",
    "mcp",
    "workspace",
    "cli",
    "config",
)


def _imported_top_level_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _module_level_imported_dotted_modules(tree: ast.Module) -> set[str]:
    """Full dotted module paths imported at *module level* only — direct
    children of the module body, not walked into function/class bodies.
    Deliberately narrower than ``_imported_top_level_modules`` above: a
    lazy, function-scoped import (like ``main_gui``'s) doesn't make the
    module itself require Qt to import, which is the actual thing PLAN.md's
    rule protects."""
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _all_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_qt_shim_file_exists():
    assert QT_SHIM_PATH.exists(), f"expected the Qt shim at {QT_SHIM_PATH}"


def test_no_direct_pyside_or_pyqt_import_outside_shim():
    offenders: list[str] = []
    for path in _all_py_files(SRC_ROOT):
        if path == QT_SHIM_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        top_level = _imported_top_level_modules(tree)
        if {"PySide6", "PyQt6", "PyQt5", "shiboken6"} & top_level:
            offenders.append(str(path.relative_to(SRC_ROOT.parent.parent)))
    assert not offenders, (
        "these files import a Qt binding directly instead of going through "
        f"aida.ui.qt._qt: {offenders}"
    )


def test_core_packages_never_import_aida_ui_at_module_level():
    offenders: list[str] = []
    for package in CORE_PACKAGES:
        pkg_dir = SRC_ROOT / package
        if not pkg_dir.exists():
            continue
        for path in _all_py_files(pkg_dir):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            dotted = _module_level_imported_dotted_modules(tree)
            if any(m == "aida.ui" or m.startswith("aida.ui.") for m in dotted):
                offenders.append(str(path.relative_to(SRC_ROOT.parent.parent)))
    assert not offenders, f"core modules must never import aida.ui at module level: {offenders}"


def test_main_gui_imports_ui_lazily_inside_a_function_not_at_module_level():
    """The specific exception the relaxed check above allows for —
    pinned down explicitly so a future refactor that hoists the import to
    module level (silently defeating "core works without Qt") fails loudly
    here even though the relaxed AST check above wouldn't catch it by
    itself changing shape."""
    path = SRC_ROOT / "cli" / "__main__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_level = _module_level_imported_dotted_modules(tree)
    assert not any(m == "aida.ui" or m.startswith("aida.ui.") for m in module_level)

    main_gui = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main_gui"
    )
    imports_inside = {
        node.module
        for node in ast.walk(main_gui)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "aida.ui.qt.app" in imports_inside
