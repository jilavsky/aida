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

        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)
        assert isinstance(window.chat_panel.widget_at(0), MessageBubble)
        assert window.chat_panel.widget_at(0).text == "hi there"
        assert window.chat_panel.widget_at(1).text == "hello from mock"
        assert not window.input_box.is_busy
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
        MockTurn(text="let me get that plot", tool_calls=[MockToolCall(name="mock-mcp.get_image", id="call_1")]),
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
        MockTurn(text="let me get that plot", tool_calls=[MockToolCall(name="mock-mcp.get_image", id="call_1")]),
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
