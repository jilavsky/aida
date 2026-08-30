"""Tests for aida.ui.qt.profiles_dialog.ProfilesDialog (U2) — same "never
calls exec(), construct + mutate + read back" philosophy as
test_settings_dialog.py, plus config-persistence and bridge-signal checks
mirroring test_mcp_management_dialog.py's pattern.
"""

from __future__ import annotations

import keyring
from keyring.backend import KeyringBackend

from aida.config import secrets
from aida.config.settings import (
    EmbeddingProfile,
    ProviderProfile,
    load_providers_config,
    load_settings,
)
from aida.providers.profiles import ProfileValidation
from aida.ui.qt._qt import QMessageBox
from aida.ui.qt.bridge import ChatBridge
from aida.ui.qt.profiles_dialog import (
    EmbeddingProfileFormDialog,
    ProfilesDialog,
    ProviderProfileFormDialog,
)


class _InMemoryKeyring(KeyringBackend):
    """Same in-process fake as tests/test_secrets.py — never touches a real
    OS keychain, reproducible in headless CI."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):  # noqa: D102
        return self._store.get((service, username))

    def set_password(self, service, username, password):  # noqa: D102
        self._store[(service, username)] = password

    def delete_password(self, service, username):  # noqa: D102
        self._store.pop((service, username), None)


def _use_memory_keyring(monkeypatch):
    backend = _InMemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


# --- ProviderProfileFormDialog -----------------------------------------------


def test_provider_profile_form_seeds_fields_when_editing(qapp):
    profile = ProviderProfile(
        name="argo-claude",
        kind="anthropic",
        base_url="https://apps.inside.anl.gov/argoapi/",
        model="claude-sonnet",
        secret_ref="argo-claude",
        capability_notes="cloud model",
        max_tokens=8192,
        temperature=0.3,
        usd_per_m_input=3.0,
        usd_per_m_output=15.0,
        supports_vision=True,
        context_window=200_000,
    )
    dialog = ProviderProfileFormDialog(profile=profile)

    assert dialog._name_edit.isReadOnly()
    assert dialog._name_edit.text() == "argo-claude"
    assert dialog._kind_combo.currentText() == "anthropic"
    assert dialog._base_url_edit.text() == profile.base_url
    assert dialog._model_edit.text() == "claude-sonnet"
    assert dialog._max_tokens_row.value() == 8192
    assert dialog._temperature_row.value() == 0.3
    assert dialog._usd_input_row.value() == 3.0
    assert dialog._usd_output_row.value() == 15.0
    assert dialog._supports_vision_checkbox.isChecked()
    assert dialog._context_window_row.value() == 200_000


def test_max_tokens_and_context_window_tooltips_explain_the_difference(qapp):
    """A user setting max_tokens to their model's full context size (the
    exact real-world mix-up `aida doctor`'s max_tokens_vs_context_window
    check exists for) is the failure this tooltip is meant to head off —
    and it has to actually be visible: the row's checkbox + spin box fill
    its whole paintable area, so the tooltip must reach both children, not
    just sit unreachable on the row's own QWidget (see
    `_OptionalNumberRow.setToolTip`)."""
    dialog = ProviderProfileFormDialog()

    max_tokens_tip = dialog._max_tokens_row.toolTip()
    assert "output" in max_tokens_tip.lower()
    assert max_tokens_tip == dialog._max_tokens_row._checkbox.toolTip()
    assert max_tokens_tip == dialog._max_tokens_row._spin.toolTip()

    context_window_tip = dialog._context_window_row.toolTip()
    assert "total context window" in context_window_tip.lower()
    assert context_window_tip == dialog._context_window_row._checkbox.toolTip()
    assert context_window_tip == dialog._context_window_row._spin.toolTip()

    assert max_tokens_tip != context_window_tip


def test_provider_profile_form_optional_fields_default_unchecked_when_adding(qapp):
    dialog = ProviderProfileFormDialog()
    assert dialog._max_tokens_row.value() is None
    assert dialog._temperature_row.value() is None
    assert dialog._usd_input_row.value() is None
    assert dialog._usd_output_row.value() is None
    assert not dialog._supports_vision_checkbox.isChecked()
    assert dialog._context_window_row.value() is None


def test_provider_profile_form_result_profile_reflects_edited_fields(qapp):
    dialog = ProviderProfileFormDialog()
    dialog._name_edit.setText("local")
    dialog._kind_combo.setCurrentText("openai_compat")
    dialog._model_edit.setText("llama3")
    dialog._max_tokens_row._checkbox.setChecked(True)
    dialog._max_tokens_row._spin.setValue(4096)
    dialog._supports_vision_checkbox.setChecked(True)
    dialog._context_window_row._checkbox.setChecked(True)
    dialog._context_window_row._spin.setValue(128_000)

    profile = dialog.result_profile()
    assert profile.name == "local"
    assert profile.kind == "openai_compat"
    assert profile.model == "llama3"
    assert profile.max_tokens == 4096
    assert profile.temperature is None  # left unchecked -> None, not 0.0
    assert profile.supports_vision is True
    assert profile.context_window == 128_000
    assert profile.secret_ref == "local"  # defaults to the profile name


def test_provider_profile_form_secret_ref_can_be_overridden(qapp):
    dialog = ProviderProfileFormDialog()
    dialog._name_edit.setText("local")
    dialog._secret_ref_edit.setText("custom-ref")
    assert dialog.result_profile().secret_ref == "custom-ref"


def test_provider_profile_form_secret_value_blank_by_default(qapp):
    profile = ProviderProfile(name="argo-claude", kind="anthropic", secret_ref="argo-claude")
    dialog = ProviderProfileFormDialog(profile=profile)
    # An existing secret is never read back into the field — blank means
    # "keep whatever is already in the keychain".
    assert dialog.secret_value() == ""


def test_provider_profile_form_rejects_a_blank_name(qapp, monkeypatch):
    dialog = ProviderProfileFormDialog()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()
    assert warned == [True]


# --- EmbeddingProfileFormDialog -----------------------------------------------


def test_embedding_profile_form_seeds_fields_when_editing(qapp):
    profile = EmbeddingProfile(name="local-embed", kind="openai_compat", model="nomic-embed", base_url="http://x")
    dialog = EmbeddingProfileFormDialog(profile=profile)
    assert dialog._name_edit.isReadOnly()
    assert dialog._model_edit.text() == "nomic-embed"
    assert dialog._base_url_edit.text() == "http://x"


def test_embedding_profile_form_result_profile(qapp):
    dialog = EmbeddingProfileFormDialog()
    dialog._name_edit.setText("local-embed")
    dialog._model_edit.setText("nomic-embed")
    profile = dialog.result_profile()
    assert profile.name == "local-embed"
    assert profile.model == "nomic-embed"
    assert profile.secret_ref == "local-embed"


# --- ProfilesDialog: config CRUD (no bridge needed) --------------------------


def test_dialog_lists_configured_provider_profiles(qapp, aida_home):
    settings = load_settings()
    settings.providers.profiles["argo-claude"] = ProviderProfile(name="argo-claude", kind="anthropic", model="claude-x")
    dialog = ProfilesDialog(settings, None)
    assert dialog._provider_list.count() == 1
    assert "argo-claude" in dialog._provider_list.item(0).text()


def test_dialog_with_no_profiles_is_empty(qapp, aida_home):
    dialog = ProfilesDialog(load_settings(), None)
    assert dialog._provider_list.count() == 0
    assert "no profile selected" in dialog._provider_details_label.text()


def test_add_provider_profile_persists_to_settings_and_disk(qapp, aida_home, monkeypatch):
    _use_memory_keyring(monkeypatch)
    settings = load_settings()
    dialog = ProfilesDialog(settings, None)

    form = ProviderProfileFormDialog()
    form._name_edit.setText("argo-claude")
    form._kind_combo.setCurrentText("anthropic")
    form._model_edit.setText("claude-sonnet")
    monkeypatch.setattr(ProviderProfileFormDialog, "exec", lambda self: 1)  # QDialog.DialogCode.Accepted
    monkeypatch.setattr("aida.ui.qt.profiles_dialog.ProviderProfileFormDialog", lambda **kw: form)

    dialog._on_add_provider()

    assert "argo-claude" in settings.providers.profiles
    reloaded = load_providers_config(aida_home)
    assert "argo-claude" in reloaded.profiles
    assert reloaded.profiles["argo-claude"].model == "claude-sonnet"


def test_add_provider_profile_with_secret_value_writes_to_keychain(qapp, aida_home, monkeypatch):
    _use_memory_keyring(monkeypatch)
    settings = load_settings()
    dialog = ProfilesDialog(settings, None)

    form = ProviderProfileFormDialog()
    form._name_edit.setText("argo-claude")
    form._secret_value_edit.setText("super-secret-key")
    monkeypatch.setattr("aida.ui.qt.profiles_dialog.ProviderProfileFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: 1)

    dialog._on_add_provider()

    assert secrets.get_secret("argo-claude") == "super-secret-key"
    # The secret itself never touches providers.yaml.
    reloaded = load_providers_config(aida_home)
    assert "secret_ref" in reloaded.to_dict()["profiles"]["argo-claude"]
    assert "super-secret-key" not in str(reloaded.to_dict())


def test_add_provider_profile_rejects_a_duplicate_name(qapp, aida_home, monkeypatch):
    settings = load_settings()
    settings.providers.profiles["argo-claude"] = ProviderProfile(name="argo-claude", kind="anthropic")
    dialog = ProfilesDialog(settings, None)

    form = ProviderProfileFormDialog()
    form._name_edit.setText("argo-claude")
    monkeypatch.setattr("aida.ui.qt.profiles_dialog.ProviderProfileFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: 1)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_add_provider()

    assert warned == [True]
    assert len(settings.providers.profiles) == 1  # not overwritten/duplicated


def test_remove_provider_profile_deletes_it(qapp, aida_home, monkeypatch):
    settings = load_settings()
    settings.providers.profiles["argo-claude"] = ProviderProfile(name="argo-claude", kind="anthropic")
    dialog = ProfilesDialog(settings, None)
    dialog._provider_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    dialog._on_remove_provider()

    assert "argo-claude" not in settings.providers.profiles
    assert dialog._provider_list.count() == 0


def test_add_embedding_profile_persists(qapp, aida_home, monkeypatch):
    settings = load_settings()
    dialog = ProfilesDialog(settings, None)

    form = EmbeddingProfileFormDialog()
    form._name_edit.setText("local-embed")
    form._model_edit.setText("nomic-embed")
    monkeypatch.setattr("aida.ui.qt.profiles_dialog.EmbeddingProfileFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: 1)

    dialog._on_add_embedding()

    assert "local-embed" in settings.providers.embedding_profiles
    reloaded = load_providers_config(aida_home)
    assert "local-embed" in reloaded.embedding_profiles


def test_remove_embedding_profile_deletes_it(qapp, aida_home, monkeypatch):
    settings = load_settings()
    settings.providers.embedding_profiles["local-embed"] = EmbeddingProfile(name="local-embed")
    dialog = ProfilesDialog(settings, None)
    dialog._embedding_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    dialog._on_remove_embedding()

    assert "local-embed" not in settings.providers.embedding_profiles


# --- ProfilesDialog: Test button (bridge signal wiring) ----------------------


def test_test_provider_calls_bridge_validate(qapp, loop_thread, aida_home):
    settings = load_settings()
    settings.providers.profiles["argo-claude"] = ProviderProfile(name="argo-claude", kind="anthropic")

    bridge = ChatBridge(loop_thread)
    calls = []
    bridge.validate_provider_profile = lambda profile: calls.append(profile.name)
    dialog = ProfilesDialog(settings, bridge)
    dialog._provider_list.setCurrentRow(0)

    dialog._on_test_provider()

    assert calls == ["argo-claude"]


def test_test_embedding_calls_bridge_validate(qapp, loop_thread, aida_home):
    settings = load_settings()
    settings.providers.embedding_profiles["local-embed"] = EmbeddingProfile(name="local-embed")

    bridge = ChatBridge(loop_thread)
    calls = []
    bridge.validate_embedding_provider_profile = lambda profile: calls.append(profile.name)
    dialog = ProfilesDialog(settings, bridge)
    dialog._embedding_list.setCurrentRow(0)

    dialog._on_test_embedding()

    assert calls == ["local-embed"]


def test_profile_validated_shows_information_dialog_on_success(qapp, aida_home, monkeypatch):
    settings = load_settings()
    settings.providers.profiles["argo-claude"] = ProviderProfile(name="argo-claude", kind="anthropic")
    dialog = ProfilesDialog(settings, None)

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(True))
    dialog._on_profile_validated("argo-claude", ProfileValidation(name="argo-claude", ok=True, detail="reachable"))
    assert shown == [True]


def test_profile_validated_shows_warning_dialog_on_failure(qapp, aida_home, monkeypatch):
    settings = load_settings()
    dialog = ProfilesDialog(settings, None)

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_profile_validated("argo-claude", ProfileValidation(name="argo-claude", ok=False, detail="not reachable"))
    assert warned == [True]
