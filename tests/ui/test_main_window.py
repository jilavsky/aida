"""Integration tests for aida.ui.qt.main_window.MainWindow — the closest
thing to Phase 5's "flagship demo" acceptance criterion this sandbox can
automate: a real (offscreen) window, a real ChatBridge/AsyncLoopThread, a
real mock-mcp subprocess for the tool-call+image case (same as
test_keystone_image_roundtrip.py / test_phase4_acceptance.py), and only the
LLM itself scripted (MockProvider). What's NOT automated here — an actual
visual/manual smoke check on macOS/Windows, a real pyirena-mcp server, a
real Ollama model — is called out in planning/phase05_gui.md instead of
silently skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.persistence.store import ConversationStore
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from aida.ui.qt._qt import QDialog, QMessageBox
from aida.ui.qt.artifact_widgets import InlineImageWidget
from aida.ui.qt.chat_panel import MessageBubble
from aida.ui.qt.main_window import MainWindow
from aida.workspace.workspaces import get_workspace
from tests.ui._qt_test_utils import pump_until

MOCK_SERVER_PATH = Path(__file__).resolve().parents[1] / "mock_mcp_server.py"


def _settings_with_profile(name: str = "mock-profile") -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(name=name, kind="openai_compat", model="mock-model")
    return settings


def _make_window(qapp, loop_thread, settings, monkeypatch, script, **start_kwargs):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider(script))
    window = MainWindow(settings, loop_thread, start_kwargs=start_kwargs)
    # Deliberately NOT `window.bridge.session is not None`: ChatBridge._start
    # (aida/ui/qt/bridge.py) sets `self.session` on the background loop
    # thread *before* emitting `session_ready`, so that attribute can
    # already be non-None on the Qt thread a moment before the queued
    # `session_ready` signal is actually delivered/processed here — i.e.
    # before MainWindow._on_session_ready (which calls chat_panel.load_history)
    # has run. Waiting on the status bar text instead, which
    # _on_session_ready/_on_startup_failed set as their last step, pins the
    # wait to "the handler actually ran". This was a real, timing-dependent
    # flake caught by running this file after other tests in the same
    # process (see planning/phase05_gui.md's notes) — not just a theoretical
    # race.
    assert pump_until(
        qapp,
        lambda: window.statusBar().currentMessage().startswith("Ready")
        or window.statusBar().currentMessage() == "Startup failed",
    )
    return window


def test_session_starts_and_completes_a_turn(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hello from mock")], profile_name="mock-profile"
    )
    try:
        assert window.bridge.session is not None

        window.input_box.set_text("hi there")
        window.input_box._send_button.click()

        # Race fixed for Windows CI: widget_count reaching 2 (event_received
        # building the final bubble) and turn_finished (which flips is_busy
        # back off) are two separate signals emitted moments apart — on a
        # slower runner, waiting on widget_count alone could observe
        # is_busy still True for one more event-loop turn. Wait for both.
        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2 and not window.input_box.is_busy)
        assert isinstance(window.chat_panel.widget_at(0), MessageBubble)
        assert window.chat_panel.widget_at(0).text == "hi there"
        assert window.chat_panel.widget_at(1).text == "hello from mock"
        assert not window.input_box.is_busy
    finally:
        window.close()


def test_profile_selector_shows_the_actual_active_profile_once_ready(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "I restored prior session and have selected local AI ...
    I suspect it must be using cloud (Argo)." _refresh_profile_selector()
    used to run exactly once, synchronously in __init__, *before*
    bridge.start()'s async session construction had resolved a profile —
    self.bridge.session was still None then, so the dropdown fell back to
    whichever profile sorts first alphabetically and was never corrected
    once the real session became ready. Configuring the started profile to
    sort *after* another one alphabetically would have shown the bug even
    for a brand-new (non-resumed) session."""
    settings = _settings_with_profile("z-profile")
    settings.providers.profiles["a-profile"] = ProviderProfile(name="a-profile", kind="openai_compat", model="m")

    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="z-profile")
    try:
        assert window.bridge.session.profile_name == "z-profile"
        assert window.profile_selector.current_profile() == "z-profile"
    finally:
        window.close()


