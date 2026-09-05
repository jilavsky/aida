"""``WorkflowManagementDialog`` (Phase 10,
planning/phase10_scheduling_design.md §4): create/edit/remove stored named
workflows (``~/.aida/workflows/NAME.yaml``) entirely from the GUI —
previously the one Phase 10 config object with no GUI editor at all: a
workflow could only be hand-written, even though ``ScheduleFormDialog``
already assumes one exists to pick from.

Structural precedent: mirrors ``aida.ui.qt.workspace_management_dialog``
(list left; Add/Edit/Remove; a details pane; persisted immediately via
``save_workflow``/``delete_workflow``, no deferred "Save" step). Steps get
their own nested list-and-form editor (``StepFormDialog``) rather than a
single free-text box with an invented delimiter convention (a blank line?
``---``? something else no one would guess without reading the docs) —
one more level of the same list+form pattern this dialog already uses at
the top level, not a new idea.

``MainWindow``'s "Save Conversation as Workflow…" File-menu action
(``_on_save_conversation_as_workflow``) is the other way a ``WorkflowConfig``
gets built: it derives one step per user message in the live conversation
and opens ``WorkflowFormDialog`` pre-filled with ``is_edit=False``, so the
name field stays editable — the draft is not yet a workflow that exists on
disk, even though a ``WorkflowConfig`` object already describes it.
"""

from __future__ import annotations

from aida.config.settings import (
    Settings,
    WorkflowConfig,
    WorkflowStep,
    delete_workflow,
    list_workflow_names,
    load_workflow,
    save_workflow,
)
from aida.mcp.groups import known_group_names
from aida.ui.qt._qt import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    Qt,
    QVBoxLayout,
    QWidget,
)

NO_PROFILE_LABEL = "(workspace default)"
NO_MCP_GROUP_LABEL = "(workspace default)"


def _step_summary(step: WorkflowStep) -> str:
    first_line = step.prompt.strip().splitlines()[0] if step.prompt.strip() else "(empty prompt)"
    suffix = f"  [expect: {', '.join(step.expect_files)}]" if step.expect_files else ""
    return first_line + suffix


# --- Add/Edit one step --------------------------------------------------


