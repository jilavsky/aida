"""``ProfilesDialog`` (U2, planning/improvement_plan_2026-08.md §3): add/
edit/remove provider profiles (``providers.yaml``'s ``profiles:``) and
embedding profiles (``embedding_profiles:``) entirely from the GUI — the
config objects every session, workspace, and knowledge base ultimately
depends on, previously only editable by hand.

Structural precedent: mirrors ``aida.ui.qt.mcp_management_dialog`` and
``aida.ui.qt.knowledge_management_dialog`` throughout — config CRUD is
persisted the moment it happens (``save_providers_config``, no deferred
"Save" step), list-left/details-right layout, and a live action ("Test")
that goes through ``ChatBridge`` so a real network ping never blocks the Qt
thread. Two independent list panels (provider profiles / embedding
profiles) live as tabs in one dialog rather than two separate dialogs,
since they're edited from the same file and a user setting up a knowledge
base needs both in the same sitting.

**Secrets never touch this dialog's model or ``providers.yaml``.** A
profile's "Secret value" field writes straight to the OS keychain via
``aida.config.secrets.set_secret`` on Save and is never read back into the
field — an existing secret shows as a blank, placeholder-only field, exactly
like every other "change your password" form. Leaving it blank on Edit
keeps whatever is already stored.
"""

from __future__ import annotations

import contextlib

from aida.config.secrets import set_secret
from aida.config.settings import (
    EmbeddingProfile,
    ProviderProfile,
    Settings,
    save_providers_config,
)
from aida.ui.qt._qt import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    Qt,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

PROVIDER_KINDS = ["openai_compat", "anthropic"]
EMBEDDING_KINDS = ["openai_compat"]


