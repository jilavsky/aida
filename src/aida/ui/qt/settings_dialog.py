"""``SettingsDialog`` (PLAN.md Phase 5): "Settings dialog v1: font size,
records folder, log level, provider profiles *view* (editing via config
file is acceptable this phase)".

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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


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

        self._log_level_combo = QComboBox(self)
        self._log_level_combo.addItems(LOG_LEVELS)
        index = self._log_level_combo.findText(app_config.log_level)
        if index >= 0:
            self._log_level_combo.setCurrentIndex(index)
        form.addRow("Log level:", self._log_level_combo)

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

    # --- edited values ---------------------------------------------------

    def font_size(self) -> int:
        return self._font_size_spin.value()

    def records_dir(self) -> str | None:
        text = self._records_dir_edit.text().strip()
        return text or None

    def log_level(self) -> str:
        return self._log_level_combo.currentText()

    def updated_app_config(self) -> AppConfig:
        """A copy of the ``AppConfig`` this dialog was opened with, with
        this dialog's edited fields applied — window geometry and every
        other field the dialog doesn't expose pass through unchanged."""
        return dataclasses.replace(
            self._original,
            font_size=self.font_size(),
            records_dir=self.records_dir(),
            log_level=self.log_level(),
        )


def _profile_rows(profiles: dict[str, ProviderProfile]) -> list[str]:
    return [f"{name}  ({profile.kind}, model={profile.model})" for name, profile in sorted(profiles.items())]


__all__ = ["SettingsDialog"]