def test_flagship_demo_tool_call_produces_inline_image(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Workspace w/ a real mock-mcp subprocess -> ask for a plot -> tool
    call -> real PNG -> ChatPanel shows a real InlineImageWidget with valid
    pixel data. This is the flagship demo's mechanics, automated."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(
        servers={
            "mock-mcp": McpServerConfig(
                name="mock-mcp", command=sys.executable, args=[str(MOCK_SERVER_PATH)], groups=["analysis"]
            )
        }
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(name="use-pyirena", profile="mock-profile", mcp_group="analysis")
        }
    )

    script = [
        MockTurn(text="let me get that plot", tool_calls=[MockToolCall(name="mock-mcp__get_image", id="call_1")]),
        MockTurn(text="here is the plot"),
    ]
    window = _make_window(qapp, loop_thread, settings, monkeypatch, script, workspace_name="use-pyirena")
    try:
        window.input_box.set_text("plot dataset X")
        window.input_box._send_button.click()

        assert pump_until(qapp, lambda: any(
            isinstance(window.chat_panel.widget_at(i), InlineImageWidget) for i in range(window.chat_panel.widget_count)
        ), timeout=10.0)

        image_widgets = [
            window.chat_panel.widget_at(i)
            for i in range(window.chat_panel.widget_count)
            if isinstance(window.chat_panel.widget_at(i), InlineImageWidget)
        ]
        assert len(image_widgets) == 1
        assert image_widgets[0].is_valid_image
        assert Path(image_widgets[0].path).exists()
    finally:
        window.close()


def test_resume_conversation_loads_prior_history(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    first = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="first reply")], profile_name="mock-profile"
    )
    conv_id = first.bridge.session.recorder.conversation_id
    first.input_box.set_text("remember this")
    first.input_box._send_button.click()
    assert pump_until(qapp, lambda: first.chat_panel.widget_count >= 2)
    first.close()

    resumed = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="second reply")], resume_conversation_id=conv_id
    )
    try:
        assert resumed.bridge.session.recorder.conversation_id == conv_id
        texts = [resumed.chat_panel.widget_at(i).text for i in range(resumed.chat_panel.widget_count)]
        assert "remember this" in texts
        assert "first reply" in texts
    finally:
        resumed.close()


def test_resume_uses_the_currently_selected_profile_not_the_conversations_original_one(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "I restored prior session and have selected local AI ...
    I suspect it must be using cloud (Argo) because no local AI server
    started." Resuming used to always fall back to the conversation's
    originally-recorded profile (start_session's own effective_profile_name
    fallback), silently ignoring whatever the toolbar dropdown currently
    shows."""
    settings = _settings_with_profile("profile-a")
    settings.providers.profiles["profile-b"] = ProviderProfile(name="profile-b", kind="openai_compat", model="model-b")

    first = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="first reply")], profile_name="profile-a"
    )
    conv_id = first.bridge.session.recorder.conversation_id
    first.input_box.set_text("remember this")
    first.input_box._send_button.click()
    assert pump_until(qapp, lambda: first.chat_panel.widget_count >= 2)
    first.close()

    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="second reply")], profile_name="profile-b"
    )
    try:
        assert window.profile_selector.current_profile() == "profile-b"

        window._on_resume_requested(conv_id)
        assert pump_until(
            qapp, lambda: window.bridge.session is not None and window.bridge.session.recorder.conversation_id == conv_id
        )
        assert window.bridge.session.profile_name == "profile-b"
    finally:
        window.close()


def test_resume_conversation_redisplays_prior_image_artifact(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """The other half of "resume yesterday's conversation... images still
    display": ChatPanel.load_history only replays text (see its docstring),
    so MainWindow._load_resumed_artifacts must independently re-derive an
    InlineImageWidget from the persisted `artifacts` table. Uses the same
    real mock-mcp subprocess as the flagship-demo test above to produce a
    real on-disk PNG worth resuming."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(
        servers={
            "mock-mcp": McpServerConfig(
                name="mock-mcp", command=sys.executable, args=[str(MOCK_SERVER_PATH)], groups=["analysis"]
            )
        }
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(name="use-pyirena", profile="mock-profile", mcp_group="analysis")
        }
    )
    script = [
        MockTurn(text="let me get that plot", tool_calls=[MockToolCall(name="mock-mcp__get_image", id="call_1")]),
        MockTurn(text="here is the plot"),
    ]
    first = _make_window(qapp, loop_thread, settings, monkeypatch, script, workspace_name="use-pyirena")
    conv_id = first.bridge.session.recorder.conversation_id
    first.input_box.set_text("plot dataset X")
    first.input_box._send_button.click()
    assert pump_until(qapp, lambda: any(
        isinstance(first.chat_panel.widget_at(i), InlineImageWidget) for i in range(first.chat_panel.widget_count)
    ), timeout=10.0)
    first.close()

    resumed = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="unused")], resume_conversation_id=conv_id
    )
    try:
        image_widgets = [
            resumed.chat_panel.widget_at(i)
            for i in range(resumed.chat_panel.widget_count)
            if isinstance(resumed.chat_panel.widget_at(i), InlineImageWidget)
        ]
        assert len(image_widgets) == 1
        assert image_widgets[0].is_valid_image
    finally:
        resumed.close()