class _OptionalNumberRow(QWidget):
    """One "[x] Override:  [spin box]" row for an optional numeric
    ``ProviderProfile`` field (``max_tokens``/``temperature``/
    ``usd_per_m_input``/``usd_per_m_output``) — unchecked means ``None``
    ("use the built-in default"), matching that field's own meaning
    (see ``ProviderProfile``'s docstring)."""

    def __init__(
        self,
        *,
        initial: float | int | None,
        minimum: float,
        maximum: float,
        decimals: int = 0,
        step: float = 1.0,
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._checkbox = QCheckBox("Override:", self)
        layout.addWidget(self._checkbox)
        self._spin = QDoubleSpinBox(self)
        self._spin.setRange(minimum, maximum)
        self._spin.setDecimals(decimals)
        self._spin.setSingleStep(step)
        if suffix:
            self._spin.setSuffix(suffix)
        layout.addWidget(self._spin, stretch=1)
        self._checkbox.toggled.connect(self._spin.setEnabled)

        if initial is None:
            self._checkbox.setChecked(False)
            self._spin.setEnabled(False)
        else:
            self._checkbox.setChecked(True)
            self._spin.setValue(float(initial))

    def value(self) -> float | None:
        return self._spin.value() if self._checkbox.isChecked() else None


# --- Add/Edit provider profile sub-dialog ------------------------------------


class ProviderProfileFormDialog(QDialog):
    """Add (``profile=None``) or edit (``profile`` given) one ``ProviderProfile``."""

    def __init__(self, *, profile: ProviderProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_edit = profile is not None
        self.setWindowTitle("Edit Provider Profile" if self._is_edit else "Add Provider Profile")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(profile.name if profile else "", self)
        self._name_edit.setReadOnly(self._is_edit)  # name is the identity; not renameable in-place
        form.addRow("Name:", self._name_edit)

        self._kind_combo = QComboBox(self)
        self._kind_combo.addItems(PROVIDER_KINDS)
        if profile:
            index = self._kind_combo.findText(profile.kind)
            if index >= 0:
                self._kind_combo.setCurrentIndex(index)
        form.addRow("Kind:", self._kind_combo)

        self._base_url_edit = QLineEdit(profile.base_url if profile else "", self)
        self._base_url_edit.setPlaceholderText("(SDK default — e.g. https://api.anthropic.com)")
        form.addRow("Base URL:", self._base_url_edit)

        self._model_edit = QLineEdit(profile.model if profile else "", self)
        form.addRow("Model:", self._model_edit)

        self._secret_ref_edit = QLineEdit(profile.secret_ref if profile and profile.secret_ref else "", self)
        self._secret_ref_edit.setPlaceholderText("(defaults to the profile name)")
        form.addRow("Secret ref:", self._secret_ref_edit)

        self._secret_value_edit = QLineEdit(self)
        self._secret_value_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret_value_edit.setPlaceholderText(
            "(leave blank to keep the existing keychain secret)" if self._is_edit else "API key / ANL username"
        )
        form.addRow("Secret value:", self._secret_value_edit)

        self._capability_notes_edit = QLineEdit(profile.capability_notes if profile else "", self)
        self._capability_notes_edit.setPlaceholderText("e.g. small local model — prefer lean MCP groups")
        form.addRow("Capability notes:", self._capability_notes_edit)

        self._max_tokens_row = _OptionalNumberRow(
            initial=profile.max_tokens if profile else None, minimum=1, maximum=1_000_000, decimals=0, step=256
        )
        form.addRow("Max tokens:", self._max_tokens_row)

        self._temperature_row = _OptionalNumberRow(
            initial=profile.temperature if profile else None, minimum=0.0, maximum=2.0, decimals=2, step=0.1
        )
        form.addRow("Temperature:", self._temperature_row)

        self._usd_input_row = _OptionalNumberRow(
            initial=profile.usd_per_m_input if profile else None,
            minimum=0.0,
            maximum=1000.0,
            decimals=4,
            step=0.1,
            suffix=" $/M tok",
        )
        form.addRow("Input cost:", self._usd_input_row)

        self._usd_output_row = _OptionalNumberRow(
            initial=profile.usd_per_m_output if profile else None,
            minimum=0.0,
            maximum=1000.0,
            decimals=4,
            step=0.1,
            suffix=" $/M tok",
        )
        form.addRow("Output cost:", self._usd_output_row)

        # PLAN.md §1.3: the model's TOTAL context window — distinct from
        # Max tokens above, which caps only the *output*. None falls back
        # to AppConfig.max_context_tokens (Settings dialog), same
        # "unchecked means use the global default" meaning every other row
        # here already has.
        self._context_window_row = _OptionalNumberRow(
            initial=profile.context_window if profile else None,
            minimum=1,
            maximum=10_000_000,
            decimals=0,
            step=1000,
            suffix=" tok",
        )
        form.addRow("Context window:", self._context_window_row)

        self._supports_vision_checkbox = QCheckBox("This model can see attached images", self)
        self._supports_vision_checkbox.setChecked(bool(profile.supports_vision) if profile else False)
        form.addRow("Vision:", self._supports_vision_checkbox)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A profile needs a name.")
            return
        self.accept()

    def secret_value(self) -> str:
        """The typed secret, or ``""`` if the field was left blank (Edit:
        "keep the existing secret"; Add: no secret configured yet)."""
        return self._secret_value_edit.text()

    def result_profile(self) -> ProviderProfile:
        name = self._name_edit.text().strip()
        return ProviderProfile(
            name=name,
            kind=self._kind_combo.currentText(),
            base_url=self._base_url_edit.text().strip() or None,
            model=self._model_edit.text().strip(),
            secret_ref=self._secret_ref_edit.text().strip() or name,
            capability_notes=self._capability_notes_edit.text().strip(),
            max_tokens=(int(v) if (v := self._max_tokens_row.value()) is not None else None),
            temperature=self._temperature_row.value(),
            usd_per_m_input=self._usd_input_row.value(),
            usd_per_m_output=self._usd_output_row.value(),
            supports_vision=self._supports_vision_checkbox.isChecked(),
            context_window=(int(v) if (v := self._context_window_row.value()) is not None else None),
        )


# --- Add/Edit embedding profile sub-dialog -----------------------------------


class EmbeddingProfileFormDialog(QDialog):
    """Add (``profile=None``) or edit (``profile`` given) one ``EmbeddingProfile``."""

    def __init__(self, *, profile: EmbeddingProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_edit = profile is not None
        self.setWindowTitle("Edit Embedding Profile" if self._is_edit else "Add Embedding Profile")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(profile.name if profile else "", self)
        self._name_edit.setReadOnly(self._is_edit)
        form.addRow("Name:", self._name_edit)

        self._kind_combo = QComboBox(self)
        self._kind_combo.addItems(EMBEDDING_KINDS)
        if profile:
            index = self._kind_combo.findText(profile.kind)
            if index >= 0:
                self._kind_combo.setCurrentIndex(index)
        form.addRow("Kind:", self._kind_combo)

        self._base_url_edit = QLineEdit(profile.base_url if profile else "", self)
        self._base_url_edit.setPlaceholderText("(SDK default)")
        form.addRow("Base URL:", self._base_url_edit)

        self._model_edit = QLineEdit(profile.model if profile else "", self)
        form.addRow("Model:", self._model_edit)

        self._secret_ref_edit = QLineEdit(profile.secret_ref if profile and profile.secret_ref else "", self)
        self._secret_ref_edit.setPlaceholderText("(defaults to the profile name)")
        form.addRow("Secret ref:", self._secret_ref_edit)

        self._secret_value_edit = QLineEdit(self)
        self._secret_value_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret_value_edit.setPlaceholderText(
            "(leave blank to keep the existing keychain secret)" if self._is_edit else "API key"
        )
        form.addRow("Secret value:", self._secret_value_edit)

        self._capability_notes_edit = QLineEdit(profile.capability_notes if profile else "", self)
        form.addRow("Capability notes:", self._capability_notes_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A profile needs a name.")
            return
        self.accept()

    def secret_value(self) -> str:
        return self._secret_value_edit.text()

    def result_profile(self) -> EmbeddingProfile:
        name = self._name_edit.text().strip()
        return EmbeddingProfile(
            name=name,
            kind=self._kind_combo.currentText(),
            base_url=self._base_url_edit.text().strip() or None,
            model=self._model_edit.text().strip(),
            secret_ref=self._secret_ref_edit.text().strip() or name,
            capability_notes=self._capability_notes_edit.text().strip(),
        )


def _provider_detail_lines(profile: ProviderProfile) -> list[str]:
    lines = [
        f"name: {profile.name}",
        f"kind: {profile.kind}",
        f"base_url: {profile.base_url or '(SDK default)'}",
        f"model: {profile.model or '(unset)'}",
        f"secret_ref: {profile.secret_ref or '(none)'}",
        f"capability_notes: {profile.capability_notes or '(none)'}",
        f"max_tokens: {profile.max_tokens if profile.max_tokens is not None else '(default)'}",
        f"temperature: {profile.temperature if profile.temperature is not None else '(default)'}",
        f"usd_per_m_input: {profile.usd_per_m_input if profile.usd_per_m_input is not None else '(default)'}",
        f"usd_per_m_output: {profile.usd_per_m_output if profile.usd_per_m_output is not None else '(default)'}",
        f"supports_vision: {profile.supports_vision}",
        f"context_window: {profile.context_window if profile.context_window is not None else '(uses global max_context_tokens)'}",
    ]
    return lines


def _embedding_detail_lines(profile: EmbeddingProfile) -> list[str]:
    return [
        f"name: {profile.name}",
        f"kind: {profile.kind}",
        f"base_url: {profile.base_url or '(SDK default)'}",
        f"model: {profile.model or '(unset)'}",
        f"secret_ref: {profile.secret_ref or '(none)'}",
        f"capability_notes: {profile.capability_notes or '(none)'}",
    ]


# --- Main dialog ---------------------------------------------------------


class ProfilesDialog(QDialog):
    """Two tabs — Provider Profiles, Embedding Profiles — each a self-
    contained list/Add/Edit/Remove/Test panel over one half of
    ``providers.yaml``."""

    def __init__(self, settings: Settings, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Providers")
        self.resize(640, 480)
        self._settings = settings
        self._bridge = bridge

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        # --- Provider profiles tab ---
        provider_tab = QWidget(self)
        provider_outer = QHBoxLayout(provider_tab)
        provider_left = QVBoxLayout()
        self._provider_list = QListWidget(self)
        self._provider_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._provider_list.currentItemChanged.connect(lambda *_: self._refresh_provider_detail())
        provider_left.addWidget(self._provider_list)
        provider_buttons = QVBoxLayout()
        for label, handler in [
            ("Add…", self._on_add_provider),
            ("Edit…", self._on_edit_provider),
            ("Remove…", self._on_remove_provider),
            ("Test", self._on_test_provider),
        ]:
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            provider_buttons.addWidget(button)
        provider_buttons.addStretch(1)
        provider_left.addLayout(provider_buttons)
        provider_outer.addLayout(provider_left, stretch=1)

        provider_details_box = QGroupBox("Details", self)
        provider_details_layout = QVBoxLayout(provider_details_box)
        self._provider_details_label = QLabel(self)
        self._provider_details_label.setWordWrap(True)
        self._provider_details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        provider_details_layout.addWidget(self._provider_details_label)
        provider_details_layout.addStretch(1)
        provider_outer.addWidget(provider_details_box, stretch=2)

        tabs.addTab(provider_tab, "Provider Profiles")

        # --- Embedding profiles tab ---
        embedding_tab = QWidget(self)
        embedding_outer = QHBoxLayout(embedding_tab)
        embedding_left = QVBoxLayout()
        self._embedding_list = QListWidget(self)
        self._embedding_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._embedding_list.currentItemChanged.connect(lambda *_: self._refresh_embedding_detail())
        embedding_left.addWidget(self._embedding_list)
        embedding_buttons = QVBoxLayout()
        for label, handler in [
            ("Add…", self._on_add_embedding),
            ("Edit…", self._on_edit_embedding),
            ("Remove…", self._on_remove_embedding),
            ("Test", self._on_test_embedding),
        ]:
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            embedding_buttons.addWidget(button)
        embedding_buttons.addStretch(1)
        embedding_left.addLayout(embedding_buttons)
        embedding_outer.addLayout(embedding_left, stretch=1)

        embedding_details_box = QGroupBox("Details", self)
        embedding_details_layout = QVBoxLayout(embedding_details_box)
        self._embedding_details_label = QLabel(self)
        self._embedding_details_label.setWordWrap(True)
        self._embedding_details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        embedding_details_layout.addWidget(self._embedding_details_label)
        embedding_details_layout.addStretch(1)
        embedding_outer.addWidget(embedding_details_box, stretch=2)

        tabs.addTab(embedding_tab, "Embedding Profiles")

        close_row = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_row.rejected.connect(self.accept)
        close_row.accepted.connect(self.accept)
        layout.addWidget(close_row)

        if self._bridge is not None:
            self._bridge.profile_validated.connect(self._on_profile_validated)
            self._bridge.embedding_profile_validated.connect(self._on_embedding_profile_validated)

        self._refresh_provider_list()
        self._refresh_embedding_list()

    def done(self, result: int) -> None:
        """Disconnect from ``self._bridge`` before closing — same leaked-
        connection fix as ``McpManagementDialog.done``/
        ``KnowledgeManagementDialog.done`` (see their docstrings)."""
        if self._bridge is not None:
            for signal, slot in (
                (self._bridge.profile_validated, self._on_profile_validated),
                (self._bridge.embedding_profile_validated, self._on_embedding_profile_validated),
            ):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(slot)
        super().done(result)

    # --- provider profiles ---------------------------------------------------

    def _provider_configs(self) -> dict[str, ProviderProfile]:
        return self._settings.providers.profiles

    def _selected_provider_name(self) -> str | None:
        item = self._provider_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_provider_list(self) -> None:
        previous = self._selected_provider_name()
        self._provider_list.clear()
        for name in sorted(self._provider_configs()):
            profile = self._provider_configs()[name]
            item = QListWidgetItem(f"{name}  ({profile.kind}, model={profile.model or 'unset'})", self._provider_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._provider_list.setCurrentItem(item)
        if self._provider_list.currentItem() is None and self._provider_list.count():
            self._provider_list.setCurrentRow(0)
        self._refresh_provider_detail()

    def _refresh_provider_detail(self) -> None:
        name = self._selected_provider_name()
        profile = self._provider_configs().get(name) if name else None
        if profile is None:
            self._provider_details_label.setText("(no profile selected)")
            return
        self._provider_details_label.setText("\n".join(_provider_detail_lines(profile)))

    def _save_providers(self) -> None:
        save_providers_config(self._settings.providers)

    def _on_add_provider(self) -> None:
        dialog = ProviderProfileFormDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.result_profile()
        if profile.name in self._provider_configs():
            QMessageBox.warning(self, "Already Exists", f"A profile named {profile.name!r} already exists.")
            return
        if dialog.secret_value():
            set_secret(profile.secret_ref, dialog.secret_value())
        self._settings.providers.profiles[profile.name] = profile
        self._save_providers()
        self._refresh_provider_list()

    def _on_edit_provider(self) -> None:
        name = self._selected_provider_name()
        profile = self._provider_configs().get(name) if name else None
        if profile is None:
            return
        dialog = ProviderProfileFormDialog(profile=profile, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_profile()
        if dialog.secret_value():
            set_secret(updated.secret_ref, dialog.secret_value())
        self._settings.providers.profiles[name] = updated
        self._save_providers()
        self._refresh_provider_list()

    def _on_remove_provider(self) -> None:
        name = self._selected_provider_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Profile",
            f"Remove provider profile {name!r}? Workspaces/knowledge bases referencing it will need a new one.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._settings.providers.profiles[name]
        self._save_providers()
        self._refresh_provider_list()

    def _on_test_provider(self) -> None:
        name = self._selected_provider_name()
        profile = self._provider_configs().get(name) if name else None
        if profile is not None and self._bridge is not None:
            self._bridge.validate_provider_profile(profile)

    def _on_profile_validated(self, name: str, result) -> None:
        if result.ok:
            QMessageBox.information(self, "Profile OK", f"{name}: {result.detail}")
        else:
            QMessageBox.warning(self, "Profile Not Reachable", f"{name}: {result.detail}")

    # --- embedding profiles ---------------------------------------------------

    def _embedding_configs(self) -> dict[str, EmbeddingProfile]:
        return self._settings.providers.embedding_profiles

    def _selected_embedding_name(self) -> str | None:
        item = self._embedding_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_embedding_list(self) -> None:
        previous = self._selected_embedding_name()
        self._embedding_list.clear()
        for name in sorted(self._embedding_configs()):
            profile = self._embedding_configs()[name]
            item = QListWidgetItem(f"{name}  ({profile.kind}, model={profile.model or 'unset'})", self._embedding_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._embedding_list.setCurrentItem(item)
        if self._embedding_list.currentItem() is None and self._embedding_list.count():
            self._embedding_list.setCurrentRow(0)
        self._refresh_embedding_detail()

    def _refresh_embedding_detail(self) -> None:
        name = self._selected_embedding_name()
        profile = self._embedding_configs().get(name) if name else None
        if profile is None:
            self._embedding_details_label.setText("(no profile selected)")
            return
        self._embedding_details_label.setText("\n".join(_embedding_detail_lines(profile)))

    def _on_add_embedding(self) -> None:
        dialog = EmbeddingProfileFormDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.result_profile()
        if profile.name in self._embedding_configs():
            QMessageBox.warning(self, "Already Exists", f"A profile named {profile.name!r} already exists.")
            return
        if dialog.secret_value():
            set_secret(profile.secret_ref, dialog.secret_value())
        self._settings.providers.embedding_profiles[profile.name] = profile
        self._save_providers()
        self._refresh_embedding_list()

    def _on_edit_embedding(self) -> None:
        name = self._selected_embedding_name()
        profile = self._embedding_configs().get(name) if name else None
        if profile is None:
            return
        dialog = EmbeddingProfileFormDialog(profile=profile, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_profile()
        if dialog.secret_value():
            set_secret(updated.secret_ref, dialog.secret_value())
        self._settings.providers.embedding_profiles[name] = updated
        self._save_providers()
        self._refresh_embedding_list()

    def _on_remove_embedding(self) -> None:
        name = self._selected_embedding_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Profile",
            f"Remove embedding profile {name!r}? Knowledge bases referencing it will stop being able to (re)build.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._settings.providers.embedding_profiles[name]
        self._save_providers()
        self._refresh_embedding_list()

    def _on_test_embedding(self) -> None:
        name = self._selected_embedding_name()
        profile = self._embedding_configs().get(name) if name else None
        if profile is not None and self._bridge is not None:
            self._bridge.validate_embedding_provider_profile(profile)

    def _on_embedding_profile_validated(self, name: str, result) -> None:
        if result.ok:
            QMessageBox.information(self, "Profile OK", f"{name}: {result.detail}")
        else:
            QMessageBox.warning(self, "Profile Not Reachable", f"{name}: {result.detail}")


__all__ = ["EmbeddingProfileFormDialog", "ProfilesDialog", "ProviderProfileFormDialog"]
