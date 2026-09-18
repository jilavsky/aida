"""``ExportConversationDialog`` — "Export Conversation As…": a one-off
Markdown+images snapshot of one conversation to a destination the user
picks (e.g. an Obsidian vault folder), independent of the always-on
background transcript ``aida.persistence.recorder.ConversationRecorder``
keeps up to date in the configured Records folder.

Same thin-wrapper shape as ``aida.ui.qt.settings_dialog.SettingsDialog``:
never calls ``exec()`` itself, exposes the edited values back out via plain
getters, and the caller (``main_window``) does the actual
``ConversationRecorder.export_transcript_to`` call.
"""

from __future__ import annotations

from aida.persistence.records import DEFAULT_TOOL_RESULT_MODE, TOOL_RESULT_MODES
from aida.ui.qt._qt import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ExportConversationDialog(QDialog):
    def __init__(
        self,
        *,
        default_destination: str,
        default_tool_result_mode: str = DEFAULT_TOOL_RESULT_MODE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Conversation As…")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        dest_row = QHBoxLayout()
        self._dest_edit = QLineEdit(default_destination, self)
        dest_row.addWidget(self._dest_edit)
        browse_button = QPushButton("Browse…", self)
        browse_button.clicked.connect(self._on_browse)
        dest_row.addWidget(browse_button)
        form.addRow("Destination folder:", dest_row)

        self._tool_results_combo = QComboBox(self)
        self._tool_results_combo.addItems(list(TOOL_RESULT_MODES))
        index = self._tool_results_combo.findText(default_tool_result_mode)
        if index >= 0:
            self._tool_results_combo.setCurrentIndex(index)
        self._tool_results_combo.setToolTip(
            "How much of a tool result's text goes into this snapshot — images and saved "
            "files are linked either way."
        )
        form.addRow("Tool results:", self._tool_results_combo)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Export Destination", self._dest_edit.text()
        )
        if folder:
            self._dest_edit.setText(folder)

    def destination(self) -> str:
        return self._dest_edit.text().strip()

    def tool_result_mode(self) -> str:
        return self._tool_results_combo.currentText()


__all__ = ["ExportConversationDialog"]