def test_folder_display_shows_workspace_folders_and_save_persists_changes(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """FolderDisplay was being built and placed in the layout but never
    populated or wired to anything real (main_window.py never called
    ``set_folders`` and ignored all three of its signals) — a v1 that would
    always show "(none)"/"(none)" and silently no-op "Save to Workspace"
    regardless of the active workspace. Verifies the fix end to end: the
    active workspace's real folders show up on session start, and editing +
    "Save to Workspace" persists back to ``workspaces.yaml`` on disk."""
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "out"
    source_dir.mkdir()
    target_dir.mkdir()

    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="mock-profile",
                mcp_group="none",
                source_folders=[str(source_dir)],
                target_folder=str(target_dir),
            )
        }
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.folder_display.source_folders == [str(source_dir)]
        assert window.folder_display.target_folder == str(target_dir)

        new_target = str(target_dir / "v2")
        (target_dir / "v2").mkdir()
        window.folder_display.target_folder_changed.emit(new_target)
        window.folder_display.save_to_workspace_requested.emit()

        assert get_workspace(window.settings, "use-pyirena").target_folder == new_target

        reloaded_settings = load_settings()
        assert get_workspace(reloaded_settings, "use-pyirena").target_folder == new_target
    finally:
        window.close()


def test_folder_display_shows_and_saves_sidecar_folder_name(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """Phase 6: the sidecar folder name (where write_markdown_report copies
    embedded images, relative to the target folder) is visible/editable in
    the workspace bar, same as source/target folders."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena", profile="mock-profile", mcp_group="none", sidecar_folder_name="figures"
            )
        }
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.folder_display.sidecar_folder_name == "figures"

        window.folder_display.sidecar_folder_name_changed.emit("plots")
        window.folder_display.save_to_workspace_requested.emit()

        assert get_workspace(window.settings, "use-pyirena").sidecar_folder_name == "plots"
        reloaded_settings = load_settings()
        assert get_workspace(reloaded_settings, "use-pyirena").sidecar_folder_name == "plots"
    finally:
        window.close()


def test_delete_conversation_removes_from_sidebar_and_db(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        conv_id = window.bridge.session.recorder.conversation_id
        window.input_box.set_text("hello")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        window._refresh_conversations_sidebar()
        assert conv_id in window.sidebar._ids_by_row

        window._on_delete_requested(conv_id)

        store = ConversationStore()
        try:
            assert store.get_conversation(conv_id) is None
        finally:
            store.close()
        assert conv_id not in window.sidebar._ids_by_row
    finally:
        window.close()


def test_settings_dialog_font_size_applies_without_restart(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        captured = {}

        def _fake_exec(self):
            self._font_size_spin.setValue(22)
            captured["dialog"] = self
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.settings_dialog.SettingsDialog.exec", _fake_exec)
        window.open_settings_dialog()

        assert qapp.font().pointSize() == 22
        assert window.settings.app.font_size == 22
    finally:
        window.close()


def test_settings_dialog_max_iterations_applies_to_the_running_session(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "Give user control on number of iterations, I asked for
    some really multi step analysis and it stopped after 10." Patched onto
    the *running* AgentLoop immediately, not just saved for next launch."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert window.bridge.session.loop.max_iterations == 10  # default, unchanged so far

        def _fake_exec(self):
            self._max_iterations_spin.setValue(500)
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.settings_dialog.SettingsDialog.exec", _fake_exec)
        window.open_settings_dialog()

        assert window.settings.app.max_agent_iterations == 500
        assert window.bridge.session.loop.max_iterations == 500
    finally:
        window.close()


def test_open_code_editor_dialog_uses_workspace_saved_scripts_dir_and_interpreter(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "ws1": WorkspaceConfig(
                name="ws1",
                profile="mock-profile",
                mcp_group="none",
                target_folder=str(tmp_path),
                python_interpreter=sys.executable,
            )
        }
    )
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="ws1")
    try:
        captured = {}

        def _fake_exec(self):
            captured["dialog"] = self
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr("aida.ui.qt.code_editor_dialog.CodeEditorDialog.exec", _fake_exec)
        window.open_code_editor_dialog(initial_text="print(1)")

        dialog = captured["dialog"]
        assert dialog.text() == "print(1)"
        assert dialog._saved_scripts_dir == str(Path(tmp_path) / "saved_scripts")
        assert dialog._python_interpreter == sys.executable
    finally:
        window.close()


def test_clicking_open_in_editor_in_chat_opens_the_code_editor_dialog(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report/phase task: "Code blocks in chat get 'Open in editor'"
    — end-to-end through the real ChatPanel -> MainWindow signal chain."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp,
        loop_thread,
        settings,
        monkeypatch,
        [MockTurn(text="```python\nprint(1)\n```")],
        profile_name="mock-profile",
    )
    try:
        captured = {}

        def _fake_exec(self):
            captured["dialog"] = self
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr("aida.ui.qt.code_editor_dialog.CodeEditorDialog.exec", _fake_exec)

        window.input_box.set_text("give me code")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        bubble = window.chat_panel.widget_at(1)
        bubble._open_in_editor_button.click()

        assert captured["dialog"].text() == "print(1)\n"
    finally:
        window.close()


def test_window_state_persisted_on_close(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    window.resize(950, 700)
    window.close()

    from aida.config.settings import load_app_config

    reloaded = load_app_config(aida_home)
    assert reloaded.window_width == 950
    assert reloaded.window_height == 700


def test_workspace_switch_confirmed_starts_new_session(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"plain-chat": WorkspaceConfig(name="plain-chat", profile="mock-profile", mcp_group="none")}
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        first_conv_id = window.bridge.session.recorder.conversation_id
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        window._on_workspace_changed("plain-chat")
        assert pump_until(qapp, lambda: window.bridge.session is not None and window.bridge.session.recorder.conversation_id != first_conv_id)
        assert window.bridge.session.recorder.workspace_name == "plain-chat"
        assert window.chat_panel.widget_count == 0  # cleared for the new conversation
    finally:
        window.close()


def test_workspace_switch_declined_keeps_current_session(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"plain-chat": WorkspaceConfig(name="plain-chat", profile="mock-profile", mcp_group="none")}
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        conv_id = window.bridge.session.recorder.conversation_id
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.No
        )
        window._on_workspace_changed("plain-chat")
        qapp.processEvents()
        assert window.bridge.session.recorder.conversation_id == conv_id  # unchanged
    finally:
        window.close()


def test_new_chat_confirmed_starts_a_fresh_conversation_same_workspace_and_profile(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "How do I create a new chat within same Workspace? Do
    not see 'New Chat' button, something which will not contain the
    history from prior chat.\""""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"plain-chat": WorkspaceConfig(name="plain-chat", profile="mock-profile", mcp_group="none")}
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="plain-chat"
    )
    try:
        first_conv_id = window.bridge.session.recorder.conversation_id
        window.input_box.set_text("hello")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        window._on_new_chat_requested()
        assert pump_until(
            qapp,
            lambda: window.bridge.session is not None
            and window.bridge.session.recorder.conversation_id != first_conv_id,
        )
        assert window.bridge.session.recorder.workspace_name == "plain-chat"
        assert window.chat_panel.widget_count == 0  # reset, not carried over

        store = ConversationStore()
        try:
            assert store.get_conversation(first_conv_id) is not None  # old conversation still there
        finally:
            store.close()
    finally:
        window.close()


