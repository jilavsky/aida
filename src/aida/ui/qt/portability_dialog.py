"""GUI front end for `aida config export` / `aida config import`.

Three small dialogs: pick what to export, pick how to import, and read the
report. The report is the substantial one — a bundle can carry configuration
but not the conda environments, MCP server packages, data folders or
keychain entries it refers to, so "what still needs doing on this machine"
is the actual product of an import and it needs somewhere readable to land.

Deliberately *not* here: a per-item pick list of which workspaces/profiles
to import. That is the expensive half (it needs dependency closure — a
workspace pulls its profile, skills, knowledge bases and the MCP servers in
its group, or it imports as broken) and it is tracked as Tier 2 in
`planning/portability.md`. Skip-on-conflict plus a report covers the case
this feature was asked for: standing up a second machine.
"""

from __future__ import annotations

from pathlib import Path

from aida.portability import CONFLICT_POLICIES
from aida.ui.qt._qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
    QWidget,
)

#: Shown next to each conflict policy so the choice does not depend on the
#: user already knowing what the words mean here.
_CONFLICT_LABELS = {
    "skip": "Skip it, keep mine (recommended)",
    "overwrite": "Replace mine with the bundle's",
    "rename": "Import it as “<name> (imported)”",
}


def _browse_row(edit: QLineEdit, button: QPushButton) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(edit, 1)
    layout.addWidget(button)
    return row


class ExportSetupDialog(QDialog):
    """Where to write the bundle, and whether personal fields go in it."""

    def __init__(self, parent: QWidget | None = None, *, default_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Setup")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Writes your provider profiles, workspaces, MCP servers, knowledge bases, "
            "schedules, workflows, skills and prompts to one file you can import on "
            "another machine.\n\n"
            "No API key, token or password is ever written to a bundle — the import "
            "report lists the ones to re-enter on the other machine.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        base = default_dir or Path.home()
        self._path_edit = QLineEdit(str(base / "aida-setup.zip"), self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._on_browse)
        form.addRow("Save to:", _browse_row(self._path_edit, browse))
        layout.addLayout(form)

        self._personal_check = QCheckBox(
            "Include my personal context and private workspace notes", self
        )
        self._personal_check.setToolTip(
            "For moving to your own second machine. Leave this off for a bundle you "
            "send to someone else."
        )
        layout.addWidget(self._personal_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export AIDA setup", self._path_edit.text(), "AIDA setup bundle (*.zip)"
        )
        if path:
            self._path_edit.setText(path)

    def destination(self) -> Path:
        """The chosen path, with a ``.zip`` suffix supplied if the user
        typed a bare name — a bundle without one is awkward to recognise
        later, and on Windows awkward to open at all."""
        text = self._path_edit.text().strip()
        path = Path(text).expanduser()
        return path if path.suffix else path.with_suffix(".zip")

    def include_personal(self) -> bool:
        return self._personal_check.isChecked()


class ImportSetupDialog(QDialog):
    """Which bundle, and what to do about names that already exist here."""

    def __init__(self, parent: QWidget | None = None, *, source: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Setup")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Merges a bundle written by another AIDA install into this one. "
            "Nothing is deleted, and anything already configured here is left alone "
            "unless you choose otherwise below.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._path_edit = QLineEdit(str(source) if source else "", self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._on_browse)
        form.addRow("Bundle:", _browse_row(self._path_edit, browse))

        self._conflict_combo = QComboBox(self)
        for policy in CONFLICT_POLICIES:
            self._conflict_combo.addItem(_CONFLICT_LABELS[policy], policy)
        form.addRow("If a name already exists:", self._conflict_combo)
        layout.addLayout(form)

        self._app_settings_check = QCheckBox("Also apply the bundle's general settings", self)
        self._app_settings_check.setToolTip(
            "Safety mode, allowed folders, iteration and context caps, records and "
            "scratch folders. Off by default so importing someone else's workspaces "
            "cannot change your safety mode."
        )
        layout.addWidget(self._app_settings_check)

        note = QLabel(
            "Secrets never travel in a bundle. After importing, set each one the "
            "report lists — Providers… for a profile's key, MCP Servers… for a "
            "server's environment.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import AIDA setup", self._path_edit.text(), "AIDA setup bundle (*.zip)"
        )
        if path:
            self._path_edit.setText(path)

    def source(self) -> Path:
        return Path(self._path_edit.text().strip()).expanduser()

    def conflict(self) -> str:
        return str(self._conflict_combo.currentData())

    def apply_app_settings(self) -> bool:
        return self._app_settings_check.isChecked()


class BundleReportDialog(QDialog):
    """The export/import report, in a monospaced, selectable, copyable box.

    Read-only text rather than a structured tree on purpose: the report is
    something the user needs to *act on* away from AIDA — install a conda
    env, run a few `aida config secret set` lines — so the ability to select
    and paste it matters more than a prettier presentation of it.
    """

    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(720, 520)
        layout = QVBoxLayout(self)

        self._view = QPlainTextEdit(text, self)
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self._view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        copy_button = QPushButton("Copy", self)
        copy_button.clicked.connect(self._on_copy)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _on_copy(self) -> None:
        self._view.selectAll()
        self._view.copy()
        cursor = self._view.textCursor()
        cursor.clearSelection()
        self._view.setTextCursor(cursor)


__all__ = ["BundleReportDialog", "ExportSetupDialog", "ImportSetupDialog"]