class StepFormDialog(QDialog):
    """Add (``step=None``) or edit (``step`` given) one ``WorkflowStep``."""

    def __init__(self, *, step: WorkflowStep | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Step" if step else "Add Step")
        self.resize(440, 320)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._prompt_edit = QPlainTextEdit(step.prompt if step else "", self)
        self._prompt_edit.setPlaceholderText(
            "The prompt this step sends. {placeholder} names are resolved from the "
            "workflow's own vars, or --var overrides at run time."
        )
        form.addRow("Prompt:", self._prompt_edit)

        self._expect_files_edit = QPlainTextEdit("\n".join(step.expect_files) if step else "", self)
        self._expect_files_edit.setPlaceholderText(
            "One glob pattern per line, e.g. *.png (optional)"
        )
        self._expect_files_edit.setMaximumHeight(80)
        form.addRow("Expect files:", self._expect_files_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self._prompt_edit.toPlainText().strip():
            QMessageBox.warning(self, "Prompt Required", "A step needs a prompt.")
            return
        self.accept()

    def result_step(self) -> WorkflowStep:
        return WorkflowStep(
            prompt=self._prompt_edit.toPlainText().strip(),
            expect_files=[
                line.strip()
                for line in self._expect_files_edit.toPlainText().splitlines()
                if line.strip()
            ],
        )


# --- Add/Edit workflow sub-dialog ----------------------------------------


class WorkflowFormDialog(QDialog):
    """Add (``workflow=None``) or edit one ``WorkflowConfig``.

    ``is_edit`` defaults to ``workflow is not None`` but is independently
    overridable: "Save Conversation as Workflow…" passes a pre-filled
    ``WorkflowConfig`` draft that is *not* yet a saved workflow, so its
    name must stay editable even though a workflow object already exists
    to seed the form from.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        workflow: WorkflowConfig | None = None,
        is_edit: bool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = is_edit if is_edit is not None else workflow is not None
        self.setWindowTitle("Edit Workflow" if self._is_edit else "Add Workflow")
        self.resize(560, 600)
        self._steps: list[WorkflowStep] = list(workflow.steps) if workflow else []

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(workflow.name if workflow else "", self)
        self._name_edit.setReadOnly(self._is_edit)  # name is the identity; not renameable in-place
        form.addRow("Name:", self._name_edit)

        self._description_edit = QLineEdit(workflow.description if workflow else "", self)
        form.addRow("Description:", self._description_edit)

        self._workspace_combo = QComboBox(self)
        self._workspace_combo.addItems(sorted(settings.workspaces.workspaces))
        if workflow and workflow.workspace:
            index = self._workspace_combo.findText(workflow.workspace)
            if index >= 0:
                self._workspace_combo.setCurrentIndex(index)
            else:
                self._workspace_combo.addItem(workflow.workspace)
                self._workspace_combo.setCurrentText(workflow.workspace)
        form.addRow("Workspace:", self._workspace_combo)

        self._profile_combo = QComboBox(self)
        self._profile_combo.addItem(NO_PROFILE_LABEL)
        self._profile_combo.addItems(sorted(settings.providers.profiles))
        if workflow and workflow.profile:
            index = self._profile_combo.findText(workflow.profile)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)
        form.addRow("Profile:", self._profile_combo)

        self._mcp_group_combo = QComboBox(self)
        self._mcp_group_combo.addItem(NO_MCP_GROUP_LABEL)
        self._mcp_group_combo.addItems(known_group_names(settings.mcp))
        if workflow and workflow.mcp_group:
            index = self._mcp_group_combo.findText(workflow.mcp_group)
            if index >= 0:
                self._mcp_group_combo.setCurrentIndex(index)
        form.addRow("MCP group:", self._mcp_group_combo)

        self._vars_edit = QPlainTextEdit(self)
        self._vars_edit.setPlaceholderText(
            "One key=value per line — defaults for {placeholder} names in steps"
        )
        if workflow:
            self._vars_edit.setPlainText("\n".join(f"{k}={v}" for k, v in workflow.vars.items()))
        self._vars_edit.setMaximumHeight(70)
        form.addRow("Vars:", self._vars_edit)

        self._preapproved_edit = QPlainTextEdit(
            "\n".join(workflow.preapproved_tools) if workflow else "", self
        )
        self._preapproved_edit.setPlaceholderText(
            "One namespaced tool per line, e.g. pyirena__reduce_scan"
        )
        self._preapproved_edit.setMaximumHeight(60)
        form.addRow("Preapproved tools:", self._preapproved_edit)

        layout.addLayout(form)

        layout.addWidget(QLabel("Steps (run in order, one shared session):", self))
        steps_row = QHBoxLayout()
        self._steps_list = QListWidget(self)
        self._steps_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        steps_row.addWidget(self._steps_list, stretch=1)

        steps_buttons = QVBoxLayout()
        for label, handler in [
            ("Add…", self._on_add_step),
            ("Edit…", self._on_edit_step),
            ("Remove", self._on_remove_step),
            ("Move Up", self._on_move_step_up),
            ("Move Down", self._on_move_step_down),
        ]:
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            steps_buttons.addWidget(button)
        steps_buttons.addStretch(1)
        steps_row.addLayout(steps_buttons)
        layout.addLayout(steps_row)

        self._refresh_steps_list()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --- steps sub-list ----------------------------------------------------

    def _refresh_steps_list(self) -> None:
        previous_row = self._steps_list.currentRow()
        self._steps_list.clear()
        for step in self._steps:
            QListWidgetItem(_step_summary(step), self._steps_list)
        if 0 <= previous_row < self._steps_list.count():
            self._steps_list.setCurrentRow(previous_row)

    def _on_add_step(self) -> None:
        dialog = StepFormDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._steps.append(dialog.result_step())
        self._refresh_steps_list()
        self._steps_list.setCurrentRow(len(self._steps) - 1)

    def _on_edit_step(self) -> None:
        row = self._steps_list.currentRow()
        if row < 0:
            return
        dialog = StepFormDialog(step=self._steps[row], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._steps[row] = dialog.result_step()
        self._refresh_steps_list()
        self._steps_list.setCurrentRow(row)

    def _on_remove_step(self) -> None:
        row = self._steps_list.currentRow()
        if row < 0:
            return
        del self._steps[row]
        self._refresh_steps_list()

    def _on_move_step_up(self) -> None:
        row = self._steps_list.currentRow()
        if row <= 0:
            return
        self._steps[row - 1], self._steps[row] = self._steps[row], self._steps[row - 1]
        self._refresh_steps_list()
        self._steps_list.setCurrentRow(row - 1)

    def _on_move_step_down(self) -> None:
        row = self._steps_list.currentRow()
        if row < 0 or row >= len(self._steps) - 1:
            return
        self._steps[row + 1], self._steps[row] = self._steps[row], self._steps[row + 1]
        self._refresh_steps_list()
        self._steps_list.setCurrentRow(row + 1)

    # --- accept/result -------------------------------------------------------

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A workflow needs a name.")
            return
        if not self._workspace_combo.currentText().strip():
            QMessageBox.warning(
                self, "Workspace Required", "Pick a workspace for this workflow to run in."
            )
            return
        if not self._steps:
            QMessageBox.warning(self, "No Steps", "Add at least one step.")
            return
        self.accept()

    def _parsed_vars(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_line in self._vars_edit.toPlainText().splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
        return result

    def result_config(self) -> WorkflowConfig:
        profile = self._profile_combo.currentText()
        mcp_group = self._mcp_group_combo.currentText()
        return WorkflowConfig(
            name=self._name_edit.text().strip(),
            description=self._description_edit.text().strip(),
            workspace=self._workspace_combo.currentText().strip(),
            profile=None if profile == NO_PROFILE_LABEL else profile,
            mcp_group=None if mcp_group == NO_MCP_GROUP_LABEL else mcp_group,
            vars=self._parsed_vars(),
            preapproved_tools=[
                line.strip()
                for line in self._preapproved_edit.toPlainText().splitlines()
                if line.strip()
            ],
            steps=list(self._steps),
        )


def _workflow_detail_lines(workflow: WorkflowConfig) -> list[str]:
    lines = [
        f"name: {workflow.name}",
        f"description: {workflow.description or '(none)'}",
        f"workspace: {workflow.workspace or '(none)'}",
        f"profile: {workflow.profile or NO_PROFILE_LABEL}",
        f"mcp_group: {workflow.mcp_group or NO_MCP_GROUP_LABEL}",
        f"vars: {workflow.vars or '(none)'}",
        f"preapproved_tools: {', '.join(workflow.preapproved_tools) or '(none)'}",
        "",
        f"steps ({len(workflow.steps)}):",
    ]
    lines.extend(f"  {i}. {_step_summary(step)}" for i, step in enumerate(workflow.steps))
    return lines


# --- Main dialog --------------------------------------------------------


class WorkflowManagementDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workflows")
        self.resize(680, 460)
        self._settings = settings

        outer = QHBoxLayout(self)

        left = QVBoxLayout()
        self._workflow_list = QListWidget(self)
        self._workflow_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._workflow_list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        left.addWidget(self._workflow_list)

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

        self._refresh_workflow_list()

    # --- rendering -----------------------------------------------------------

    def _names(self) -> list[str]:
        return list_workflow_names()

    def _selected_name(self) -> str | None:
        item = self._workflow_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_workflow_list(self) -> None:
        previous = self._selected_name()
        self._workflow_list.clear()
        for name in sorted(self._names()):
            item = QListWidgetItem(name, self._workflow_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._workflow_list.setCurrentItem(item)
        if self._workflow_list.currentItem() is None and self._workflow_list.count():
            self._workflow_list.setCurrentRow(0)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        name = self._selected_name()
        if name is None:
            self._details_label.setText("(no workflow selected)")
            return
        try:
            workflow = load_workflow(name)
        except FileNotFoundError:
            self._details_label.setText("(workflow file no longer exists)")
            return
        self._details_label.setText("\n".join(_workflow_detail_lines(workflow)))

    # --- add/edit/remove -------------------------------------------------

    def _on_add(self) -> None:
        dialog = WorkflowFormDialog(settings=self._settings, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.result_config()
        if config.name in self._names():
            QMessageBox.warning(
                self, "Already Exists", f"A workflow named {config.name!r} already exists."
            )
            return
        save_workflow(config)
        self._refresh_workflow_list()

    def _on_edit(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        try:
            workflow = load_workflow(name)
        except FileNotFoundError:
            return
        dialog = WorkflowFormDialog(settings=self._settings, workflow=workflow, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        save_workflow(dialog.result_config())
        self._refresh_workflow_list()

    def _on_remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Workflow",
            f"Remove workflow {name!r}? Any schedule still referencing it will fail at its next run.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_workflow(name)
        self._refresh_workflow_list()


__all__ = ["StepFormDialog", "WorkflowFormDialog", "WorkflowManagementDialog"]
