"""``SettingsDialog`` (PLAN.md Phase 5; U3, planning/completed/improvement_plan_2026-08.md
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

from aida.config.settings import AppConfig, ProviderProfile
from aida.ui.qt._qt import (
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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
SAFETY_MODES = ["confirm", "relaxed"]
TRANSCRIPT_TOOL_RESULT_MODES = ["off", "summary", "full"]


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
            '"The user is Jan, a beamline scientist at APS."'
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

        # Bug report: a long instrument-control conversation's exported
        # transcript was unreadable as a lab notebook — every raw tool
        # result (EPICS readbacks, JSON dumps) the agent needed showed up
        # verbatim. This only trims the *text* of tool-result messages in
        # the saved .md; any image/file a tool produced is still linked
        # regardless of this setting. See aida.persistence.records.
        self._transcript_tool_results_combo = QComboBox(self)
        self._transcript_tool_results_combo.addItems(TRANSCRIPT_TOOL_RESULT_MODES)
        index = self._transcript_tool_results_combo.findText(app_config.transcript_tool_results)
        if index >= 0:
            self._transcript_tool_results_combo.setCurrentIndex(index)
        self._transcript_tool_results_combo.setToolTip(
            "How much of a tool result's text is written into the exported Markdown "
            "transcript — Off/Summary keep images and saved files linked either way."
        )
        form.addRow("Transcript tool results:", self._transcript_tool_results_combo)

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

        # Bug report: "use AI to rename the chat ... may be keep renaming
        # the chat if the topic changes enough". On by default; this is
        # the opt-out for a metered endpoint or a local model slow enough
        # that the extra round trip at the end of a turn is felt. See
        # aida.core.titling.
        self._auto_title_check = QCheckBox("Name conversations from their content", self)
        self._auto_title_check.setChecked(app_config.auto_title_conversations)
        self._auto_title_check.setToolTip(
            "After the first reply, the model gives the conversation a short name "
            "instead of leaving it called after the first line you typed. "
            "A name you type yourself is never changed."
        )
        form.addRow("Automatic titles:", self._auto_title_check)

        self._auto_title_interval_spin = QSpinBox(self)
        self._auto_title_interval_spin.setRange(1, 100)
        self._auto_title_interval_spin.setSuffix(" turns")
        self._auto_title_interval_spin.setValue(app_config.auto_title_interval_turns)
        self._auto_title_interval_spin.setToolTip(
            "How often an existing automatic title is re-checked, so a conversation "
            "that moves on to another subject stops carrying the old name. "
            "The model is asked to keep the current title unless the subject really changed."
        )
        self._auto_title_interval_spin.setEnabled(app_config.auto_title_conversations)
        self._auto_title_check.toggled.connect(self._auto_title_interval_spin.setEnabled)
        form.addRow("Re-check the title every:", self._auto_title_interval_spin)

        self._auto_continue_check = QCheckBox("Nudge the agent when it stalls mid-task", self)
        self._auto_continue_check.setChecked(app_config.auto_continue_on_stall)
        self._auto_continue_check.setToolTip(
            "When a short reply right after tool work announces a next step "
            '("Writing the report now.") but makes no tool call, AIDA sends one '
            'automatic "continue" message instead of ending the turn. '
            "At most twice per turn; the message is shown in the chat."
        )
        form.addRow("Long tasks:", self._auto_continue_check)

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

        documents_group = QGroupBox("Documents", self)
        # A vertical layout, not a form: a word-wrapped label in a form row is
        # not given its full height and gets clipped.
        documents_layout = QVBoxLayout(documents_group)
        # Explicit line breaks, no word wrap: a wrapped label can be squeezed
        # to one line when the dialog is short; this one keeps its height.
        explain = QLabel(
            "Scanned PDF pages are read by a model with vision: the OCR profile\n"
            "below if set, otherwise the chat model when its profile has vision.\n"
            "A separate remote OCR profile asks before pages are sent.",
            documents_group,
        )
        documents_layout.addWidget(explain)
        self._ocr_backend_check = QCheckBox("Convert scanned PDF pages", documents_group)
        self._ocr_backend_check.setChecked(app_config.ocr_backend != "off")
        documents_layout.addWidget(self._ocr_backend_check)
        self._ocr_profile_combo = QComboBox(documents_group)
        self._ocr_profile_combo.addItem("(use the chat model)", None)
        for name in sorted(profiles or {}):
            self._ocr_profile_combo.addItem(name, name)
        if app_config.ocr_profile:
            index = self._ocr_profile_combo.findData(app_config.ocr_profile)
            if index < 0:  # configured but missing: keep it visible, not silently dropped
                self._ocr_profile_combo.addItem(
                    f"{app_config.ocr_profile} (missing)", app_config.ocr_profile
                )
                index = self._ocr_profile_combo.count() - 1
            self._ocr_profile_combo.setCurrentIndex(index)
        self._ocr_profile_combo.setToolTip(
            "A profile used only for converting pages, for example a small local OCR model. "
            "Test it with: aida documents convert FILE"
        )
        self._ocr_profile_combo.setEnabled(self._ocr_backend_check.isChecked())
        self._ocr_backend_check.toggled.connect(self._ocr_profile_combo.setEnabled)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("OCR profile:", documents_group))
        profile_row.addWidget(self._ocr_profile_combo, stretch=1)
        documents_layout.addLayout(profile_row)
        form.addRow(documents_group)

        layout.addLayout(form)

        self._profiles_list = QListWidget(self)
        self._profiles_list.addItems(_profile_rows(profiles or {}))
        layout.addWidget(self._profiles_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse_records_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Records Folder", self._records_dir_edit.text()
        )
        if folder:
            self._records_dir_edit.setText(folder)

    def _on_browse_scratch_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Scratchpad Folder", self._scratch_dir_edit.text()
        )
        if folder:
            self._scratch_dir_edit.setText(folder)

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

    def transcript_tool_results(self) -> str:
        return self._transcript_tool_results_combo.currentText()

    def allowed_folders(self) -> list[str]:
        return [
            line.strip()
            for line in self._allowed_folders_edit.toPlainText().splitlines()
            if line.strip()
        ]

    def command_allowlist(self) -> list[str]:
        return [
            line.strip()
            for line in self._command_allowlist_edit.toPlainText().splitlines()
            if line.strip()
        ]

    def max_context_tokens(self) -> int:
        return self._max_context_tokens_spin.value()

    def scheduler_quiet_period_seconds(self) -> int:
        return self._scheduler_quiet_spin.value() * 60

    def scheduler_max_defer_seconds(self) -> int:
        return self._scheduler_max_defer_spin.value() * 60

    def auto_title_conversations(self) -> bool:
        return self._auto_title_check.isChecked()

    def auto_title_interval_turns(self) -> int:
        return self._auto_title_interval_spin.value()

    def auto_continue_on_stall(self) -> bool:
        return self._auto_continue_check.isChecked()

    def ocr_backend(self) -> str:
        return "auto" if self._ocr_backend_check.isChecked() else "off"

    def ocr_profile(self) -> str | None:
        return self._ocr_profile_combo.currentData()

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
            transcript_tool_results=self.transcript_tool_results(),
            auto_title_conversations=self.auto_title_conversations(),
            auto_title_interval_turns=self.auto_title_interval_turns(),
            auto_continue_on_stall=self.auto_continue_on_stall(),
            ocr_backend=self.ocr_backend(),
            ocr_profile=self.ocr_profile(),
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