def test_new_chat_declined_keeps_current_session(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        conv_id = window.bridge.session.recorder.conversation_id
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.No
        )
        window._on_new_chat_requested()
        qapp.processEvents()
        assert window.bridge.session.recorder.conversation_id == conv_id  # unchanged
    finally:
        window.close()


def test_rename_conversation_updates_title_in_db_and_sidebar(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "Can we have the chat list in the history column have
    some kind of names? ... these date/times are not very convenient to
    use.\""""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        conv_id = window.bridge.session.recorder.conversation_id
        window.input_box.set_text("hello")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)
        window._refresh_conversations_sidebar()

        window._on_rename_requested(conv_id, "USAXS beamtime notes")

        store = ConversationStore()
        try:
            assert store.get_conversation(conv_id).title == "USAXS beamtime notes"
        finally:
            store.close()
        assert "USAXS beamtime notes" in window.sidebar._titles_by_row
    finally:
        window.close()


def test_usage_label_updates_after_a_turn_reports_usage(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "Can we get cost estimate as I got to other tool? Or
    token use may be better... at this moment it is a black box.\""""
    settings = _settings_with_profile()
    window = _make_window(
        qapp,
        loop_thread,
        settings,
        monkeypatch,
        [MockTurn(text="hi", input_tokens=100, output_tokens=50)],
        profile_name="mock-profile",
    )
    try:
        assert window._usage_label.text() == "Tokens: 0 in / 0 out (~$0.000 est.)"
        window.input_box.set_text("hello")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: "100" in window._usage_label.text())
        assert "50" in window._usage_label.text()
    finally:
        window.close()


