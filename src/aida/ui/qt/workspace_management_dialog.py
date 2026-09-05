"""``WorkspaceManagementDialog`` (U1, planning/improvement_plan_2026-08.md
§3): add/edit/remove named workspaces (``workspaces.yaml``) entirely from
the GUI — previously the one config object with no GUI editor at all (every
other one — MCP servers, knowledge bases, and now providers/embeddings via
U2 — already had one), so a new user had to hand-write YAML or use
``aida workspace new`` from a terminal before AIDA's flagship "workspace ->
source/target folders -> tools" flow meant anything to them.

Structural precedent: mirrors ``aida.ui.qt.mcp_management_dialog`` (list
left; Add/Edit/Remove; form dialog; persisted the moment it happens via
``save_workspace``/``delete_workspace``, no deferred "Save" step). The
folder-list fields (``source_folders``/``command_allowlist``) use the same
one-per-line ``QPlainTextEdit`` convention ``ServerFormDialog``/
``KnowledgeBaseFormDialog`` already use, rather than reusing
``aida.ui.qt.selectors.FolderDisplay`` — that widget is tightly coupled to
the *active chat session's* in-memory workspace state (its own
"Save to Workspace" step, `Remove`/`Change` buttons that mutate live
session folders), not a generic reusable list editor for arbitrary
``WorkspaceConfig`` objects being created or edited outside any session at
all; see ``knowledge_management_dialog``'s module docstring for the same
reasoning applied there first.

No live ``ChatBridge`` actions here (unlike MCP servers/knowledge bases, a
workspace has nothing to start/stop/rebuild) — switching *to* a saved
workspace remains the toolbar's ``WorkspaceSelector``'s job.
"""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import Settings
from aida.core.context import list_skills
from aida.mcp.groups import known_group_names
from aida.ui.qt._qt import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
    QWidget,
)
from aida.workspace.safety import relaxed_mode_warning_if_newly_enabled
from aida.workspace.workspaces import (
    WorkspaceConfig,
    WorkspaceValidation,
    delete_workspace,
    save_workspace,
    validate_workspace,
)

SAFETY_MODES = ["confirm", "relaxed"]
NO_MCP_GROUP_LABEL = "none"


# --- Add/Edit workspace sub-dialog -------------------------------------------


