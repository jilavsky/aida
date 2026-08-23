"""``SettingsDialog`` (PLAN.md Phase 5; U3, planning/improvement_plan_2026-08.md
§3): font size, records folder, log level, provider profiles *view*, and
(U3) the remaining ``AppConfig`` fields that previously required hand-
editing ``config.yaml``: ``default_safety_mode``, the global
``allowed_folders``/``command_allowlist`` lists, and ``max_context_tokens``.

Never calls ``exec()`` from anywhere in ``aida.ui.qt`` itself and never
touches ``aida.config`` I/O directly — it's constructed from an
``AppConfig`` snapshot, exposes the edited values back out via plain
getters, and the caller (``main_window``) decides whether/how to persist
them (``save_app_config``) and whether to call ``exec()`` for real or, in a
test, just construct + mutate + read back.
"""

from __future__ import annotations

import dataclasses

from aida.config.settings import AppConfig, ProviderProfile
from aida.ui.qt._qt import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
SAFETY_MODES = ["confirm", "relaxed"]


class SettingsDialog(QDialog):
    def __init__(
        self,
        app_config: AppConfig,
        profiles: dict[str, ProviderProfile] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._original = app_config

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._font_size_spin = QSpinBox(self)
        self._font_size_spin.setRange(6, 48)
        self._font_size_spin.setValue(app_config.font_size)
        form.addRow("Font size:", self._font_size_spin)

        records_row = QHBoxLayout()
        self._records_dir_edit = QLineEdit(app_config.records_dir or "", self)
        records_row.addWidget(self._records_dir_edit)
        browse_button = QPushButton("Browse…", self)
        browse_button.clicked.connect(self._on_browse_records_dir)
        records_row.addWidget(browse_button)
        form.addRow("Records folder:", records_row)

        # Bug report: "Agents seem to be saving temporary files ... in
        # random places." One well-known, overridable scratch folder every
        # MCP server subprocess is launched in — see aida.core.session and
        # aida.mcp.server.
        scratch_row = QHBoxLayout()
        self._scratch_dir_edit = QLineEdit(app_config.scratch_dir or "", self)
        self._scratch_dir_edit.setPlaceholderText("Default: ~/.aida/tmp")
        scratch_row.addWidget(self._scratch_dir_edit)
        scratch_browse_button = QPushButton("Browse…", self)
        scratch_browse_button.clicked.connect(self._on_browse_scratch_dir)
        scratch_row.addWidget(scratch_browse_button)
        form.addRow("Scratchpad folder:", scratch_row)

        self._log_level_combo = QComboBox(self)
        self._log_level_combo.addItems(LOG_LEVELS)
        index = self._log_level_combo.findText(app_config.log_level)
        if index >= 0:
            self._log_level_combo.setCurrentIndex(index)
        form.addRow("Log level:", self._log_level_combo)

        # Bug report: "Give user control on number of iterations, I asked
        # for some really multi step analysis and it stopped after 10."
        # Was a hardcoded AgentLoop constant; now a per-install setting.
        self._max_iterations_spin = QSpinBox(self)
        self._max_iterations_spin.setRange(1, 2000)
        self._max_iterations_spin.setValue(app_config.max_agent_iterations)
        form.addRow("Max tool-call iterations per turn:", self._max_iterations_spin)

        # U3: the remaining AppConfig fields that previously required
        # hand-editing config.yaml.
        self._default_safety_combo = QComboBox(self)
        self._default_safety_combo.addItems(SAFETY_MODES)
        index = self._default_safety_combo.findText(app_config.default_safety_mode)
        if index >= 0:
            self._default_safety_combo.setCurrentIndex(index)
        form.addRow("Default safety mode:", self._default_safety_combo)

        self._allowed_folders_edit = QPlainTextEdit("\n".join(app_config.allowed_folders), self)
        self._allowed_folders_edit.setPlaceholderText(
            "One folder per line — implicitly allowed for every workspace/session,\n"
            "on top of that workspace's own source/target folders"
        )
        self._allowed_folders_edit.setMaximumHeight(80)
        form.addRow("Global allowed folders:", self._allowed_folders_edit)

        self._command_allowlist_edit = QPlainTextEdit("\n".join(app_config.command_allowlist), self)
        self._command_allowlist_edit.setPlaceholderText(
            "One allowed command pattern per line — union'd with each workspace's own"
        )
        self._command_allowlist_edit.setMaximumHeight(80)
        form.addRow("Global command allowlist:", self._command_allowlist_edit)

        self._max_context_tokens_spin = QSpinBox(self)
        self._max_context_tokens_spin.setRange(0, 2_000_000)
        self._max_context_tokens_spin.setSingleStep(1000)
        self._max_context_tokens_spin.setSpecialValueText("Disabled (no trimming)")
        self._max_context_tokens_spin.setValue(app_config.max_context_tokens)
        form.addRow("Max context tokens:", self._max_context_tokens_spin)

        layout.addLayout(form)

        self._profiles_list = QListWidget(self)
        self._profiles_list.addItems(_profile_rows(profiles or {}))
        layout.addWidget(self._profiles_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse_records_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Records Folder", self._records_dir_edit.text())
        if folder:
            self._records_dir_edit.setText(folder)

    def _on_browse_scratch_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Scratchpad Folder", self._scratch_dir_edit.text())
        if folder:
            self._scratch_dir_edit.setText(folder)

    # --- edited values ---------------------------------------------------

    def font_size(self) -> int:
        return self._font_size_spin.value()

    def records_dir(self) -> str | None:
        text = self._records_dir_edit.text().strip()
        return text or None

    def scratch_dir(self) -> str | None:
        text = self._scratch_dir_edit.text().strip()
        return text or None

    def log_level(self) -> str:
        return self._log_level_combo.currentText()

    def max_agent_iterations(self) -> int:
        return self._max_iterations_spin.value()

    def default_safety_mode(self) -> str:
        return self._default_safety_combo.currentText()

    def allowed_folders(self) -> list[str]:
        return [line.strip() for line in self._allowed_folders_edit.toPlainText().splitlines() if line.strip()]

    def command_allowlist(self) -> list[str]:
        return [line.strip() for line in self._command_allowlist_edit.toPlainText().splitlines() if line.strip()]

    def max_context_tokens(self) -> int:
        return self._max_context_tokens_spin.value()

    def updated_app_config(self) -> AppConfig:
        """A copy of the ``AppConfig`` this dialog was opened with, with
        this dialog's edited fields applied — window geometry and every
        other field the dialog doesn't expose pass through unchanged."""
        return dataclasses.replace(
            self._original,
            font_size=self.font_size(),
            records_dir=self.records_dir(),
            scratch_dir=self.scratch_dir(),
            log_level=self.log_level(),
            max_agent_iterations=self.max_agent_iterations(),
            default_safety_mode=self.default_safety_mode(),
            allowed_folders=self.allowed_folders(),
            command_allowlist=self.command_allowlist(),
            max_context_tokens=self.max_context_tokens(),
        )


def _profile_rows(profiles: dict[str, ProviderProfile]) -> list[str]:
    # U7 paper cut: "capability_notes is stored but shown nowhere" — appended
    # here when set, e.g. "small local model — prefer lean MCP groups".
    rows = []
    for name, profile in sorted(profiles.items()):
        row = f"{name}  ({profile.kind}, model={profile.model})"
        if profile.capability_notes:
            row += f" — {profile.capability_notes}"
        rows.append(row)
    return rows


__all__ = ["SettingsDialog"]
