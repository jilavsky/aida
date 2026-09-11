"""GUI front end for `aida config export` / `aida config import`.

Three small dialogs: pick what to export, pick how to import, and read the
report. The report is the substantial one — a bundle can carry configuration
but not the conda environments, MCP server packages, data folders or
keychain entries it refers to, so "what still needs doing on this machine"
is the actual product of an import and it needs somewhere readable to land.

The import dialog grew two tabs in Tier 2. **Contents** is a checkable tree
of what the bundle holds: check one workspace and its profile, skills,
knowledge bases and MCP servers check themselves, because importing a
workspace without them produces something that looks configured and is not
(`aida.portability.closure`). **Paths** lists what will not resolve on this
machine, with an editable column to say where it really is — a mapping
matches by prefix, so one row can redirect a whole tree.

Both tabs appear only once a readable bundle is selected, and the Paths tab
only when there is actually something wrong: a dialog that shows an empty
problem table on the happy path teaches people to ignore it.
"""

from __future__ import annotations

from pathlib import Path

from aida.portability import (
    CONFLICT_POLICIES,
    KIND_ORDER,
    ROLE_EXECUTABLE,
    BundleContents,
    BundleError,
    PathIssue,
    PathMapper,
    expand_selection,
    inspect_paths,
    read_contents,
)
from aida.ui.qt._qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

#: Column indexes of the Paths tab.
_COL_WHAT, _COL_BUNDLE_VALUE, _COL_REPLACEMENT = range(3)

