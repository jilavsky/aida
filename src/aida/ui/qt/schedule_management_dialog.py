"""``ScheduleManagementDialog`` (Phase 10,
planning/phase10_scheduling_design.md §6): add/edit/enable/disable/remove
schedules (``schedules.yaml``) and fire one right now, entirely from the
GUI.

Structural precedent: mirrors ``aida.ui.qt.workspace_management_dialog`` /
``mcp_management_dialog`` (list left; Add/Edit/Remove-style buttons; a
details pane; persisted immediately via ``save_schedules_config``, no
deferred "Save" step). "Run Now" and the list's live "last run"/"running"
state go through ``aida.ui.qt.scheduler_bridge.SchedulerBridge`` — the same
``run_started``/``run_finished`` signals the app-wide failure indicator
(``MainWindow._on_schedule_run_finished``) listens to — so a run started
from this dialog is indistinguishable, to every other listener, from one
the scheduler fired on its own.
"""

from __future__ import annotations

import contextlib

from aida.config.settings import (
    ScheduleEntry,
    Settings,
    list_workflow_names,
    save_schedules_config,
)
from aida.core.scheduling import ScheduleConfigError, parse_schedule_timing
from aida.persistence.store import ScheduleRunStore
from aida.ui.qt._qt import (
    QAbstractItemView,
    QCheckBox,
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

#: Deliberately a QComboBox choice, not a pair of QRadioButtons — every
#: other Qt widget type this dialog needs is already re-exported from
#: ``aida.ui.qt._qt``; a radio-button group would be the first caller and
#: the first new entry in that shim just for this one field.
AT_LABEL = "At a daily time"
EVERY_LABEL = "Every interval"


def _last_run_summary(name: str) -> str:
    store = ScheduleRunStore()
    try:
        last = store.last_run(name)
    finally:
        store.close()
    if last is None:
        return "never run"
    detail = f"{last.status} at {last.fired_at}"
    if last.error:
        detail += f" — {last.error}"
    return detail


# --- Add/Edit schedule sub-dialog --------------------------------------------


class ScheduleFormDialog(QDialog):
    """Add (``entry=None``) or edit (``entry`` given) one ``ScheduleEntry``."""

    def __init__(
        self,
        *,
        workflow_names: list[str],
        entry: ScheduleEntry | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = entry is not None
        self.setWindowTitle("Edit Schedule" if self._is_edit else "Add Schedule")
        self.resize(480, 440)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(entry.name if entry else "", self)
        self._name_edit.setReadOnly(self._is_edit)  # name is the identity; not renameable in-place
        form.addRow("Name:", self._name_edit)

        self._workflow_combo = QComboBox(self)
        self._workflow_combo.addItems(sorted(workflow_names))
        if entry and entry.workflow:
            index = self._workflow_combo.findText(entry.workflow)
            if index >= 0:
                self._workflow_combo.setCurrentIndex(index)
            else:
                # A workflow the schedule references may have been deleted
                # since this schedule was created — show it anyway rather
                # than silently swapping in whatever sorts first.
                self._workflow_combo.addItem(entry.workflow)
                self._workflow_combo.setCurrentText(entry.workflow)
        form.addRow("Workflow:", self._workflow_combo)

        self._timing_kind_combo = QComboBox(self)
        self._timing_kind_combo.addItems([AT_LABEL, EVERY_LABEL])
        self._timing_value_edit = QLineEdit(self)
        if entry and entry.every:
            self._timing_kind_combo.setCurrentText(EVERY_LABEL)
            self._timing_value_edit.setText(entry.every)
        else:
            self._timing_kind_combo.setCurrentText(AT_LABEL)
            self._timing_value_edit.setText(entry.at if entry else "")
        self._timing_kind_combo.currentTextChanged.connect(self._on_timing_kind_changed)
        self._on_timing_kind_changed(self._timing_kind_combo.currentText())
        form.addRow("Trigger:", self._timing_kind_combo)
        form.addRow("When:", self._timing_value_edit)

        self._vars_edit = QPlainTextEdit(self)
        self._vars_edit.setPlaceholderText("One key=value per line")
        if entry:
            self._vars_edit.setPlainText("\n".join(f"{k}={v}" for k, v in entry.vars.items()))
        self._vars_edit.setMaximumHeight(80)
        form.addRow("Vars:", self._vars_edit)

        self._preapproved_edit = QPlainTextEdit(
            "\n".join(entry.preapproved_tools) if entry else "", self
        )
        self._preapproved_edit.setPlaceholderText(
            "One namespaced tool per line, e.g. pyirena__reduce_scan"
        )
        self._preapproved_edit.setMaximumHeight(60)
        form.addRow("Preapproved tools:", self._preapproved_edit)

        self._yes_in_allowed_checkbox = QCheckBox(
            "Auto-approve writes/deletes inside the workflow's own allowed folders", self
        )
        self._yes_in_allowed_checkbox.setChecked(entry.yes_in_allowed if entry else False)
        form.addRow("Unattended writes:", self._yes_in_allowed_checkbox)

        self._enabled_checkbox = QCheckBox("Enabled", self)
        self._enabled_checkbox.setChecked(entry.enabled if entry else True)
        form.addRow("", self._enabled_checkbox)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_timing_kind_changed(self, label: str) -> None:
        self._timing_value_edit.setPlaceholderText("07:00" if label == AT_LABEL else "4h")

    def _timing_kwargs(self) -> tuple[str | None, str | None]:
        value = self._timing_value_edit.text().strip() or None
        if self._timing_kind_combo.currentText() == AT_LABEL:
            return value, None
        return None, value

    def _parsed_vars(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_line in self._vars_edit.toPlainText().splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
        return result

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A schedule needs a name.")
            return
        if not self._workflow_combo.currentText().strip():
            QMessageBox.warning(self, "Workflow Required", "Pick a stored workflow.")
            return
        at, every = self._timing_kwargs()
        try:
            parse_schedule_timing(at=at, every=every)
        except ScheduleConfigError as exc:
            QMessageBox.warning(self, "Invalid Timing", str(exc))
            return
        self.accept()

    def result_entry(self) -> ScheduleEntry:
        at, every = self._timing_kwargs()
        return ScheduleEntry(
            name=self._name_edit.text().strip(),
            workflow=self._workflow_combo.currentText().strip(),
            at=at,
            every=every,
            vars=self._parsed_vars(),
            preapproved_tools=[
                line.strip()
                for line in self._preapproved_edit.toPlainText().splitlines()
                if line.strip()
            ],
            yes_in_allowed=self._yes_in_allowed_checkbox.isChecked(),
            enabled=self._enabled_checkbox.isChecked(),
        )


# --- Main dialog --------------------------------------------------------


class ScheduleManagementDialog(QDialog):
    def __init__(self, settings: Settings, scheduler_bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Schedules")
        self.resize(680, 460)
        self._settings = settings
        self._scheduler_bridge = scheduler_bridge
        #: Names with a run currently in flight (started, not yet
        #: finished) — purely a display nicety so "Run Now" gives visible
        #: feedback immediately rather than only once the run completes.
        self._running_names: set[str] = set()

        outer = QHBoxLayout(self)

        left = QVBoxLayout()
        self._schedule_list = QListWidget(self)
        self._schedule_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._schedule_list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        left.addWidget(self._schedule_list)

        buttons_col = QVBoxLayout()
        for label, handler in [
            ("Add…", self._on_add),
            ("Edit…", self._on_edit),
            ("Enable", self._on_enable),
            ("Disable", self._on_disable),
            ("Remove…", self._on_remove),
            ("Run Now", self._on_run_now),
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

        if self._scheduler_bridge is not None:
            self._scheduler_bridge.run_started.connect(self._on_run_started)
            self._scheduler_bridge.run_finished.connect(self._on_run_finished)

        self._refresh_schedule_list()

    def done(self, result: int) -> None:
        """Disconnect from ``self._scheduler_bridge`` before closing — same
        leaked-instance bug class ``McpManagementDialog.done`` guards
        against: ``SchedulerBridge`` outlives every dialog opened on top of
        it, so a dialog that never disconnects stays subscribed forever."""
        if self._scheduler_bridge is not None:
            for signal, slot in (
                (self._scheduler_bridge.run_started, self._on_run_started),
                (self._scheduler_bridge.run_finished, self._on_run_finished),
            ):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(slot)
        super().done(result)

    # --- rendering -----------------------------------------------------------

    def _configs(self) -> dict[str, ScheduleEntry]:
        return self._settings.schedules.schedules

    def _selected_name(self) -> str | None:
        item = self._schedule_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_schedule_list(self) -> None:
        previous = self._selected_name()
        self._schedule_list.clear()
        for name in sorted(self._configs()):
            entry = self._configs()[name]
            timing = f"at {entry.at}" if entry.at else f"every {entry.every}"
            if name in self._running_names:
                state = "running"
            else:
                state = "enabled" if entry.enabled else "disabled"
            item = QListWidgetItem(f"{name}  [{state}]  {timing}", self._schedule_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._schedule_list.setCurrentItem(item)
        if self._schedule_list.currentItem() is None and self._schedule_list.count():
            self._schedule_list.setCurrentRow(0)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        name = self._selected_name()
        entry = self._configs().get(name) if name else None
        if entry is None:
            self._details_label.setText("(no schedule selected)")
            return
        lines = [
            f"name: {entry.name}",
            f"workflow: {entry.workflow}",
            f"at: {entry.at or '(none)'}",
            f"every: {entry.every or '(none)'}",
            f"trigger: {entry.trigger}",
            f"enabled: {entry.enabled}",
            f"vars: {entry.vars or '(none)'}",
            f"preapproved_tools: {', '.join(entry.preapproved_tools) or '(none)'}",
            f"yes_in_allowed: {entry.yes_in_allowed}",
            "",
            f"status: {'running now' if name in self._running_names else _last_run_summary(name)}",
        ]
        self._details_label.setText("\n".join(lines))

    # --- add/edit/enable/disable/remove --------------------------------------

    def _on_add(self) -> None:
        dialog = ScheduleFormDialog(workflow_names=list_workflow_names(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dialog.result_entry()
        if entry.name in self._configs():
            QMessageBox.warning(
                self, "Already Exists", f"A schedule named {entry.name!r} already exists."
            )
            return
        self._configs()[entry.name] = entry
        save_schedules_config(self._settings.schedules)
        self._refresh_schedule_list()

    def _on_edit(self) -> None:
        name = self._selected_name()
        entry = self._configs().get(name) if name else None
        if entry is None:
            return
        dialog = ScheduleFormDialog(workflow_names=list_workflow_names(), entry=entry, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_entry()
        self._configs()[updated.name] = updated
        save_schedules_config(self._settings.schedules)
        self._refresh_schedule_list()

    def _set_enabled(self, *, enabled: bool) -> None:
        name = self._selected_name()
        entry = self._configs().get(name) if name else None
        if entry is None:
            return
        entry.enabled = enabled
        save_schedules_config(self._settings.schedules)
        self._refresh_schedule_list()

    def _on_enable(self) -> None:
        self._set_enabled(enabled=True)

    def _on_disable(self) -> None:
        self._set_enabled(enabled=False)

    def _on_remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Schedule",
            f"Remove schedule {name!r}? Its run history is left in place.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._configs()[name]
        save_schedules_config(self._settings.schedules)
        self._refresh_schedule_list()

    # --- run now (SchedulerBridge) -------------------------------------------

    def _on_run_now(self) -> None:
        name = self._selected_name()
        entry = self._configs().get(name) if name else None
        if entry is None:
            return
        if self._scheduler_bridge is None:
            QMessageBox.warning(
                self, "Scheduler Unavailable", "The in-app scheduler isn't running."
            )
            return
        self._scheduler_bridge.run_now(name, entry, self._settings)

    def _on_run_started(self, name: str) -> None:
        self._running_names.add(name)
        if name in self._configs():
            self._refresh_schedule_list()

    def _on_run_finished(self, name: str, _ok: bool, _conversation_id: str, _error: str) -> None:
        self._running_names.discard(name)
        if name in self._configs():
            self._refresh_schedule_list()


__all__ = ["ScheduleFormDialog", "ScheduleManagementDialog"]
