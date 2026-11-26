"""Tests for aida.ui.qt.settings_dialog.SettingsDialog — never calls
exec(); every test constructs the dialog, mutates widget state, and reads
values back directly, per this module's own docstring."""

from __future__ import annotations

from aida.config.settings import AppConfig, ProviderProfile
from aida.ui.qt.settings_dialog import SettingsDialog


def test_dialog_seeds_fields_from_app_config(qapp):
    cfg = AppConfig(
        font_size=14, records_dir="/data/records", log_level="DEBUG", max_agent_iterations=100
    )
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
        "aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory",
        lambda *a, **kw: str(tmp_path),
    )
    dialog._on_browse_records_dir()
    assert dialog.records_dir() == str(tmp_path)


def test_browse_cancelled_leaves_records_dir_unchanged(qapp, monkeypatch):
    cfg = AppConfig(records_dir="/keep/me")
    dialog = SettingsDialog(cfg)
    monkeypatch.setattr(
        "aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory", lambda *a, **kw: ""
    )
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
        "aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory",
        lambda *a, **kw: str(tmp_path),
    )
    dialog._on_browse_scratch_dir()
    assert dialog.scratch_dir() == str(tmp_path)


def test_browse_cancelled_leaves_scratch_dir_unchanged(qapp, monkeypatch):
    cfg = AppConfig(scratch_dir="/keep/me")
    dialog = SettingsDialog(cfg)
    monkeypatch.setattr(
        "aida.ui.qt.settings_dialog.QFileDialog.getExistingDirectory", lambda *a, **kw: ""
    )
    dialog._on_browse_scratch_dir()
    assert dialog.scratch_dir() == "/keep/me"


# --- assistant_name / user_context (B15) --------------------------------


def test_dialog_seeds_assistant_name_and_user_context_from_app_config(qapp):
    cfg = AppConfig(assistant_name="Beamie", user_context="Jan, beamline scientist at APS.")
    dialog = SettingsDialog(cfg)
    assert dialog.assistant_name() == "Beamie"
    assert dialog.user_context() == "Jan, beamline scientist at APS."


def test_dialog_defaults_assistant_name_to_aida(qapp):
    dialog = SettingsDialog(AppConfig())
    assert dialog.assistant_name() == "Aida"
    assert dialog.user_context() == ""