class WorkspaceFormDialog(QDialog):
    """Add (``workspace=None``) or edit (``workspace`` given) one
    ``WorkspaceConfig``."""

    def __init__(
        self,
        *,
        settings: Settings,
        skills_dir: Path,
        workspace: WorkspaceConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = workspace is not None
        # Kept so result_config() can carry forward the fields this form
        # does not show — see its docstring.
        self._original = workspace
        self._previous_safety = workspace.safety if workspace else None
        self.setWindowTitle("Edit Workspace" if self._is_edit else "Add Workspace")
        self.resize(520, 640)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(workspace.name if workspace else "", self)
        self._name_edit.setReadOnly(self._is_edit)  # name is the identity; not renameable in-place
        form.addRow("Name:", self._name_edit)

        self._profile_combo = QComboBox(self)
        self._profile_combo.addItem("(none)")
        self._profile_combo.addItems(sorted(settings.providers.profiles))
        if workspace and workspace.profile:
            index = self._profile_combo.findText(workspace.profile)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)
        form.addRow("Profile:", self._profile_combo)

        self._source_folders_edit = QPlainTextEdit(
            "\n".join(workspace.source_folders) if workspace else "", self
        )
        self._source_folders_edit.setPlaceholderText("One folder per line")
        form.addRow("Source folders:", self._source_folders_edit)

        target_row = QHBoxLayout()
        self._target_folder_edit = QLineEdit(workspace.target_folder if workspace else "", self)
        target_row.addWidget(self._target_folder_edit, stretch=1)
        target_browse = QPushButton("Browse…", self)
        target_browse.clicked.connect(self._on_browse_target)
        target_row.addWidget(target_browse)
        form.addRow("Target folder:", target_row)

        self._sidecar_edit = QLineEdit(workspace.sidecar_folder_name if workspace else "figures", self)
        form.addRow("Sidecar folder name:", self._sidecar_edit)

        self._mcp_group_combo = QComboBox(self)
        self._mcp_group_combo.addItem(NO_MCP_GROUP_LABEL)
        self._mcp_group_combo.addItems([g for g in known_group_names(settings.mcp) if g != NO_MCP_GROUP_LABEL])
        current_group = workspace.mcp_group if workspace else NO_MCP_GROUP_LABEL
        index = self._mcp_group_combo.findText(current_group)
        if index >= 0:
            self._mcp_group_combo.setCurrentIndex(index)
        else:
            self._mcp_group_combo.addItem(current_group)
            self._mcp_group_combo.setCurrentText(current_group)
        form.addRow("MCP group:", self._mcp_group_combo)

        self._skills_list = QListWidget(self)
        self._skills_list.setMaximumHeight(100)
        for info in list_skills(skills_dir):
            item = QListWidgetItem(info.name, self._skills_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if workspace and info.name in workspace.skills else Qt.CheckState.Unchecked
            )
        form.addRow("Skills:", self._skills_list)

        self._kb_list = QListWidget(self)
        self._kb_list.setMaximumHeight(100)
        for name in sorted(settings.knowledge.knowledge_bases):
            item = QListWidgetItem(name, self._kb_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if workspace and name in workspace.knowledge_bases else Qt.CheckState.Unchecked
            )
        form.addRow("Knowledge bases:", self._kb_list)

        self._safety_combo = QComboBox(self)
        self._safety_combo.addItems(SAFETY_MODES)
        index = self._safety_combo.findText(workspace.safety if workspace else "confirm")
        if index >= 0:
            self._safety_combo.setCurrentIndex(index)
        form.addRow("Safety mode:", self._safety_combo)

        system_prompt_row = QVBoxLayout()
        self._system_prompt_edit = QPlainTextEdit(workspace.system_prompt if workspace else "", self)
        self._system_prompt_edit.setPlaceholderText(
            "Literal prompt text, or a path to a .md/.txt file (e.g. prompts/pyirena.md)"
        )
        self._system_prompt_edit.setMaximumHeight(80)
        system_prompt_row.addWidget(self._system_prompt_edit)
        load_file_button = QPushButton("Use a File Reference…", self)
        load_file_button.clicked.connect(self._on_pick_system_prompt_file)
        system_prompt_row.addWidget(load_file_button)
        form.addRow("System prompt:", system_prompt_row)

        self._scripting_checkbox = QCheckBox("Allow running scripts/commands in this workspace", self)
        self._scripting_checkbox.setChecked(workspace.scripting_enabled if workspace else True)
        form.addRow("Scripting:", self._scripting_checkbox)

        self._use_ocr_checkbox = QCheckBox(
            "Use Mistral OCR for figures in attached documents", self
        )
        self._use_ocr_checkbox.setChecked(workspace.use_ocr if workspace else False)
        self._use_ocr_checkbox.setToolTip(
            "Uploads attached documents to Mistral to read their figures. Off by default. "
            "Per workspace because the answer differs: a manuals workspace can have it on "
            "while one used to review unpublished manuscripts keeps it off. You are still "
            "asked before each document is sent."
        )
        form.addRow("Document OCR:", self._use_ocr_checkbox)

        interpreter_row = QHBoxLayout()
        self._interpreter_edit = QLineEdit(workspace.python_interpreter if workspace else "", self)
        self._interpreter_edit.setPlaceholderText("(default: the interpreter AIDA itself runs under)")
        interpreter_row.addWidget(self._interpreter_edit, stretch=1)
        interpreter_browse = QPushButton("Browse…", self)
        interpreter_browse.clicked.connect(self._on_browse_interpreter)
        interpreter_row.addWidget(interpreter_browse)
        form.addRow("Python interpreter:", interpreter_row)

        self._command_allowlist_edit = QPlainTextEdit(
            "\n".join(workspace.command_allowlist) if workspace else "", self
        )
        self._command_allowlist_edit.setPlaceholderText("One allowed command pattern per line, e.g. git status")
        self._command_allowlist_edit.setMaximumHeight(80)
        form.addRow("Command allowlist:", self._command_allowlist_edit)

        # B5: previously hardcoded to 30s with no per-workspace override —
        # a workspace whose scripts legitimately run long (a multi-minute
        # reduction/fit) had no way to raise it short of hand-editing
        # workspaces.yaml.
        self._script_timeout_spin = QSpinBox(self)
        self._script_timeout_spin.setRange(1, 3600)
        self._script_timeout_spin.setSuffix(" s")
        self._script_timeout_spin.setValue(int(workspace.script_timeout_seconds) if workspace else 30)
        form.addRow("Script/command timeout:", self._script_timeout_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse_target(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Target Folder", self._target_folder_edit.text())
        if folder:
            self._target_folder_edit.setText(folder)

    def _on_browse_interpreter(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Python Interpreter", self._interpreter_edit.text())
        if path:
            self._interpreter_edit.setText(path)

    def _on_pick_system_prompt_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "System Prompt File", "", "Text/Markdown (*.md *.txt);;All files (*)"
        )
        if path:
            self._system_prompt_edit.setPlainText(path)

    def _checked_items(self, widget: QListWidget) -> list[str]:
        return [
            widget.item(row).text()
            for row in range(widget.count())
            if widget.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A workspace needs a name.")
            return
        new_safety = self._safety_combo.currentText()
        warning = relaxed_mode_warning_if_newly_enabled(self._previous_safety, new_safety)
        if warning:
            answer = QMessageBox.warning(
                self, "Relaxed Mode", warning, QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
            )
            if answer != QMessageBox.StandardButton.Ok:
                return
        self.accept()

    def result_config(self) -> WorkspaceConfig:
        """The edited workspace.

        Bug report: "I can add Quick tasks into workspace, but these seem to
        disappear." Root cause was here — this built a *fresh*
        ``WorkspaceConfig`` from the form's own widgets, so every field the
        form doesn't show reverted to its dataclass default the moment the
        user pressed OK in the Workspaces… dialog. ``quick_tasks`` (edited
        in the main window's own panel, never in this form) was silently
        emptied; ``templates_dir`` and ``saved_scripts_dir`` (settable by
        hand in ``workspaces.yaml``) were reset to ``None`` the same way.

        Carrying them over from ``self._original`` is deliberately explicit
        rather than a ``replace()`` over the whole dataclass: a new field
        added to ``WorkspaceConfig`` that this form *should* edit will show
        up as a missing widget here, not as a value that quietly survives
        editing.
        """
        original = self._original
        profile = self._profile_combo.currentText()
        mcp_group = self._mcp_group_combo.currentText()
        return WorkspaceConfig(
            name=self._name_edit.text().strip(),
            profile=None if profile == "(none)" else profile,
            source_folders=[line.strip() for line in self._source_folders_edit.toPlainText().splitlines() if line.strip()],
            target_folder=self._target_folder_edit.text().strip() or None,
            sidecar_folder_name=self._sidecar_edit.text().strip() or "figures",
            mcp_group=mcp_group or NO_MCP_GROUP_LABEL,
            skills=self._checked_items(self._skills_list),
            system_prompt=self._system_prompt_edit.toPlainText().strip() or None,
            safety=self._safety_combo.currentText(),
            knowledge_bases=self._checked_items(self._kb_list),
            command_allowlist=[
                line.strip() for line in self._command_allowlist_edit.toPlainText().splitlines() if line.strip()
            ],
            python_interpreter=self._interpreter_edit.text().strip() or None,
            scripting_enabled=self._scripting_checkbox.isChecked(),
            use_ocr=self._use_ocr_checkbox.isChecked(),
            script_timeout_seconds=float(self._script_timeout_spin.value()),
            # Not editable in this form — preserved, not reset:
            quick_tasks=list(original.quick_tasks) if original else [],
            notes=original.notes if original else "",
            templates_dir=original.templates_dir if original else None,
            saved_scripts_dir=original.saved_scripts_dir if original else None,
        )


def _workspace_detail_lines(workspace: WorkspaceConfig, validation: WorkspaceValidation) -> list[str]:
    lines = [
        f"name: {workspace.name}",
        f"profile: {workspace.profile or '(none)'}",
        f"source_folders: {', '.join(workspace.source_folders) or '(none)'}",
        f"target_folder: {workspace.target_folder or '(none)'}",
        f"sidecar_folder_name: {workspace.sidecar_folder_name}",
        f"mcp_group: {workspace.mcp_group}",
        f"skills: {', '.join(workspace.skills) or '(none)'}",
        f"knowledge_bases: {', '.join(workspace.knowledge_bases) or '(none)'}",
        f"safety: {workspace.safety}",
        f"scripting_enabled: {workspace.scripting_enabled}",
        f"use_ocr: {workspace.use_ocr}",
        f"python_interpreter: {workspace.python_interpreter or '(default)'}",
        f"command_allowlist: {', '.join(workspace.command_allowlist) or '(none)'}",
        f"script_timeout_seconds: {workspace.script_timeout_seconds:g}",
        f"quick_tasks: {', '.join(t.name for t in workspace.quick_tasks) or '(none)'}",
        f"notes: {'(set)' if workspace.notes.strip() else '(none)'}",
        f"system_prompt: {'(set)' if workspace.system_prompt else '(none)'}",
    ]
    if validation.warnings:
        lines.append("")
        lines.append("warnings:")
        lines.extend(f"- {w}" for w in validation.warnings)
    return lines


# --- Main dialog ---------------------------------------------------------


class WorkspaceManagementDialog(QDialog):
    def __init__(self, settings: Settings, skills_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workspaces")
        self.resize(680, 460)
        self._settings = settings
        self._skills_dir = skills_dir

        outer = QHBoxLayout(self)

        left = QVBoxLayout()
        self._workspace_list = QListWidget(self)
        self._workspace_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._workspace_list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        left.addWidget(self._workspace_list)

        buttons_col = QVBoxLayout()
        for label, handler in [
            ("Add…", self._on_add),
            ("Edit…", self._on_edit),
            ("Remove…", self._on_remove),
        ]:
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            buttons_col.addWidget(button)
        buttons_col.addStretch(1)
        left.addLayout(buttons_col)
        outer.addLayout(left, stretch=1)

        details_box = QGroupBox("Details", self)
        details_layout = QVBoxLayout(details_box)
        self._details_label = QLabel(self)
        self._details_label.setWordWrap(True)
        self._details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_label)
        details_layout.addStretch(1)
        outer.addWidget(details_box, stretch=2)

        self._refresh_workspace_list()

    # --- rendering -----------------------------------------------------------

    def _configs(self) -> dict[str, WorkspaceConfig]:
        return self._settings.workspaces.workspaces

    def _selected_name(self) -> str | None:
        item = self._workspace_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_workspace_list(self) -> None:
        previous = self._selected_name()
        self._workspace_list.clear()
        for name in sorted(self._configs()):
            item = QListWidgetItem(name, self._workspace_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._workspace_list.setCurrentItem(item)
        if self._workspace_list.currentItem() is None and self._workspace_list.count():
            self._workspace_list.setCurrentRow(0)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        name = self._selected_name()
        workspace = self._configs().get(name) if name else None
        if workspace is None:
            self._details_label.setText("(no workspace selected)")
            return
        validation = validate_workspace(self._settings, workspace)
        self._details_label.setText("\n".join(_workspace_detail_lines(workspace, validation)))

    # --- add/edit/remove -------------------------------------------------

    def _on_add(self) -> None:
        dialog = WorkspaceFormDialog(settings=self._settings, skills_dir=self._skills_dir, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.result_config()
        if config.name in self._configs():
            QMessageBox.warning(self, "Already Exists", f"A workspace named {config.name!r} already exists.")
            return
        save_workspace(self._settings, config)
        self._refresh_workspace_list()

    def _on_edit(self) -> None:
        name = self._selected_name()
        workspace = self._configs().get(name) if name else None
        if workspace is None:
            return
        dialog = WorkspaceFormDialog(settings=self._settings, skills_dir=self._skills_dir, workspace=workspace, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_config()
        save_workspace(self._settings, updated)
        self._refresh_workspace_list()

    def _on_remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Workspace",
            f"Remove workspace {name!r}? Its source/target folders and files are left untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_workspace(self._settings, name)
        self._refresh_workspace_list()


__all__ = ["WorkspaceFormDialog", "WorkspaceManagementDialog"]
