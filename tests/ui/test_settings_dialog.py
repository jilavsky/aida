"""Tests for aida.ui.qt.settings_dialog.SettingsDialog — never calls
exec(); every test constructs the dialog, mutates widget state, and reads
values back directly, per this module's own docstring."""

from __future__ import annotations

from aida.config.settings import AppConfig, ProviderProfile
from aida.ui.qt.settings_dialog import SettingsDialog


def test_dialog_seeds_fields_from_app_config(qapp):
    cfg = AppConfig(font_size=14, records_dir="/data/records", log_level="DEBUG")
    dialog = SettingsDialog(cfg)
    assert dialog.font_size() == 14
    assert dialog.records_dir() == "/data/records"
    assert dialog.log_level() == "DEBUG"


def test_editing_fields_and_reading_back(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._font_size_spin.setValue(20)
    dialog._records_dir_edit.setText("/new/records")
    dialog._log_level_combo.setCurrentText("ERROR")

    assert dialog.font_size() == 20
    assert dialog.records_dir() == "/new/records"
    assert dialog.log_level() == "ERROR"


def test_blank_records_dir_returns_none(qapp):
    cfg = AppConfig(records_dir="/data")
    dialog = SettingsDialog(cfg)
    dialog._records_dir_edit.setText("   ")
    assert dialog.records_dir() is None


def test_updated_app_config_preserves_unedited_fields(qapp):
    cfg = AppConfig(theme="dark", window_width=900, default_safety_mode="relaxed")
    dialog = SettingsDialog(cfg)
    dialog._font_size_spin.setValue(18)

    updated = dialog.updated_app_config()
    assert updated.font_size == 18
    assert updated.theme == "dark"
    assert updated.window_width == 900
    assert updated.default_safety_mode == "relaxed"
    # original untouched
    assert cfg.font_size != 18


def test_browse_button_sets_records_dir(qapp, monkeypatch, tmp_path):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    monkeypatch.setattr(
        "aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory", lambda *a, **kw: str(tmp_path)
    )
    dialog._on_browse_records_dir()
    assert dialog.records_dir() == str(tmp_path)


def test_browse_cancelled_leaves_records_dir_unchanged(qapp, monkeypatch):
    cfg = AppConfig(records_dir="/keep/me")
    dialog = SettingsDialog(cfg)
    monkeypatch.setattr("aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory", lambda *a, **kw: "")
    dialog._on_browse_records_dir()
    assert dialog.records_dir() == "/keep/me"


def test_profiles_shown_read_only(qapp):
    profiles = {
        "argo-claude": ProviderProfile(name="argo-claude", kind="anthropic", model="claude-x"),
        "local": ProviderProfile(name="local", kind="openai_compat", model="llama"),
    }
    dialog = SettingsDialog(AppConfig(), profiles)
    items = [dialog._profiles_list.item(i).text() for i in range(dialog._profiles_list.count())]
    assert any("argo-claude" in text and "anthropic" in text for text in items)
    assert any("local" in text and "llama" in text for text in items)


def test_no_profiles_gives_empty_list(qapp):
    dialog = SettingsDialog(AppConfig())
    assert dialog._profiles_list.count() == 0