def test_editing_assistant_name_and_user_context_and_reading_back(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._assistant_name_edit.setText("Beamie")
    dialog._user_context_edit.setPlainText("Jan, beamline scientist at APS.")
    assert dialog.assistant_name() == "Beamie"
    assert dialog.user_context() == "Jan, beamline scientist at APS."


def test_blank_assistant_name_falls_back_to_the_original_value(qapp):
    cfg = AppConfig(assistant_name="Beamie")
    dialog = SettingsDialog(cfg)
    dialog._assistant_name_edit.setText("   ")
    assert dialog.assistant_name() == "Beamie"


def test_updated_app_config_includes_edited_assistant_name_and_user_context(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._assistant_name_edit.setText("Beamie")
    dialog._user_context_edit.setPlainText("Jan, beamline scientist at APS.")

    updated = dialog.updated_app_config()
    assert updated.assistant_name == "Beamie"
    assert updated.user_context == "Jan, beamline scientist at APS."
    assert cfg.assistant_name == "Aida"  # original untouched


# --- transcript_tool_results (Export Conversation As… / clean records) --


def test_dialog_seeds_transcript_tool_results_from_app_config(qapp):
    cfg = AppConfig(transcript_tool_results="summary")
    dialog = SettingsDialog(cfg)
    assert dialog.transcript_tool_results() == "summary"


def test_dialog_defaults_transcript_tool_results_to_full(qapp):
    dialog = SettingsDialog(AppConfig())
    assert dialog.transcript_tool_results() == "full"


def test_editing_transcript_tool_results_and_reading_back(qapp):
    dialog = SettingsDialog(AppConfig())
    dialog._transcript_tool_results_combo.setCurrentText("off")
    assert dialog.transcript_tool_results() == "off"


def test_updated_app_config_includes_edited_transcript_tool_results(qapp):
    cfg = AppConfig(transcript_tool_results="full")
    dialog = SettingsDialog(cfg)
    dialog._transcript_tool_results_combo.setCurrentText("summary")

    updated = dialog.updated_app_config()
    assert updated.transcript_tool_results == "summary"
    assert cfg.transcript_tool_results == "full"  # original untouched


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
            name="local",
            kind="openai_compat",
            model="llama",
            capability_notes="small local model — prefer lean MCP groups",
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


def test_scheduler_timings_round_trip_in_minutes(qapp, aida_home):
    """Stored in seconds, edited in minutes — nobody reasons about "wait
    300 seconds before starting a job"."""
    from aida.config.settings import AppConfig
    from aida.ui.qt.settings_dialog import SettingsDialog

    dialog = SettingsDialog(
        AppConfig(scheduler_quiet_period_seconds=300, scheduler_max_defer_seconds=3600), {}
    )
    assert dialog._scheduler_quiet_spin.value() == 5
    assert dialog._scheduler_max_defer_spin.value() == 60

    dialog._scheduler_quiet_spin.setValue(2)
    dialog._scheduler_max_defer_spin.setValue(30)
    updated = dialog.updated_app_config()
    assert updated.scheduler_quiet_period_seconds == 120
    assert updated.scheduler_max_defer_seconds == 1800


def test_scheduler_timings_accept_the_disabling_zero(qapp, aida_home):
    from aida.config.settings import AppConfig
    from aida.ui.qt.settings_dialog import SettingsDialog

    dialog = SettingsDialog(AppConfig(), {})
    dialog._scheduler_quiet_spin.setValue(0)  # "Never wait"
    dialog._scheduler_max_defer_spin.setValue(0)  # "Wait indefinitely"
    updated = dialog.updated_app_config()
    assert updated.scheduler_quiet_period_seconds == 0
    assert updated.scheduler_max_defer_seconds == 0


# --- documents (scanned PDF conversion) -----------------------------------


def test_documents_section_round_trips_backend_and_profile(qapp):
    profiles = {"light": ProviderProfile(name="light"), "argo": ProviderProfile(name="argo")}
    dialog = SettingsDialog(AppConfig(ocr_backend="auto", ocr_profile="light"), profiles)
    assert dialog.ocr_backend() == "auto" and dialog.ocr_profile() == "light"

    dialog._ocr_profile_combo.setCurrentIndex(0)
    dialog._ocr_backend_check.setChecked(False)
    updated = dialog.updated_app_config()
    assert (updated.ocr_backend, updated.ocr_profile) == ("off", None)
    assert not dialog._ocr_profile_combo.isEnabled()


def test_a_missing_ocr_profile_stays_visible_rather_than_being_dropped(qapp):
    dialog = SettingsDialog(AppConfig(ocr_profile="gone"), {})
    assert dialog.ocr_profile() == "gone"
    assert "missing" in dialog._ocr_profile_combo.currentText()


def test_personal_context_edits_the_active_users_own_text(qapp):
    """On a shared machine the box has to mean one person's context, not
    everybody's — and the label has to say which, or a box that silently
    means two different things depending on a dropdown elsewhere is worse
    than no box."""
    config = AppConfig(
        active_user="Jan",
        user_context="Shared framing.",
        user_contexts={"Jan": "Jan runs the beamline."},
    )
    dialog = SettingsDialog(config)

    assert dialog._user_context_edit.toPlainText() == "Jan runs the beamline."
    dialog._user_context_edit.setPlainText("Jan, updated.")
    updated = dialog.updated_app_config()

    assert updated.user_contexts == {"Jan": "Jan, updated."}
    assert updated.user_context == "Shared framing.", "the shared text must not be overwritten"


def test_clearing_a_users_context_falls_back_rather_than_storing_blank(qapp):
    """ "No personal context" and "an empty personal context" should not be
    different states."""
    config = AppConfig(
        active_user="Jan", user_context="Shared framing.", user_contexts={"Jan": "Jan's text."}
    )
    dialog = SettingsDialog(config)
    dialog._user_context_edit.setPlainText("   ")
    updated = dialog.updated_app_config()

    assert updated.user_contexts == {}
    assert updated.context_for_user("Jan") == "Shared framing."


def test_with_no_active_user_the_box_edits_the_shared_text(qapp):
    config = AppConfig(user_context="Shared framing.", user_contexts={"Jan": "Jan's text."})
    dialog = SettingsDialog(config)

    assert dialog._user_context_edit.toPlainText() == "Shared framing."
    dialog._user_context_edit.setPlainText("New shared framing.")
    updated = dialog.updated_app_config()

    assert updated.user_context == "New shared framing."
    assert updated.user_contexts == {"Jan": "Jan's text."}, "another user's text is untouched"


# --- automatic conversation titles (aida.core.titling) -------------------


def test_dialog_seeds_the_auto_title_fields_from_app_config(qapp):
    dialog = SettingsDialog(AppConfig(auto_title_conversations=False, auto_title_interval_turns=9))
    assert dialog.auto_title_conversations() is False
    assert dialog.auto_title_interval_turns() == 9


def test_dialog_defaults_automatic_titles_to_on(qapp):
    dialog = SettingsDialog(AppConfig())
    assert dialog.auto_title_conversations() is True
    assert dialog.auto_title_interval_turns() == 5


def test_the_interval_is_disabled_while_automatic_titles_are_off(qapp):
    """A re-check cadence means nothing with nothing to re-check."""
    dialog = SettingsDialog(AppConfig(auto_title_conversations=False))
    assert dialog._auto_title_interval_spin.isEnabled() is False

    dialog._auto_title_check.setChecked(True)
    assert dialog._auto_title_interval_spin.isEnabled() is True


def test_updated_app_config_includes_the_edited_auto_title_fields(qapp):
    cfg = AppConfig()
    dialog = SettingsDialog(cfg)
    dialog._auto_title_check.setChecked(False)
    dialog._auto_title_interval_spin.setValue(20)

    updated = dialog.updated_app_config()
    assert updated.auto_title_conversations is False
    assert updated.auto_title_interval_turns == 20
    assert cfg.auto_title_conversations is True  # original untouched


def test_dialog_round_trips_auto_continue_on_stall(qapp):
    dialog = SettingsDialog(AppConfig(auto_continue_on_stall=False))
    assert dialog.auto_continue_on_stall() is False
    dialog._auto_continue_check.setChecked(True)
    assert dialog.updated_app_config().auto_continue_on_stall is True


def test_loop_nudge_threshold_round_trips_and_skips_one(qapp):
    dialog = SettingsDialog(AppConfig(loop_nudge_threshold=3))
    assert dialog.loop_nudge_threshold() == 3
    dialog._loop_threshold_spin.setValue(0)
    assert dialog._loop_threshold_spin.text() == "Off"
    dialog._loop_threshold_spin.setValue(1)  # stepping up from Off
    assert dialog.loop_nudge_threshold() == 2
    dialog._loop_threshold_spin.setValue(1)  # stepping down from 2
    assert dialog.loop_nudge_threshold() == 0
    dialog._loop_threshold_spin.setValue(6)
    assert dialog.updated_app_config().loop_nudge_threshold == 6


def test_test_on_a_page_uses_the_settings_on_screen_and_shows_the_result(qapp, tmp_path):
    from concurrent.futures import Future

    from aida.documents.ocr.standalone import PageTestResult
    from aida.documents.ocr.vlm import ConversionResult
    from aida.ui.qt.page_test_dialog import PageTestDialog

    calls = []
    future: Future = Future()

    def tester(app_config, profiles, chat_profile_name, pdf, page_index):
        calls.append((app_config.ocr_profile, chat_profile_name, pdf, page_index))
        return future

    profiles = {
        "chat": ProviderProfile(name="chat", kind="openai_compat", model="m"),
        "ocr": ProviderProfile(
            name="ocr", kind="openai_compat", model="o", base_url="http://127.0.0.1:1234/v1"
        ),
    }
    dialog = SettingsDialog(AppConfig(), profiles, chat_profile_name="chat", page_tester=tester)
    dialog._ocr_profile_combo.setCurrentIndex(dialog._ocr_profile_combo.findData("ocr"))
    pdf = tmp_path / "scan.pdf"

    assert dialog.start_page_test(pdf, page_index=2)
    assert calls == [("ocr", "chat", pdf, 2)]
    assert not dialog._test_page_button.isEnabled()
    assert not dialog.start_page_test(pdf, page_index=0)  # one at a time

    conversion = ConversionResult(
        pages={2: "# Title\n\n$E = mc^2$"}, route_label="the OCR profile 'ocr'"
    )
    future.set_result(
        PageTestResult(pdf=pdf, page_index=2, conversion=conversion, page_image=b"", seconds=4.2)
    )
    dialog._poll_page_test()

    assert dialog._test_page_button.isEnabled()
    shown = dialog._page_test_dialog
    assert isinstance(shown, PageTestDialog)
    assert shown.markdown() == "# Title\n\n$E = mc^2$"
    assert "page 3: converted by the OCR profile 'ocr' in 4.2 s" in shown._summary_label.text()
    shown.close()


def test_test_on_a_page_says_why_when_no_model_would_convert(qapp, tmp_path, monkeypatch):
    from aida.ui.qt import settings_dialog

    shown = []
    monkeypatch.setattr(
        settings_dialog.QMessageBox, "information", lambda *args: shown.append(args[2])
    )
    profiles = {"chat": ProviderProfile(name="chat", kind="openai_compat", model="m")}
    dialog = SettingsDialog(
        AppConfig(), profiles, chat_profile_name="chat", page_tester=lambda *a: None
    )
    assert not dialog.start_page_test(tmp_path / "scan.pdf", page_index=0)
    assert shown and "supports_vision off" in shown[0]


def test_page_test_dialog_shows_figures_in_their_tab_and_in_the_preview(qapp, tmp_path):
    from aida.documents.figures import FigureEntry
    from aida.documents.ocr.standalone import PageFigure, PageTestResult
    from aida.documents.ocr.vlm import ConversionResult
    from aida.ui.qt._qt import QColor, QImage, QTextDocument, QUrl
    from aida.ui.qt.page_test_dialog import PageTestDialog, preview_markdown

    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    image.save(str(tmp_path / "fig.png"))
    png = (tmp_path / "fig.png").read_bytes()
    entry = FigureEntry(
        label="Figure 3", caption="Figure 3. I(q).", file="fig-01.png", page=1, confidence="high"
    )
    markdown = "# Results\n![fig-1](fig-1)\nFigure 3. I(q).\n![fig-2](fig-2)\n"
    result = PageTestResult(
        pdf=tmp_path / "x.pdf",
        page_index=0,
        conversion=ConversionResult(pages={0: markdown}, route_label="the chat model"),
        page_image=b"",
        seconds=1.0,
        figures=[PageFigure(entry, png, "fig-1")],
    )
    dialog = PageTestDialog(result)

    assert dialog._tabs.tabText(2) == "Figures (1)"
    assert "paired in order" in dialog.figures_note()
    kind = QTextDocument.ResourceType.ImageResource.value
    shown = dialog._preview.loadResource(kind, QUrl("fig-1"))
    assert isinstance(shown, QImage) and shown.width() == 40
    assert not isinstance(dialog._preview.loadResource(kind, QUrl("fig-2")), QImage)
    paragraphs = [b.strip() for b in preview_markdown(markdown).split("\n\n") if b.strip()]
    assert paragraphs == ["# Results", "![fig-1](fig-1)", "Figure 3. I(q).", "![fig-2](fig-2)"]
    assert dialog.markdown() == markdown  # the raw tab is left as returned
    dialog.close()
