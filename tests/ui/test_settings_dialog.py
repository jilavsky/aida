"""Tests for aida.ui.qt.settings_dialog.SettingsDialog — never calls
exec(); every test constructs the dialog, mutates widget state, and reads
values back directly, per this module's own docstring."""

from __future__ import annotations

from aida.config.settings import AppConfig, ProviderProfile
from aida.ui.qt.settings_dialog import SettingsDialog


def test_dialog_seeds_fields_from_app_config(qapp):
    cfg = AppConfig(font_size=14, records_dir="/data/records", log_level="DEBUG", max_agent_iterations=100)
    dialog = SettingsDialog(cfg)
    assert dialog.font_size() == 14
    assert dialog.records_dir() == "/data/records"
    assert dialog.log_level() == "DEBUG"
    assert dialog.max_agent_iterations() == 100


def test_editing_fields_and_reading_back(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._font_size_spin.setValue(20)
    dialog._records_dir_edit.setText("/new/records")
    dialog._log_level_combo.setCurrentText("ERROR")
    dialog._max_iterations_spin.setValue(500)

    assert dialog.font_size() == 20
    assert dialog.records_dir() == "/new/records"
    assert dialog.log_level() == "ERROR"
    assert dialog.max_agent_iterations() == 500


def test_blank_records_dir_returns_none(qapp):
    cfg = AppConfig(records_dir="/data")
    dialog = SettingsDialog(cfg)
    dialog._records_dir_edit.setText("   ")
    assert dialog.records_dir() is None


def test_updated_app_config_preserves_unedited_fields(qapp):
    cfg = AppConfig(window_width=900, default_safety_mode="relaxed")
    dialog = SettingsDialog(cfg)
    dialog._font_size_spin.setValue(18)

    updated = dialog.updated_app_config()
    assert updated.font_size == 18
    assert updated.window_width == 900
    assert updated.default_safety_mode == "relaxed"
    # original untouched
    assert cfg.font_size != 18


def test_updated_app_config_includes_edited_max_agent_iterations(qapp):
    """Bug report: "Give user control on number of iterations, I asked for
    some really multi step analysis and it stopped after 10.\""""
    cfg = AppConfig(max_agent_iterations=10)
    dialog = SettingsDialog(cfg)
    dialog._max_iterations_spin.setValue(500)

    updated = dialog.updated_app_config()
    assert updated.max_agent_iterations == 500
    assert cfg.max_agent_iterations == 10  # original untouched


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


# --- scratch_dir (bug report: "Agents seem to be saving temporary files
# ... in random places") — mirrors the records_dir tests above exactly.


def test_dialog_seeds_scratch_dir_from_app_config(qapp):
    cfg = AppConfig(scratch_dir="/data/scratch")
    dialog = SettingsDialog(cfg)
    assert dialog.scratch_dir() == "/data/scratch"


def test_editing_scratch_dir_field_and_reading_back(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._scratch_dir_edit.setText("/new/scratch")
    assert dialog.scratch_dir() == "/new/scratch"


def test_blank_scratch_dir_returns_none(qapp):
    cfg = AppConfig(scratch_dir="/data")
    dialog = SettingsDialog(cfg)
    dialog._scratch_dir_edit.setText("   ")
    assert dialog.scratch_dir() is None


def test_updated_app_config_includes_edited_scratch_dir(qapp):
    cfg = AppConfig(scratch_dir="/old/scratch")
    dialog = SettingsDialog(cfg)
    dialog._scratch_dir_edit.setText("/new/scratch")

    updated = dialog.updated_app_config()
    assert updated.scratch_dir == "/new/scratch"
    assert cfg.scratch_dir == "/old/scratch"  # original untouched


def test_browse_button_sets_scratch_dir(qapp, monkeypatch, tmp_path):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    monkeypatch.setattr(
        "aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory", lambda *a, **kw: str(tmp_path)
    )
    dialog._on_browse_scratch_dir()
    assert dialog.scratch_dir() == str(tmp_path)


def test_browse_cancelled_leaves_scratch_dir_unchanged(qapp, monkeypatch):
    cfg = AppConfig(scratch_dir="/keep/me")
    dialog = SettingsDialog(cfg)
    monkeypatch.setattr("aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory", lambda *a, **kw: "")
    dialog._on_browse_scratch_dir()
    assert dialog.scratch_dir() == "/keep/me"


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


# --- U3: the remaining AppConfig fields that previously required
# hand-editing config.yaml ----------------------------------------------


def test_u3_fields_seeded_from_app_config(qapp):
    cfg = AppConfig(
        default_safety_mode="relaxed",
        allowed_folders=["/shared/refs"],
        command_allowlist=["git status"],
        max_context_tokens=50_000,
    )
    dialog = SettingsDialog(cfg)
    assert dialog.default_safety_mode() == "relaxed"
    assert dialog.allowed_folders() == ["/shared/refs"]
    assert dialog.command_allowlist() == ["git status"]
    assert dialog.max_context_tokens() == 50_000


def test_u3_fields_default_from_a_fresh_app_config(qapp):
    dialog = SettingsDialog(AppConfig())
    assert dialog.default_safety_mode() == "confirm"
    assert dialog.allowed_folders() == []
    assert dialog.command_allowlist() == []
    assert dialog.max_context_tokens() == 120_000


def test_u3_fields_editable_and_reflected_in_updated_app_config(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._default_safety_combo.setCurrentText("relaxed")
    dialog._allowed_folders_edit.setPlainText("/one\n/two")
    dialog._command_allowlist_edit.setPlainText("git status\ngit log *")
    dialog._max_context_tokens_spin.setValue(75_000)

    updated = dialog.updated_app_config()
    assert updated.default_safety_mode == "relaxed"
    assert updated.allowed_folders == ["/one", "/two"]
    assert updated.command_allowlist == ["git status", "git log *"]
    assert updated.max_context_tokens == 75_000
    # original untouched
    assert cfg.default_safety_mode == "confirm"


def test_max_context_tokens_zero_means_disabled(qapp):
    """max_context_tokens=0 (AppConfig's own documented "trimming disabled"
    value) must stay reachable from the spin box, not clamped to 1."""
    dialog = SettingsDialog(AppConfig())
    dialog._max_context_tokens_spin.setValue(0)
    assert dialog.max_context_tokens() == 0


def test_profile_row_shows_capability_notes_when_set(qapp):
    """U7 paper cut: "capability_notes is stored but shown nowhere"."""
    profiles = {
        "local": ProviderProfile(
            name="local", kind="openai_compat", model="llama", capability_notes="small local model — prefer lean MCP groups"
        ),
        "argo-claude": ProviderProfile(name="argo-claude", kind="anthropic", model="claude-x"),
    }
    dialog = SettingsDialog(AppConfig(), profiles)
    items = [dialog._profiles_list.item(i).text() for i in range(dialog._profiles_list.count())]
    assert any("small local model — prefer lean MCP groups" in text for text in items)
    # A profile with no capability_notes gets no trailing " — " at all.
    assert not any(text.startswith("argo-claude") and " — " in text for text in items)


def test_theme_field_no_longer_exists_on_app_config(qapp):
    """U3: theme was a dead, write-only setting — removed rather than
    exposed in this dialog. Regression guard against it quietly coming
    back."""
    assert not hasattr(AppConfig(), "theme")
    assert not hasattr(SettingsDialog(AppConfig()), "_theme_combo")