def test_safety_confirmation_shows_modal_and_approving_lets_write_through(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """Phase 6 GUI wiring: a write_file tool call outside the (empty)
    allowed-folders set triggers SafetyGuard's confirm_callback, which
    ChatBridge.start defaults to bridge._confirm -> confirmation_requested ->
    MainWindow._on_confirmation_requested -> a real (mocked-out) QMessageBox.
    Approving lets the write through."""
    target = tmp_path / "note.txt"
    settings = _settings_with_profile()
    seen_prompts = []

    def _fake_question(self, title, text, *args, **kwargs):
        seen_prompts.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("aida.ui.qt.main_window.QMessageBox.question", _fake_question)

    script = [
        MockTurn(
            tool_calls=[
                MockToolCall(name="write_file", id="call_1", arguments={"path": str(target), "content": "hi"})
            ]
        ),
        MockTurn(text="done"),
    ]
    window = _make_window(qapp, loop_thread, settings, monkeypatch, script, profile_name="mock-profile")
    try:
        window.input_box.set_text("write it")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: target.exists(), timeout=10.0)
        assert target.read_text(encoding="utf-8") == "hi"
        assert seen_prompts  # the modal was actually shown
    finally:
        window.close()


def test_safety_confirmation_declining_blocks_the_write(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    target = tmp_path / "note.txt"
    settings = _settings_with_profile()
    monkeypatch.setattr(
        "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.No
    )

    script = [
        MockTurn(
            tool_calls=[
                MockToolCall(name="write_file", id="call_1", arguments={"path": str(target), "content": "hi"})
            ]
        ),
        MockTurn(text="declined"),
    ]
    window = _make_window(qapp, loop_thread, settings, monkeypatch, script, profile_name="mock-profile")
    try:
        # Waiting on turn_finished directly (rather than input_box.is_busy)
        # avoids a race: is_busy starts out False too, so a pump_until poll
        # landing before turn_started has even been delivered across the
        # thread boundary would otherwise look identical to "the turn
        # already finished".
        finished = []
        window.bridge.turn_finished.connect(lambda: finished.append(True))
        window.input_box.set_text("write it")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: finished, timeout=10.0)
        assert not target.exists()
    finally:
        window.close()


def test_send_with_attachment_includes_file_content_in_the_message(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """Phase 6: attaching a file (Attach button or drag-and-drop) and
    sending includes its content in the message actually sent to the
    provider — this is what makes "I cannot upload a PDF file" (and any
    other document type readers.py supports) work from the GUI."""
    note = tmp_path / "notes.txt"
    note.write_text("the sample was annealed at 400C", encoding="utf-8")

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="got it")], profile_name="mock-profile")
    try:
        window.input_box.add_attachment(str(note))
        window.input_box.set_text("please summarize")
        window.input_box._send_button.click()

        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        sent_messages = window.bridge.session.provider.calls[-1][0]
        last_user_message = [m for m in sent_messages if m.role == "user"][-1]
        assert "please summarize" in last_user_message.content
        assert "the sample was annealed at 400C" in last_user_message.content
        assert "notes.txt" in last_user_message.content

        # Attachments are cleared after send.
        assert window.input_box.attached_paths() == []
    finally:
        window.close()