#: Role on a tree item holding its ``(kind, name)`` key.
_KEY_ROLE = Qt.ItemDataRole.UserRole

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
    """Which bundle, which parts of it, where its paths point, and what to
    do about names that already exist here."""

    def __init__(self, parent: QWidget | None = None, *, source: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Setup")
        self.setMinimumSize(760, 560)
        self._contents: BundleContents | None = None
        self._issues: list[PathIssue] = []
        #: Guards the cascade in ``_on_item_changed``: checking a workspace
        #: checks its dependencies, and each of those fires the same signal
        #: again. Without this the closure re-runs once per checkbox.
        self._updating = False

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
        self._path_edit.editingFinished.connect(self._on_bundle_changed)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._on_browse)
        form.addRow("Bundle:", _browse_row(self._path_edit, browse))
        layout.addLayout(form)

        self._status_label = QLabel("Choose a bundle to see what it contains.", self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._tabs = QTabWidget(self)
        self._tabs.setVisible(False)
        layout.addWidget(self._tabs, 1)

        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Item", "Detail"])
        self._tree.setColumnWidth(0, 320)
        self._tree.itemChanged.connect(self._on_item_changed)
        contents_tab = QWidget(self)
        contents_layout = QVBoxLayout(contents_tab)
        contents_layout.setContentsMargins(0, 6, 0, 0)
        contents_layout.addWidget(self._tree, 1)
        select_row = QHBoxLayout()
        for label, checked in (("Select all", True), ("Select none", False)):
            button = QPushButton(label, contents_tab)
            button.clicked.connect(lambda _=False, value=checked: self._set_all(value))
            select_row.addWidget(button)
        select_row.addStretch(1)
        self._closure_label = QLabel("", contents_tab)
        self._closure_label.setWordWrap(True)
        select_row.addWidget(self._closure_label, 1)
        contents_layout.addLayout(select_row)
        self._tabs.addTab(contents_tab, "Contents")

        self._paths_table = QTableWidget(0, 3, self)
        self._paths_table.setHorizontalHeaderLabels(["Used by", "In the bundle", "Use instead"])
        header = self._paths_table.horizontalHeader()
        header.setSectionResizeMode(_COL_WHAT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_BUNDLE_VALUE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_REPLACEMENT, QHeaderView.ResizeMode.Stretch)
        paths_tab = QWidget(self)
        paths_layout = QVBoxLayout(paths_tab)
        paths_layout.setContentsMargins(0, 6, 0, 0)
        paths_hint = QLabel(
            "These do not exist on this machine. Fill in where they really are, or "
            "leave a row blank to import it unchanged and fix it later. A replacement "
            "matches by prefix, so remapping a parent folder moves everything under it.",
            paths_tab,
        )
        paths_hint.setWordWrap(True)
        paths_layout.addWidget(paths_hint)
        paths_layout.addWidget(self._paths_table, 1)
        browse_row = QHBoxLayout()
        browse_row.addStretch(1)
        self._browse_folder_button = QPushButton("Browse for selected row…", paths_tab)
        self._browse_folder_button.clicked.connect(self._on_browse_replacement)
        browse_row.addWidget(self._browse_folder_button)
        paths_layout.addLayout(browse_row)
        self._paths_tab = paths_tab

        options = QFormLayout()
        self._conflict_combo = QComboBox(self)
        for policy in CONFLICT_POLICIES:
            self._conflict_combo.addItem(_CONFLICT_LABELS[policy], policy)
        options.addRow("If a name already exists:", self._conflict_combo)
        layout.addLayout(options)

        self._app_settings_check = QCheckBox("Also apply the bundle's general settings", self)
        self._app_settings_check.setToolTip(
            "Safety mode, allowed folders, iteration and context caps, records and "
            "scratch folders. Off by default so importing someone else's workspaces "
            "cannot change your safety mode."
        )
        self._app_settings_check.toggled.connect(lambda _: self._refresh_paths())
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
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if source:
            self._on_bundle_changed()

    # -- bundle loading ---------------------------------------------------

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import AIDA setup", self._path_edit.text(), "AIDA setup bundle (*.zip)"
        )
        if path:
            self._path_edit.setText(path)
            self._on_bundle_changed()

    def _on_bundle_changed(self) -> None:
        """Load the bundle the user named and rebuild both tabs.

        A bad path is reported in the status line rather than a modal: the
        user is still filling the field in, and a message box on every
        keystroke-completion would be intolerable.
        """
        text = self._path_edit.text().strip()
        if not text:
            self._set_no_bundle("Choose a bundle to see what it contains.")
            return
        try:
            contents = read_contents(text)
        except (BundleError, OSError) as exc:
            self._set_no_bundle(str(exc))
            return
        self._contents = contents
        self._populate_tree(contents)
        created = f" · created {contents.created_at}" if contents.created_at else ""
        self._status_label.setText(f"{len(contents.items())} item(s){created}")
        self._tabs.setVisible(True)
        self._ok_button.setEnabled(True)
        self._refresh_paths()

    def _set_no_bundle(self, message: str) -> None:
        self._contents = None
        self._issues = []
        self._tabs.setVisible(False)
        self._tree.clear()
        self._status_label.setText(message)
        self._ok_button.setEnabled(False)

    def _populate_tree(self, contents: BundleContents) -> None:
        self._updating = True
        try:
            self._tree.clear()
            by_kind: dict[str, list] = {}
            for item in contents.items():
                by_kind.setdefault(item.kind, []).append(item)
            for kind in KIND_ORDER:
                entries = by_kind.get(kind)
                if not entries:
                    continue
                parent = QTreeWidgetItem(self._tree, [f"{kind} ({len(entries)})", ""])
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                parent.setExpanded(True)
                for entry in entries:
                    child = QTreeWidgetItem(parent, [entry.name, entry.detail])
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Checked)
                    child.setData(0, _KEY_ROLE, entry.key)
        finally:
            self._updating = False
        self._closure_label.setText("")

    # -- selection --------------------------------------------------------

    def _child_items(self) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            out.extend(parent.child(j) for j in range(parent.childCount()))
        return out

    def _set_all(self, checked: bool) -> None:
        self._updating = True
        try:
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for child in self._child_items():
                child.setCheckState(0, state)
        finally:
            self._updating = False
        self._apply_closure()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0 or item.data(0, _KEY_ROLE) is None:
            return
        self._apply_closure()

    def _apply_closure(self) -> None:
        """Check whatever the current selection needs, and say what was
        added. Unchecking never cascades: removing a profile that three
        other workspaces still need would be a surprising thing for a
        checkbox to do, and the closure re-adds it on the next change
        anyway."""
        if self._contents is None:
            return
        seeds = {
            child.data(0, _KEY_ROLE)
            for child in self._child_items()
            if child.checkState(0) == Qt.CheckState.Checked
        }
        chosen = expand_selection(self._contents, seeds)
        self._updating = True
        try:
            for child in self._child_items():
                if child.data(0, _KEY_ROLE) in chosen.keys:
                    child.setCheckState(0, Qt.CheckState.Checked)
        finally:
            self._updating = False

        notes: list[str] = []
        if chosen.added:
            names = ", ".join(f"{kind}: {name}" for kind, name in sorted(chosen.added))
            notes.append(f"Also needed — {names}")
        if chosen.missing:
            notes.append(
                chosen.missing[0]
                if len(chosen.missing) == 1
                else f"{len(chosen.missing)} unsatisfied reference(s) — see the report"
            )
        self._closure_label.setText(" · ".join(notes))
        self._refresh_paths()

    # -- paths ------------------------------------------------------------

    def _refresh_paths(self) -> None:
        """Recompute the problem list against the current selection *and*
        the replacements typed so far, so the table shrinks as it is filled
        in — otherwise a user cannot tell whether their entry worked."""
        if self._contents is None:
            return
        overrides = self.path_overrides()
        chosen = expand_selection(self._contents, self.selection())
        self._issues = inspect_paths(
            self._contents,
            PathMapper(overrides=overrides),
            chosen,
            include_app=self._app_settings_check.isChecked(),
        )
        remembered = dict(overrides)
        self._paths_table.setRowCount(len(self._issues))
        for row, issue in enumerate(self._issues):
            what = QTableWidgetItem(
                f"{issue.where[0]}"
                + (f" (+{len(issue.where) - 1})" if len(issue.where) > 1 else "")
            )
            what.setToolTip("\n".join(issue.where) + f"\n\n{issue.problem}")
            what.setFlags(what.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value = QTableWidgetItem(issue.value)
            value.setFlags(value.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value.setToolTip(issue.problem)
            replacement = QTableWidgetItem(remembered.get(issue.value, issue.resolved_hint))
            self._paths_table.setItem(row, _COL_WHAT, what)
            self._paths_table.setItem(row, _COL_BUNDLE_VALUE, value)
            self._paths_table.setItem(row, _COL_REPLACEMENT, replacement)

        index = self._tabs.indexOf(self._paths_tab)
        if self._issues and index == -1:
            self._tabs.addTab(self._paths_tab, f"Paths ({len(self._issues)})")
        elif self._issues:
            self._tabs.setTabText(index, f"Paths ({len(self._issues)})")
        elif index != -1:
            self._tabs.removeTab(index)

    def _on_browse_replacement(self) -> None:
        row = self._paths_table.currentRow()
        if row < 0 or row >= len(self._issues):
            return
        issue = self._issues[row]
        current = self._paths_table.item(row, _COL_REPLACEMENT)
        start = (current.text() if current else "") or str(Path.home())
        if issue.role == ROLE_EXECUTABLE:
            chosen, _ = QFileDialog.getOpenFileName(self, "Locate the program", start)
        else:
            chosen = QFileDialog.getExistingDirectory(self, "Locate the folder", start)
        if chosen:
            self._paths_table.setItem(row, _COL_REPLACEMENT, QTableWidgetItem(chosen))

    # -- results ----------------------------------------------------------

    def source(self) -> Path:
        return Path(self._path_edit.text().strip()).expanduser()

    def conflict(self) -> str:
        return str(self._conflict_combo.currentData())

    def apply_app_settings(self) -> bool:
        return self._app_settings_check.isChecked()

    def selection(self) -> set[tuple[str, str]] | None:
        """The checked items, or ``None`` when everything is checked.

        ``None`` rather than "all the keys" on purpose: it is what tells
        ``import_bundle`` this was not a partial pick, which in turn keeps
        the report from listing every item as a dependency of itself."""
        if self._contents is None:
            return None
        checked = {
            child.data(0, _KEY_ROLE)
            for child in self._child_items()
            if child.checkState(0) == Qt.CheckState.Checked
        }
        return None if checked == self._contents.all_keys() else checked

    def path_overrides(self) -> dict[str, str]:
        overrides: dict[str, str] = {}
        for row in range(self._paths_table.rowCount()):
            value_item = self._paths_table.item(row, _COL_BUNDLE_VALUE)
            replacement_item = self._paths_table.item(row, _COL_REPLACEMENT)
            if value_item is None or replacement_item is None:
                continue
            replacement = replacement_item.text().strip()
            if replacement:
                overrides[value_item.text()] = replacement
        return overrides


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
