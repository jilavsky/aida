"""``SettingsDialog`` (PLAN.md Phase 5; U3, planning/improvement_plan_2026-08.md
§3): font size, records folder, log level, provider profiles *view*, and
(U3) the remaining ``AppConfig`` fields that previously required hand-
editing ``config.yaml``: ``default_safety_mode``, the global
``allowed_folders``/``command_allowlist`` lists, and ``max_context_tokens``.
(B15) also ``assistant_name``/``user_context`` — the global identity/user
framing every session's system message opens with, see
``aida.core.context.build_identity_context_block``.

Never calls ``exec()`` from anywhere in ``aida.ui.qt`` itself. It only
touches secret storage for the explicit "Clear key" action; otherwise it is constructed from an
``AppConfig`` snapshot, exposes the edited values back out via plain
getters, and the caller (``main_window``) decides whether/how to persist
them (``save_app_config``) and whether to call ``exec()`` for real or, in a
test, just construct + mutate + read back.
"""

from __future__ import annotations

import dataclasses

from aida.config.secrets import delete_secret
from aida.config.settings import AppConfig, ProviderProfile
from aida.documents.ocr.mistral import SECRET_REF
from aida.ui.qt._qt import (
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
    QMessageBox,
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

        # B15: the model was never told its own name or anything about the
        # user — see aida.core.context.build_identity_context_block. Global
        # (not per-workspace) since it's the same assistant/person
        # regardless of which workspace is active.
        self._assistant_name_edit = QLineEdit(app_config.assistant_name, self)
        form.addRow("Assistant name:", self._assistant_name_edit)

        # Edits the *active user's* context when there is one, falling back
        # to the install-wide text otherwise. The label says which, because
        # a box that silently means two different things depending on a
        # dropdown elsewhere in the window is worse than no box.
        active_user = (app_config.active_user or "").strip()
        self._context_user = active_user
        context_label = f"Personal context ({active_user}):" if active_user else "Personal context:"
        self._user_context_edit = QPlainTextEdit(app_config.context_for_user(active_user), self)
        self._user_context_edit.setPlaceholderText(
            "Optional — a sentence or two the model always sees, e.g. "
            "\"The user is Jan, a beamline scientist at APS.\""
        )
        if active_user:
            self._user_context_edit.setToolTip(
                f"Saved for {active_user!r} only. Users with nothing of their own fall back "
                f"to the text saved with no user selected."
            )
        self._user_context_edit.setMaximumHeight(60)
        form.addRow(context_label, self._user_context_edit)

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

        # Phase 10: how considerate the in-app scheduler is of a user who
        # is mid-something. Both are in seconds but shown in minutes —
        # nobody reasons about "wait 300 seconds before starting a job".
        self._scheduler_quiet_spin = QSpinBox(self)
        self._scheduler_quiet_spin.setRange(0, 120)
        self._scheduler_quiet_spin.setSuffix(" min")
        self._scheduler_quiet_spin.setSpecialValueText("Never wait")
        self._scheduler_quiet_spin.setValue(round(app_config.scheduler_quiet_period_seconds / 60))
        self._scheduler_quiet_spin.setToolTip(
            "How long after your last message or keystroke a scheduled job waits "
            "before starting. A job never starts while a turn is running, whatever this is set to."
        )
        form.addRow("Scheduler: wait for me:", self._scheduler_quiet_spin)

        self._scheduler_max_defer_spin = QSpinBox(self)
        self._scheduler_max_defer_spin.setRange(0, 1440)
        self._scheduler_max_defer_spin.setSuffix(" min")
        self._scheduler_max_defer_spin.setSpecialValueText("Wait indefinitely")
        self._scheduler_max_defer_spin.setValue(round(app_config.scheduler_max_defer_seconds / 60))
        self._scheduler_max_defer_spin.setToolTip(
            "After waiting this long, a job runs even if you are still active — "
            "but still never on top of a turn that is actually running."
        )
        form.addRow("Scheduler: run anyway after:", self._scheduler_max_defer_spin)

        ocr_group = QGroupBox("Document OCR", self)
        ocr_layout = QVBoxLayout(ocr_group)
        key_help = QLabel(
            'Get a free API key from <a href="https://console.mistral.ai/api-keys">'
            "console.mistral.ai/api-keys</a>. The free tier covers roughly 10 documents / "
            "50 MB at a time — enough for occasional use.",
            ocr_group,
        )
        key_help.setOpenExternalLinks(True)
        key_help.setWordWrap(True)
        ocr_layout.addWidget(key_help)
        consent = QLabel(
            "Documents you ask about are uploaded to Mistral. AIDA asks before each one. "
            "Enable it per workspace in Workspaces…",
            ocr_group,
        )
        consent.setWordWrap(True)
        ocr_layout.addWidget(consent)
        key_row = QHBoxLayout()
        self._ocr_api_key_edit = QLineEdit(ocr_group)
        self._ocr_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._ocr_api_key_edit.setPlaceholderText("(unchanged)")
        key_row.addWidget(self._ocr_api_key_edit)
        # "Is my key working?" must be answerable without uploading a
        # document — otherwise the only way to test the configuration is to
        # perform the exact action the user is being careful about. This
        # lists models instead, the cheapest authenticated call there is.
        self._verify_ocr_key_button = QPushButton("Verify key", ocr_group)
        self._verify_ocr_key_button.clicked.connect(self._on_verify_ocr_key)
        key_row.addWidget(self._verify_ocr_key_button)
        self._clear_ocr_key_button = QPushButton("Clear key", ocr_group)
        self._clear_ocr_key_button.clicked.connect(self._on_clear_ocr_key)
        key_row.addWidget(self._clear_ocr_key_button)
        ocr_layout.addLayout(key_row)
        form.addRow(ocr_group)

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

    def _on_verify_ocr_key(self) -> None:
        """Check the key in the box, or the stored one if the box is empty
        (it is never pre-filled, so "empty" means "the saved one")."""
        from aida.config.secrets import get_secret
        from aida.documents.ocr.mistral import MistralOcrError, verify_api_key

        key = self._ocr_api_key_edit.text().strip() or (get_secret(SECRET_REF) or "")
        if not key:
            QMessageBox.warning(self, "Document OCR", "No API key to check — enter one first.")
            return
        try:
            detail = verify_api_key(key)
        except MistralOcrError as exc:
            QMessageBox.warning(self, "Document OCR", f"That key did not work:\n\n{exc}")
            return
        QMessageBox.information(self, "Document OCR", detail)

    def _on_clear_ocr_key(self) -> None:
        delete_secret(SECRET_REF)
        self._ocr_api_key_edit.clear()

    # --- edited values ---------------------------------------------------

    def font_size(self) -> int:
        return self._font_size_spin.value()

    def assistant_name(self) -> str:
        text = self._assistant_name_edit.text().strip()
        return text or self._original.assistant_name

    def user_context(self) -> str:
        """The install-wide text — unchanged when a specific user is being
        edited, since their text belongs in ``user_contexts`` instead."""
        if self._context_user:
            return self._original.user_context
        return self._user_context_edit.toPlainText().strip()

    def user_contexts(self) -> dict[str, str]:
        """``user_contexts`` with this dialog's edit applied. Clearing the
        box removes the entry rather than storing an empty string, so the
        user falls back to the shared text — "no personal context" and
        "an empty personal context" should not be different states."""
        contexts = dict(self._original.user_contexts)
        if not self._context_user:
            return contexts
        edited = self._user_context_edit.toPlainText().strip()
        if edited:
            contexts[self._context_user] = edited
        else:
            contexts.pop(self._context_user, None)
        return contexts

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

    def scheduler_quiet_period_seconds(self) -> int:
        return self._scheduler_quiet_spin.value() * 60

    def scheduler_max_defer_seconds(self) -> int:
        return self._scheduler_max_defer_spin.value() * 60

    def ocr_api_key(self) -> str:
        return self._ocr_api_key_edit.text().strip()

    def updated_app_config(self) -> AppConfig:
        """A copy of the ``AppConfig`` this dialog was opened with, with
        this dialog's edited fields applied — window geometry and every
        other field the dialog doesn't expose pass through unchanged."""
        return dataclasses.replace(
            self._original,
            font_size=self.font_size(),
            assistant_name=self.assistant_name(),
            user_context=self.user_context(),
            user_contexts=self.user_contexts(),
            records_dir=self.records_dir(),
            scratch_dir=self.scratch_dir(),
            log_level=self.log_level(),
            max_agent_iterations=self.max_agent_iterations(),
            default_safety_mode=self.default_safety_mode(),
            allowed_folders=self.allowed_folders(),
            command_allowlist=self.command_allowlist(),
            max_context_tokens=self.max_context_tokens(),
            scheduler_quiet_period_seconds=self.scheduler_quiet_period_seconds(),
            scheduler_max_defer_seconds=self.scheduler_max_defer_seconds(),
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