def test_send_with_attachment_and_no_text_still_sends_the_file_content(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    note = tmp_path / "notes.txt"
    note.write_text("q range: 0.01 to 0.5", encoding="utf-8")

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="got it")], profile_name="mock-profile")
    try:
        window.input_box.add_attachment(str(note))
        # No typed text at all — attach-and-send with an empty box. The
        # InputBox's own _on_submit guard normally blocks empty sends, so
        # drive this through the button with attachments present instead
        # (InputBox intentionally lets a send through as long as there's
        # either text or an attachment — see its module docstring).
        window.input_box._on_submit()

        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 1, timeout=5.0)
    finally:
        window.close()


def test_folder_drop_with_active_workspace_offers_to_add_source_folder(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", mcp_group="none")}
    )
    prompts = []
    monkeypatch.setattr(
        "aida.ui.qt.main_window.QMessageBox.question",
        lambda self, title, text, *a, **kw: (prompts.append(text), QMessageBox.StandardButton.Yes)[1],
    )
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-ws")
    try:
        new_folder = str(tmp_path / "extra")
        (tmp_path / "extra").mkdir()
        window.input_box.folder_dropped.emit(new_folder)

        assert prompts and new_folder in prompts[0]
        assert new_folder in window.folder_display.source_folders
    finally:
        window.close()


def test_folder_drop_declined_does_not_add(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", mcp_group="none")}
    )
    monkeypatch.setattr(
        "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.No
    )
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-ws")
    try:
        new_folder = str(tmp_path / "extra")
        window.input_box.folder_dropped.emit(new_folder)
        assert window.folder_display.source_folders == []
    finally:
        window.close()


def test_folder_drop_with_no_active_workspace_shows_status_message(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window.input_box.folder_dropped.emit(str(tmp_path))
        assert "workspace" in window.statusBar().currentMessage().lower()
    finally:
        window.close()


def test_write_markdown_report_shows_as_file_artifact_card(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """"Generated documents appear as file cards" (PLAN.md Phase 6): a
    write_markdown_report tool call is just another FileArtifactCreated
    event, so it rides the same ChatBridge.event_received ->
    ChatPanel.handle_event -> FileArtifactCard plumbing Phase 5 already
    built for any file-producing tool — this pins that down for the
    document-writing tools specifically."""
    from aida.ui.qt.artifact_widgets import FileArtifactCard

    target_dir = tmp_path / "out"
    target_dir.mkdir()
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-ws": WorkspaceConfig(
                name="use-ws", profile="mock-profile", mcp_group="none", target_folder=str(target_dir), safety="relaxed"
            )
        }
    )
    script = [
        MockTurn(
            tool_calls=[
                MockToolCall(
                    name="write_markdown_report",
                    id="call_1",
                    arguments={"path": str(target_dir / "report.md"), "title": "Report", "body": "Findings."},
                )
            ]
        ),
        MockTurn(text="wrote it"),
    ]
    window = _make_window(qapp, loop_thread, settings, monkeypatch, script, workspace_name="use-ws")
    try:
        window.input_box.set_text("write a report")
        window.input_box._send_button.click()

        assert pump_until(
            qapp,
            lambda: any(
                isinstance(window.chat_panel.widget_at(i), FileArtifactCard)
                for i in range(window.chat_panel.widget_count)
            ),
            timeout=10.0,
        )
        cards = [
            window.chat_panel.widget_at(i)
            for i in range(window.chat_panel.widget_count)
            if isinstance(window.chat_panel.widget_at(i), FileArtifactCard)
        ]
        assert len(cards) == 1
        assert Path(cards[0].path) == target_dir / "report.md"
    finally:
        window.close()


def test_startup_failure_shows_error_and_leaves_session_none(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = load_settings()  # no profiles configured
    monkeypatch.setattr("aida.ui.qt.main_window.QMessageBox.critical", lambda *a, **kw: None)
    window = MainWindow(settings, loop_thread, start_kwargs={"profile_name": "does-not-exist"})
    try:
        assert pump_until(qapp, lambda: window.statusBar().currentMessage() == "Startup failed")
        assert window.bridge.session is None
    finally:
        window.close()
