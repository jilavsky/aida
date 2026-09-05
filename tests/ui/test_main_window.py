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
    QuickTask,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.core.confirmation import ConfirmAnswer
from aida.core.events import ContextTrimmed
from aida.persistence.store import ConversationStore
from aida.providers.base import Message
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from aida.ui.qt._qt import QApplication, QDesktopServices, QDialog, QMessageBox, Qt
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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider(script))
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


def test_workspace_selector_shows_the_actual_active_workspace_once_ready(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: on startup the last workspace is clearly loaded (the
    folder display/MCP panel/session all reflect it correctly) but the
    toolbar's workspace dropdown shows "(no workspace)" regardless. Same
    root cause and same fix as
    ``test_profile_selector_shows_the_actual_active_profile_once_ready``:
    __init__'s one-time ``_refresh_workspace_selector()`` call runs right
    after ``bridge.start()`` kicks off session construction on the
    background loop, so ``self.bridge.session`` is still ``None`` at that
    point and the dropdown falls back to "(no workspace)" — and unlike the
    profile selector, nothing ever refreshed it again for a normal
    startup/resume, so it stayed wrong for the rest of the session even
    though the session itself was using the right workspace the whole
    time."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-pyirena": WorkspaceConfig(name="use-pyirena", profile="mock-profile")}
    )

    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.bridge.session.recorder.workspace_name == "use-pyirena"
        assert window.workspace_selector.current_workspace() == "use-pyirena"
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


def test_user_selector_updates_config_and_stamps_the_restarted_conversation(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert pump_until(qapp, lambda: settings.app.last_profile_name == "mock-profile")
        first_conversation_id = window.bridge.session.recorder.conversation_id

        # setCurrentText alone no longer emits: the selector commits on
        # Return / focus-out, not per keystroke (see UserSelector).
        window.user_selector._combo.setCurrentText("Alice")
        window.user_selector._combo.lineEdit().editingFinished.emit()

        assert pump_until(
            qapp,
            lambda: window.bridge.session is not None
            and window.bridge.session.recorder.conversation_id != first_conversation_id,
        )
        assert settings.app.active_user == "Alice"
        assert window.bridge.session.recorder.user == "Alice"
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
    display": MainWindow._load_resumed_history queries the `artifacts`
    table directly (ChatPanel.load_history only knows about Messages) and
    hands both to ChatPanel.load_history to build an InlineImageWidget.
    Uses the same real mock-mcp subprocess as the flagship-demo test above
    to produce a real on-disk PNG worth resuming — this also exercises
    U6(b)'s seq-based interleaving end to end (the image lands at its
    recorded seq, not just appended after the whole transcript)."""
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


def test_folder_display_shows_and_saves_command_allowlist_and_interpreter(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: the command allowlist and Python interpreter
    (WorkspaceConfig.command_allowlist/.python_interpreter) were CLI-only —
    ``aida workspace edit --command-allowlist ...`` — with no GUI way to
    manage what run_command may run without confirmation. Verifies the same
    show-on-start + edit + "Save to Workspace" round trip as the folder
    fields already get."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="mock-profile",
                mcp_group="none",
                command_allowlist=["git status"],
                python_interpreter="/opt/env/bin/python",
            )
        }
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.folder_display.command_allowlist == ["git status"]
        assert window.folder_display.python_interpreter == "/opt/env/bin/python"

        window.folder_display.command_allowlist_changed.emit(["git status", "git log *"])
        window.folder_display.python_interpreter_changed.emit("/usr/bin/python3")
        window.folder_display.save_to_workspace_requested.emit()

        saved = get_workspace(window.settings, "use-pyirena")
        assert saved.command_allowlist == ["git status", "git log *"]
        assert saved.python_interpreter == "/usr/bin/python3"

        reloaded_settings = load_settings()
        reloaded = get_workspace(reloaded_settings, "use-pyirena")
        assert reloaded.command_allowlist == ["git status", "git log *"]
        assert reloaded.python_interpreter == "/usr/bin/python3"
    finally:
        window.close()


# --- quick tasks panel (B14) --------------------------------------------


def test_quick_tasks_panel_shows_workspace_tasks_and_edits_persist(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """User request: workspace-specific routine-task templates, editable
    add/remove/edit. Verifies the same show-on-start + edit + auto-persist
    round trip the folder fields get, minus an explicit "Save" step (quick
    tasks persist immediately on every add/edit/delete, like the
    conversations sidebar's rename/delete)."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="mock-profile",
                mcp_group="none",
                quick_tasks=[QuickTask(name="Reduce data", text="Reduce today's USAXS runs.")],
            )
        }
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.quick_tasks_panel.count == 1
        assert window.quick_tasks_panel.tasks()[0].name == "Reduce data"
        assert window.quick_tasks_panel.isEnabled()

        from aida.ui.qt.quick_tasks_panel import QuickTaskData

        window.quick_tasks_panel.tasks_changed.emit(
            [QuickTaskData(name="Reduce data", text="Reduce today's USAXS runs."), QuickTaskData(name="Fit Guinier", text="Fit a Guinier region.")]
        )

        saved = get_workspace(window.settings, "use-pyirena")
        assert [t.name for t in saved.quick_tasks] == ["Reduce data", "Fit Guinier"]

        reloaded_settings = load_settings()
        reloaded = get_workspace(reloaded_settings, "use-pyirena")
        assert [t.name for t in reloaded.quick_tasks] == ["Reduce data", "Fit Guinier"]
    finally:
        window.close()


def test_quick_tasks_panel_empty_and_disabled_with_no_active_workspace(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert window.quick_tasks_panel.count == 0
        assert not window.quick_tasks_panel.isEnabled()
    finally:
        window.close()


def test_quick_task_selected_fills_the_input_box(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="mock-profile",
                mcp_group="none",
                quick_tasks=[QuickTask(name="Reduce data", text="Reduce today's USAXS runs.")],
            )
        }
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.input_box.text() == ""
        window.quick_tasks_panel.task_selected.emit("Reduce today's USAXS runs.")
        assert window.input_box.text() == "Reduce today's USAXS runs."
    finally:
        window.close()


def test_quick_task_selected_with_a_draft_asks_before_replacing(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Double-clicking a quick task while the user has unsent text in the
    input box must not silently discard it."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-pyirena": WorkspaceConfig(name="use-pyirena", profile="mock-profile", mcp_group="none")}
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        window.input_box.set_text("a draft the user was mid-typing")
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.No
        )
        window.quick_tasks_panel.task_selected.emit("Reduce today's USAXS runs.")
        assert window.input_box.text() == "a draft the user was mid-typing"

        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        window.quick_tasks_panel.task_selected.emit("Reduce today's USAXS runs.")
        assert window.input_box.text() == "Reduce today's USAXS runs."
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


def test_delete_many_requested_removes_all_from_sidebar_and_db(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "Enable multiple file selection ... useful for deleting
    multiple chats." — the sidebar's bulk-delete signal."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        conv_id = window.bridge.session.recorder.conversation_id
        window.input_box.set_text("hello")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        store = ConversationStore()
        try:
            other_id = store.create_conversation(timestamp="2026-08-23T00:00:00", workspace_name=None, profile_name=None)
            store.append_message(other_id, Message(role="user", content="hi"), timestamp="2026-08-23T00:00:01")
        finally:
            store.close()
        window._refresh_conversations_sidebar()
        assert conv_id in window.sidebar._ids_by_row
        assert other_id in window.sidebar._ids_by_row

        window._on_delete_many_requested([conv_id, other_id])

        store = ConversationStore()
        try:
            assert store.get_conversation(conv_id) is None
            assert store.get_conversation(other_id) is None
        finally:
            store.close()
        assert conv_id not in window.sidebar._ids_by_row
        assert other_id not in window.sidebar._ids_by_row
    finally:
        window.close()


# --- empty conversations are never left behind (bug report: "Let's not add
# in this list ... conversations which have no messages in them ... or
# remove automatically when new conversation is created") -------------------


def test_sidebar_never_shows_the_freshly_started_empty_conversation(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        conv_id = window.bridge.session.recorder.conversation_id
        window._refresh_conversations_sidebar()
        assert conv_id not in window.sidebar._ids_by_row
    finally:
        window.close()


def test_new_chat_on_an_untouched_conversation_deletes_it(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """The conversation MainWindow.__init__ creates up front is deleted
    outright once New Chat replaces it, since nothing was ever sent to it —
    it would otherwise sit in the DB forever as a permanent "(untitled)"
    row (hidden from the sidebar by the fix above, but never cleaned up)."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        first_conv_id = window.bridge.session.recorder.conversation_id
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        window._on_new_chat_requested()
        assert pump_until(
            qapp,
            lambda: window.bridge.session is not None
            and window.bridge.session.recorder.conversation_id != first_conv_id,
        )

        store = ConversationStore()
        try:
            assert store.get_conversation(first_conv_id) is None
        finally:
            store.close()
    finally:
        window.close()


def test_workspace_switch_on_an_untouched_conversation_deletes_it(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"plain-chat": WorkspaceConfig(name="plain-chat", profile="mock-profile", mcp_group="none")}
    )
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        first_conv_id = window.bridge.session.recorder.conversation_id
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        window._on_workspace_changed("plain-chat")
        assert pump_until(
            qapp,
            lambda: window.bridge.session is not None
            and window.bridge.session.recorder.conversation_id != first_conv_id,
        )

        store = ConversationStore()
        try:
            assert store.get_conversation(first_conv_id) is None
        finally:
            store.close()
    finally:
        window.close()


def test_close_event_deletes_an_untouched_conversation(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    conv_id = window.bridge.session.recorder.conversation_id
    window.close()

    store = ConversationStore()
    try:
        assert store.get_conversation(conv_id) is None
    finally:
        store.close()


def test_close_event_keeps_a_conversation_with_messages(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    conv_id = window.bridge.session.recorder.conversation_id
    window.input_box.set_text("hello")
    window.input_box._send_button.click()
    assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)
    window.close()

    store = ConversationStore()
    try:
        assert store.get_conversation(conv_id) is not None
    finally:
        store.close()


def test_mcp_quick_panel_manage_button_opens_management_dialog(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: the quick panel's checkboxes silently did nothing when
    ticked (now fixed for real — see the tests below); its "MCP Servers…"
    button must also actually open the full management dialog (previously
    nothing connected McpQuickPanel to it at all)."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        opened = {}

        def _fake_exec(self):
            opened["dialog"] = self
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.mcp_management_dialog.McpManagementDialog.exec", _fake_exec)
        window.mcp_panel.manage_requested.emit()

        assert "dialog" in opened
    finally:
        window.close()


def test_mcp_panel_start_requested_calls_bridge_start_mcp_server(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        calls = []
        monkeypatch.setattr(window.bridge, "start_mcp_server", lambda name: calls.append(name))
        window.mcp_panel.server_start_requested.emit("mock-mcp")
        assert calls == ["mock-mcp"]
    finally:
        window.close()


def test_mcp_panel_stop_requested_calls_bridge_stop_mcp_server(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        calls = []
        monkeypatch.setattr(window.bridge, "stop_mcp_server", lambda name: calls.append(name))
        window.mcp_panel.server_stop_requested.emit("mock-mcp")
        assert calls == ["mock-mcp"]
    finally:
        window.close()


def test_mcp_server_status_changed_refreshes_the_quick_panel(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        refreshed = []
        monkeypatch.setattr(window, "_refresh_mcp_panel", lambda: refreshed.append(True))
        window.bridge.mcp_server_status_changed.emit("mock-mcp")
        assert refreshed == [True]
    finally:
        window.close()


def test_mcp_server_action_failed_warns_and_refreshes_the_quick_panel(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """A start/stop that fails must not leave the checkbox showing a state
    that isn't real — refreshing re-reads McpManager.running_server_names,
    which snaps a just-ticked-but-failed checkbox back to unchecked."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        warned = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: warned.append(a[2:]))
        refreshed = []
        monkeypatch.setattr(window, "_refresh_mcp_panel", lambda: refreshed.append(True))
        window.bridge.mcp_server_action_failed.emit("mock-mcp", "boom")
        assert warned and "mock-mcp" in warned[0][0] and "boom" in warned[0][0]
        assert refreshed == [True]
    finally:
        window.close()


def test_checking_a_box_in_the_quick_panel_really_starts_the_server(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """The other half of "can those checkboxes start/stop the mcp" — a
    real mock-mcp subprocess started and stopped purely by ticking the
    checkbox, verified via the actual McpManager.running_server_names (not
    just that a signal fired) — same real-subprocess testing approach as
    the flagship-demo test above."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(
        servers={"mock-mcp": McpServerConfig(name="mock-mcp", command=sys.executable, args=[str(MOCK_SERVER_PATH)])}
    )
    # No active workspace/mcp_group, so nothing auto-starts — the checkbox
    # is the only thing that can start this server.
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._refresh_mcp_panel()
        assert "mock-mcp" not in window.mcp_panel.enabled_servers()

        window.mcp_panel._checkboxes["mock-mcp"].setChecked(True)
        assert pump_until(
            qapp,
            lambda: window.bridge.mcp_manager is not None
            and "mock-mcp" in window.bridge.mcp_manager.running_server_names,
            timeout=10.0,
        )
        assert pump_until(qapp, lambda: "mock-mcp" in window.mcp_panel.enabled_servers())

        window.mcp_panel._checkboxes["mock-mcp"].setChecked(False)
        assert pump_until(
            qapp,
            lambda: window.bridge.mcp_manager is not None
            and "mock-mcp" not in window.bridge.mcp_manager.running_server_names,
            timeout=10.0,
        )
        assert pump_until(qapp, lambda: "mock-mcp" not in window.mcp_panel.enabled_servers())
    finally:
        window.close()


def test_failed_profile_switch_warns_and_resets_the_selector(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report class: "I selected local AI but it used Argo" — a failed
    mid-session profile switch (ChatBridge.profile_switch_failed) used to
    leave the toolbar dropdown showing a profile that was never actually
    put into use, with no indication anything failed. It should now show a
    warning dialog and reset the dropdown back to the profile that's
    genuinely active."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        warnings = []
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.warning",
            lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.Ok,
        )

        window.profile_selector.profile_changed.emit("does-not-exist")
        assert pump_until(qapp, lambda: warnings)

        assert window.bridge.session.profile_name == "mock-profile"  # unchanged
        assert window.profile_selector.current_profile() == "mock-profile"
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


def test_settings_dialog_empty_ocr_key_leaves_secret_unchanged(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        monkeypatch.setattr(
            "aida.ui.qt.settings_dialog.SettingsDialog.exec",
            lambda self: QDialog.DialogCode.Accepted,
        )
        saved = []
        monkeypatch.setattr("aida.ui.qt.main_window.set_secret", lambda *args: saved.append(args))

        window.open_settings_dialog()

        assert saved == []
    finally:
        window.close()


def test_settings_dialog_saves_a_nonempty_ocr_key(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    from aida.documents.ocr.mistral import SECRET_REF

    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        def _fake_exec(self):
            self._ocr_api_key_edit.setText("mistral-key")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.settings_dialog.SettingsDialog.exec", _fake_exec)
        saved = []
        monkeypatch.setattr("aida.ui.qt.main_window.set_secret", lambda *args: saved.append(args))

        window.open_settings_dialog()

        assert saved == [(SECRET_REF, "mistral-key")]
    finally:
        window.close()


def test_settings_dialog_warns_when_global_default_safety_becomes_relaxed(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """U3: flipping the *global default* safety mode gets the same
    one-time relaxed-mode warning a single workspace's own safety field
    already shows (relaxed_mode_warning_if_newly_enabled)."""
    settings = _settings_with_profile()
    settings.app.default_safety_mode = "confirm"
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:

        def _fake_exec(self):
            self._default_safety_combo.setCurrentText("relaxed")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.settings_dialog.SettingsDialog.exec", _fake_exec)
        warned = []
        monkeypatch.setattr("aida.ui.qt.main_window.QMessageBox.warning", lambda *a, **k: warned.append(True))
        window.open_settings_dialog()

        assert warned == [True]
        assert window.settings.app.default_safety_mode == "relaxed"
    finally:
        window.close()


def test_settings_dialog_does_not_warn_when_default_safety_stays_confirm(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:

        def _fake_exec(self):
            return QDialog.DialogCode.Accepted  # no changes made

        monkeypatch.setattr("aida.ui.qt.settings_dialog.SettingsDialog.exec", _fake_exec)
        warned = []
        monkeypatch.setattr("aida.ui.qt.main_window.QMessageBox.warning", lambda *a, **k: warned.append(True))
        window.open_settings_dialog()

        assert warned == []
    finally:
        window.close()


# --- U1/U2 toolbar dialogs ----------------------------------------------


def test_open_profiles_dialog_refreshes_the_profile_selector(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:

        def _fake_exec(self):
            self._settings.providers.profiles["new-profile"] = ProviderProfile(
                name="new-profile", kind="openai_compat", model="m"
            )
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.profiles_dialog.ProfilesDialog.exec", _fake_exec)
        window.open_profiles_dialog()

        assert window.profile_selector._combo.findText("new-profile") >= 0
    finally:
        window.close()


def test_open_workspace_management_dialog_refreshes_the_workspace_selector(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:

        def _fake_exec(self):
            self._settings.workspaces.workspaces["new-ws"] = WorkspaceConfig(name="new-ws")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "aida.ui.qt.workspace_management_dialog.WorkspaceManagementDialog.exec", _fake_exec
        )
        window.open_workspace_management_dialog()

        assert window.workspace_selector._combo.findText("new-ws") >= 0
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
        # Wait for the turn to *finish*, not merely for the assistant bubble
        # to exist: the bubble is created on the first TextDelta, so
        # widget_count alone is satisfied while the code fence is still
        # half-streamed and first_code_block() still returns None.
        assert pump_until(
            qapp, lambda: window.chat_panel.widget_count >= 2 and not window.input_box.is_busy
        )

        bubble = window.chat_panel.widget_at(1)
        bubble._open_in_editor_button.click()

        assert captured["dialog"].text() == "print(1)\n"
    finally:
        window.close()


def test_clicking_open_in_code_editor_on_a_generated_py_file_opens_it(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """Bug report: "agent writes correctly py file into target folder...
    perfect. But then when I try to open, it opens in system (text)
    editor... code editor has no way in." End-to-end through the real
    write_file tool -> FileArtifactCreated -> FileArtifactCard ->
    ChatPanel.open_in_code_editor_requested -> MainWindow chain — the
    dialog must open *at* the real file (Save/Run act on it directly), not
    a disconnected copy of its text."""
    from aida.ui.qt.artifact_widgets import FileArtifactCard

    target_dir = tmp_path / "out"
    target_dir.mkdir()
    script_path = target_dir / "reduce.py"
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
                    name="write_file",
                    id="call_1",
                    arguments={"path": str(script_path), "content": "print('generated')"},
                )
            ]
        ),
        MockTurn(text="wrote it"),
    ]
    window = _make_window(qapp, loop_thread, settings, monkeypatch, script, workspace_name="use-ws")
    try:
        captured = {}

        def _fake_exec(self):
            captured["dialog"] = self
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr("aida.ui.qt.code_editor_dialog.CodeEditorDialog.exec", _fake_exec)

        window.input_box.set_text("write a script")
        window.input_box._send_button.click()

        assert pump_until(
            qapp,
            lambda: any(
                isinstance(window.chat_panel.widget_at(i), FileArtifactCard)
                for i in range(window.chat_panel.widget_count)
            ),
            timeout=10.0,
        )
        card = next(
            window.chat_panel.widget_at(i)
            for i in range(window.chat_panel.widget_count)
            if isinstance(window.chat_panel.widget_at(i), FileArtifactCard)
        )
        card._editor_button.click()

        dialog = captured["dialog"]
        assert dialog.current_path == script_path
        assert dialog.text() == "print('generated')"
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


def test_restart_session_shows_busy_cursor_while_shutting_down_the_old_bridge(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """U7 paper cut: ChatBridge.shutdown() can block the Qt thread up to 5s
    with nothing to show for it — a busy cursor marks that pause as
    intentional rather than a hang, and must always be restored again
    afterward regardless of how shutdown() behaves."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        observed_cursors = []
        original_shutdown = window.bridge.shutdown

        def _spy_shutdown():
            observed_cursors.append(QApplication.overrideCursor())
            original_shutdown()

        monkeypatch.setattr(window.bridge, "shutdown", _spy_shutdown)
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.question", lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        window._on_new_chat_requested()

        assert observed_cursors and observed_cursors[0] is not None
        assert observed_cursors[0].shape() == Qt.CursorShape.WaitCursor
        assert QApplication.overrideCursor() is None  # restored once shutdown() returns
    finally:
        window.close()


def test_refresh_profile_selector_passes_capability_notes_as_tooltips(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """U7 paper cut: "capability_notes is stored but shown nowhere"."""
    settings = _settings_with_profile("mock-profile")
    settings.providers.profiles["mock-profile"].capability_notes = "small local model — prefer lean MCP groups"
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        index = window.profile_selector._combo.findText("mock-profile")
        assert (
            window.profile_selector._combo.itemData(index, Qt.ItemDataRole.ToolTipRole)
            == "small local model — prefer lean MCP groups"
        )
    finally:
        window.close()


# --- U7: File/Help menu bar --------------------------------------------------


def test_menu_bar_has_file_and_help_menus(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    """U7 paper cut: "A menu bar (File/Help) with 'Open config folder',
    'Open records folder', 'Documentation', 'About' — cheap discoverability
    for exactly the folders users otherwise have to find by hand." The app
    previously had no menu bar at all."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        menu_titles = [action.text().replace("&", "") for action in window.menuBar().actions()]
        assert "File" in menu_titles
        assert "Help" in menu_titles

        file_menu = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "File")
        file_actions = [a.text() for a in file_menu.actions()]
        assert "Open Config Folder" in file_actions
        assert "Open Records Folder" in file_actions
        assert "Open Conversation Folder" in file_actions

        help_menu = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Help")
        help_actions = [a.text() for a in help_menu.actions()]
        assert "Documentation" in help_actions
        assert "About AIDA" in help_actions
    finally:
        window.close()


def test_open_config_folder_opens_the_config_dir(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    from aida.config.paths import config_dir

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
        window._on_open_config_folder()
        # Compared via Path rather than a raw string: on Windows, Qt's
        # QUrl round-trip through fromLocalFile()/toLocalFile() has been
        # observed to come back with forward slashes rather than the
        # native backslash separator (real CI failure) — Path() treats
        # both as equivalent, which is all this test actually cares about.
        assert len(opened) == 1
        assert Path(opened[0]) == config_dir()
    finally:
        window.close()


def test_open_records_folder_opens_the_records_dir(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    from aida.config.paths import default_records_dir

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
        window._on_open_records_folder()
        # See test_open_config_folder_opens_the_config_dir's comment on why
        # this compares via Path rather than a raw string.
        assert len(opened) == 1
        assert Path(opened[0]) == default_records_dir()
    finally:
        window.close()


def test_open_scratch_folder_opens_the_scratch_dir(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    """Bug report: "Agents seem to be saving temporary files ... in random
    places" — the File-menu button that opens the one well-known scratch
    folder, mirroring test_open_records_folder_opens_the_records_dir."""
    from aida.config.paths import default_scratch_dir

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
        window._on_open_scratch_folder()
        # See test_open_config_folder_opens_the_config_dir's comment on why
        # this compares via Path rather than a raw string.
        assert len(opened) == 1
        assert Path(opened[0]) == default_scratch_dir()
    finally:
        window.close()


def test_open_conversation_folder_with_no_session_shows_status(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    session = window.bridge.session
    try:
        window.bridge.session = None
        window._on_open_conversation_folder()
        assert window.statusBar().currentMessage() == "No conversation open yet."
    finally:
        window.bridge.session = session
        window.close()


def test_open_conversation_folder_without_attachments_does_not_create_it(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        directory = window.bridge.session.recorder.attachments_dir()
        assert not directory.exists()
        window._on_open_conversation_folder()
        assert window.statusBar().currentMessage() == "Nothing has been attached to this conversation."
        assert not directory.exists()
    finally:
        window.close()


def test_open_conversation_folder_opens_the_existing_attachment_directory(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    note = tmp_path / "notes.txt"
    note.write_text("beamline notes", encoding="utf-8")
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        recorder = window.bridge.session.recorder
        window.input_box.add_attachment(str(note))
        window.input_box.set_text("keep this")
        window.input_box._send_button.click()
        assert pump_until(qapp, lambda: recorder.attachments_dir().is_dir())
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))

        window._on_open_conversation_folder()

        assert [Path(path) for path in opened] == [recorder.attachments_dir()]
    finally:
        window.close()


def test_open_documentation_opens_the_project_url(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
        window._on_open_documentation()
        assert opened == ["https://github.com/jilavsky/aida"]
    finally:
        window.close()


def test_show_about_displays_the_version(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    from aida import __version__

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        shown = []
        monkeypatch.setattr(QMessageBox, "about", lambda *a, **kw: shown.append(a[2]))
        window._on_show_about()
        assert shown and __version__ in shown[0]
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
        assert window._usage_label.text() == "Session total: 0 in / 0 out (~$0.000 est.)"
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
    ChatBridge.start defaults to a RememberingConfirm wrapping
    bridge._confirm_interactive -> confirmation_requested ->
    MainWindow._on_confirmation_requested -> a real (mocked-out) dialog via
    MainWindow._ask_confirmation. Approving lets the write through."""
    target = tmp_path / "note.txt"
    settings = _settings_with_profile()
    seen_prompts = []

    def _fake_ask_confirmation(self, request):
        seen_prompts.append(request.detail)
        return ConfirmAnswer.ALLOW_ONCE

    monkeypatch.setattr("aida.ui.qt.main_window.MainWindow._ask_confirmation", _fake_ask_confirmation)

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
        "aida.ui.qt.main_window.MainWindow._ask_confirmation", lambda self, request: ConfirmAnswer.DENY
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
        assert window.statusBar().currentMessage() == (
            "Attached notes.txt — copied into this conversation's folder"
        )

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


def test_send_with_large_attachment_is_not_truncated_below_the_interactive_cap(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """Regression test: _read_attachment_for_model used to hand
    read_document()'s text through describe_for_model() with its own
    separate, much smaller 4,000-char default, silently re-truncating any
    attached document (e.g. a real PDF paper) far below what read_document
    itself allows. A 10,000-char attachment — over the old 4,000-char
    ceiling, under the fixed 100,000-char interactive cap — must arrive
    whole in the message actually sent to the provider."""
    text = "abcdefghij" * 1_000  # 10,000 chars
    note = tmp_path / "notes.txt"
    note.write_text(text, encoding="utf-8")

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="got it")], profile_name="mock-profile")
    try:
        window.input_box.add_attachment(str(note))
        window.input_box.set_text("please summarize")
        window.input_box._send_button.click()

        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        sent_messages = window.bridge.session.provider.calls[-1][0]
        last_user_message = [m for m in sent_messages if m.role == "user"][-1]
        assert text in last_user_message.content
        assert "truncated" not in last_user_message.content
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


def test_context_trimmed_event_shows_in_the_status_bar(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """B7: trimming used to be invisible in the GUI (a log line only) —
    ContextTrimmed now surfaces in the same low-key status-bar channel
    already used for "Ready — profile" / "Saved folders to workspace X"."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_event_received(ContextTrimmed(dropped_turns=5, estimated_tokens=1234))
        message = window.statusBar().currentMessage()
        assert "5" in message
        assert "1234" in message
    finally:
        window.close()


def test_context_trimmed_summarized_event_shows_compacted_wording(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """PLAN.md §1.3: a compacted drop reads differently from a plain one —
    "the model still remembers a summary" is a materially different
    outcome from "these turns are just gone"."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_event_received(
            ContextTrimmed(dropped_turns=5, estimated_tokens=1234, summarized=True, summary_tokens=90)
        )
        message = window.statusBar().currentMessage()
        assert "compacted" in message.lower()
        assert "5" in message
        assert "90" in message
    finally:
        window.close()


def test_context_label_shows_fullness_after_session_ready(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """planning/context_management.md §3.5: a separate "Context: Nk / Mk
    (P%)" label, distinct from the ever-growing "Session total:" one."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert window._context_label.text().startswith("Context:")
    finally:
        window.close()


def test_compact_conversation_action_triggers_bridge_and_updates_status(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """The "Compact Conversation" File-menu action end to end: it reaches
    ChatBridge.compact_context, which (on success) re-emits the resulting
    ContextTrimmed through the normal event_received path — the same
    status-bar/context-label handling as an automatic mid-turn compaction."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp,
        loop_thread,
        settings,
        monkeypatch,
        [MockTurn(text="- summarized some old turns")],
        profile_name="mock-profile",
    )
    try:
        session = window.bridge.session
        for i in range(10):
            session.messages.append(Message(role="user", content=f"old question {i} " + "x" * 2000))
            session.messages.append(Message(role="assistant", content="old answer " + "y" * 2000))

        window._on_compact_requested()

        assert pump_until(qapp, lambda: "compacted" in window.statusBar().currentMessage().lower())
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


def test_startup_failure_with_a_configured_profile_shows_critical_dialog(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """U4: the onboarding panel only replaces the bare critical dialog when
    *zero* provider profiles are configured — this is the "something else
    is wrong" case (a real profile exists; the requested one is just a
    typo), which must still get the plain critical dialog, not the
    first-run panel."""
    settings = _settings_with_profile()  # a real profile exists
    monkeypatch.setattr("aida.ui.qt.main_window.QMessageBox.critical", lambda *a, **kw: None)
    window = MainWindow(settings, loop_thread, start_kwargs={"profile_name": "does-not-exist"})
    try:
        assert pump_until(qapp, lambda: window.statusBar().currentMessage() == "Startup failed")
        assert window.bridge.session is None
    finally:
        window.close()


def test_startup_failure_with_no_profiles_shows_onboarding_instead(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """U4: a genuine first run (no profiles configured at all) shows the
    onboarding panel instead of the bare "No profile given" critical
    dialog."""
    settings = load_settings()  # no profiles configured
    opened = []

    class _FakeOnboardingDialog:
        def __init__(self, *a, **k):
            opened.append(True)

        def exec(self):
            return 0

    monkeypatch.setattr("aida.ui.qt.main_window.OnboardingDialog", _FakeOnboardingDialog)
    critical_calls = []
    monkeypatch.setattr("aida.ui.qt.main_window.QMessageBox.critical", lambda *a, **kw: critical_calls.append(True))
    window = MainWindow(settings, loop_thread, start_kwargs={})
    try:
        assert pump_until(qapp, lambda: window.statusBar().currentMessage() == "Startup failed")
        assert window.bridge.session is None
        assert opened == [True]
        assert critical_calls == []  # onboarding replaced the critical dialog, didn't add to it
    finally:
        window.close()


# ---------------------------------------------------------------------------
# B1 (vision): a GUI-attached image file becomes an ImageRef on the outgoing
# user Message, end to end through InputBox -> MainWindow._on_send_requested
# -> ChatBridge.send -> ChatSession.send.
# ---------------------------------------------------------------------------

# Smallest possible valid PNG (1x1), same fixture used in
# tests/test_provider_translation.py's vision tests.
_TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def test_send_with_image_attachment_records_an_image_ref_on_the_sent_message(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    import base64

    from aida.providers.base import ImageRef

    image_path = tmp_path / "plot.png"
    image_path.write_bytes(base64.b64decode(_TINY_PNG_B64))

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="got it")], profile_name="mock-profile")
    try:
        window.input_box.add_attachment(str(image_path))
        window.input_box.set_text("what is this?")
        window.input_box._send_button.click()

        assert pump_until(qapp, lambda: window.chat_panel.widget_count >= 2)

        sent_messages = window.bridge.session.provider.calls[-1][0]
        last_user_message = [m for m in sent_messages if m.role == "user"][-1]
        assert "what is this?" in last_user_message.content
        assert "plot.png" in last_user_message.content  # still described in text too
        assert len(last_user_message.images) == 1
        assert isinstance(last_user_message.images[0], ImageRef)
        assert Path(last_user_message.images[0].path) == image_path

        # Attachments are cleared after send, same as any other attachment.
        assert window.input_box.attached_paths() == []
    finally:
        window.close()


def test_send_with_non_image_attachment_records_no_image_ref(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
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
        assert last_user_message.images == []
    finally:
        window.close()


def test_quick_tasks_panel_starts_disabled_before_a_session_is_ready(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report follow-up: the panel was constructed enabled, so an Add
    during startup was accepted by the panel and then silently dropped by
    _on_quick_tasks_changed (no workspace resolved yet). It now starts
    disabled and is enabled only by _refresh_quick_tasks_panel."""
    from aida.ui.qt.quick_tasks_panel import QuickTasksPanel

    panel = QuickTasksPanel()
    assert panel.isEnabled()  # the widget itself has no opinion...

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        # ...the window is what gates it on having a workspace to save to.
        assert not window.quick_tasks_panel.isEnabled()
    finally:
        window.close()


def test_quick_task_edit_without_a_workspace_says_so_instead_of_dropping_it(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    from aida.ui.qt.quick_tasks_panel import QuickTaskData

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._current_workspace_config = None

        window._on_quick_tasks_changed([QuickTaskData(name="Reduce", text="Reduce runs.")])

        assert "workspace" in window.statusBar().currentMessage()
    finally:
        window.close()


# --- mid-turn usage refresh ----------------------------------------------


def test_usage_labels_refresh_while_a_turn_is_running(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """User request: "while we are running a long session, the costs do not
    get updated... can the counter get updated every 2-5 minutes". The
    totals were only repainted on turn_finished, so a long tool-loop turn
    showed pre-turn numbers the whole way through — even though
    ChatSession accumulates them per model round trip."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        session = window.bridge.session
        assert session is not None
        window._update_usage_label()
        before = window._usage_label.text()

        # What the loop thread does as a turn progresses.
        session.total_input_tokens += 12_345
        session.total_output_tokens += 678
        window._on_usage_refresh_tick()

        assert window._usage_label.text() != before
        assert "12,345" in window._usage_label.text()
    finally:
        window.close()


def test_usage_refresh_polls_only_while_a_turn_is_in_flight(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Idle totals cannot change, so the poll must not outlive the turn."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert not window._usage_refresh_timer.isActive()

        window._on_turn_started()
        assert window._usage_refresh_timer.isActive()

        window._on_turn_finished()
        assert not window._usage_refresh_timer.isActive()
    finally:
        window.close()


def test_usage_refresh_stops_itself_when_the_session_is_gone(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """A bridge retired mid-turn never emits turn_finished, so the tick has
    to be able to stand itself down."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_turn_started()
        window.bridge.session = None

        window._on_usage_refresh_tick()

        assert not window._usage_refresh_timer.isActive()
    finally:
        window.close()


# --- workspace notes ------------------------------------------------------


def test_notes_panel_shows_workspace_notes_and_edits_persist(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """User request: "users really need a workspace notepad... it needs to
    be saved with workspace." Same show-on-start + edit + auto-persist round
    trip the quick tasks panel gets."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena", profile="mock-profile", mcp_group="none", notes="check run 42"
            )
        }
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    try:
        assert window.notes_panel.notes() == "check run 42"
        assert window.notes_panel.isEnabled()

        window.notes_panel.notes_changed.emit("check run 42\nand re-fit run 43")

        assert get_workspace(window.settings, "use-pyirena").notes == "check run 42\nand re-fit run 43"
        reloaded = get_workspace(load_settings(), "use-pyirena")
        assert reloaded.notes == "check run 42\nand re-fit run 43"
    finally:
        window.close()


def test_notes_panel_empty_and_disabled_with_no_active_workspace(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert window.notes_panel.notes() == ""
        assert not window.notes_panel.isEnabled()
    finally:
        window.close()


def test_closing_the_window_saves_a_note_typed_a_moment_earlier(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Saving is debounced, so quitting right after the last keystroke must
    flush rather than drop it."""
    settings = _settings_with_profile()
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-pyirena": WorkspaceConfig(name="use-pyirena", profile="mock-profile", mcp_group="none")}
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], workspace_name="use-pyirena"
    )
    window.notes_panel._edit.setPlainText("do not lose this")
    assert window.notes_panel.has_unsaved_edit

    window.close()

    assert get_workspace(load_settings(), "use-pyirena").notes == "do not lose this"


# --- collapsible session panels -------------------------------------------


def test_session_panels_collapse_and_the_state_is_remembered(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """User request: "we could make the different subwindows in the right
    panel collapsible... open only if user wants to change the content"."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        section = window._sections["Quick Tasks"]
        assert not section.is_collapsed

        section._header.click()

        assert section.is_collapsed
        assert not window.quick_tasks_panel.isVisibleTo(section)
        assert "Quick Tasks" in load_settings().app.collapsed_panels
    finally:
        window.close()


def test_collapsed_panels_reopen_collapsed(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    settings.app.collapsed_panels = ["MCP Servers", "Workspace Notes"]
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert window._sections["MCP Servers"].is_collapsed
        assert window._sections["Workspace Notes"].is_collapsed
        assert not window._sections["Folders"].is_collapsed
    finally:
        window.close()


# --- typing while the agent works -----------------------------------------


def test_send_while_a_turn_is_running_queues_instead_of_starting_a_turn(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """User request: "when agent is working, user has no chance for input to
    the process... so I can tell agent what I forgot"."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        queued: list[str] = []
        monkeypatch.setattr(window.bridge, "queue_user_message", lambda text: queued.append(text) or True)
        monkeypatch.setattr(type(window.bridge), "is_busy", property(lambda self: True))
        sent: list[str] = []
        monkeypatch.setattr(window.bridge, "send", lambda text, **kw: sent.append(text))

        window._on_send_requested("also check the background")

        assert queued == ["also check the background"]
        assert sent == []  # no second turn started
    finally:
        window.close()


def test_a_queued_message_the_turn_never_reached_comes_back(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Text accepted by the queue but never delivered must reappear in the
    input box, not vanish."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        monkeypatch.setattr(window.bridge, "take_undelivered_messages", lambda: ["what about run 43"])

        window._on_turn_finished()

        assert "what about run 43" in window.input_box.text()
    finally:
        window.close()


# --- REVIEW.md P1: state-mutating controls are disabled during a turn -------


def test_profile_selector_and_compaction_are_disabled_while_a_turn_runs(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Only the input box used to be disabled during a turn, leaving two live
    controls that rewrite the state the turn is using: switching profile
    closes the provider the running ``AgentLoop`` is streaming from, and
    Compact Conversation replaces the whole message list from a plan computed
    before an awaited summarization call.
    """
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="done")], profile_name="mock-profile"
    )
    try:
        assert window.profile_selector.isEnabled()
        assert window.compact_action.isEnabled()

        window._on_turn_started()
        assert not window.profile_selector.isEnabled()
        assert not window.compact_action.isEnabled()

        window._on_turn_finished()
        assert window.profile_selector.isEnabled()
        assert window.compact_action.isEnabled()
    finally:
        window.close()


def test_bridge_refuses_a_profile_switch_while_busy(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Enforced in the bridge as well as the window: the selector is only one
    of the routes into ``switch_profile``."""
    settings = _settings_with_profile()
    settings.providers.profiles["other"] = ProviderProfile(
        name="other", kind="openai_compat", model="mock-model"
    )
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="done")], profile_name="mock-profile"
    )
    # MainWindow is connected to profile_switch_failed and answers it with a
    # modal QMessageBox — which blocks forever under the offscreen platform.
    warned: list[tuple] = []
    monkeypatch.setattr(
        "aida.ui.qt.main_window.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args) or QMessageBox.StandardButton.Ok,
    )
    failures: list[str] = []
    window.bridge.profile_switch_failed.connect(failures.append)
    try:
        window.bridge._turn_future = object()  # pretend a turn is in flight
        window.bridge.switch_profile("other")
        qapp.processEvents()

        assert failures, "a switch during a turn was accepted"
        assert "turn is running" in failures[0]
        assert window.bridge.session.profile_name == "mock-profile"
    finally:
        window.bridge._turn_future = None
        window.close()


# --- Phase 10: scheduler wiring -------------------------------------------


def test_main_window_starts_a_scheduler_bridge(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert window.scheduler_bridge is not None
        assert window.scheduler_bridge._future is not None  # start() was called
    finally:
        window.close()


def test_schedule_run_finished_ok_refreshes_sidebar_without_a_failure_badge(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    from aida.persistence.store import ConversationStore
    from aida.providers.base import Message

    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        store = ConversationStore()
        conv_id = store.create_conversation(timestamp="2026-09-02T00:00:00", origin="schedule")
        # A real scheduled run's conversation always has at least one
        # message (the workflow's own prompt) — set_conversations filters
        # out message-less rows (see its own docstring), so an empty one
        # would never show up in the sidebar regardless of this handler.
        store.append_message(conv_id, Message(role="user", content="go"), timestamp="2026-09-02T00:00:01")
        store.close()

        window._on_schedule_run_finished("nightly", True, conv_id, "")

        assert conv_id in window.sidebar._ids_by_row
        assert not window._schedule_failures_button.isVisibleTo(window)
        assert window._schedule_failure_count == 0
    finally:
        window.close()


def test_schedule_run_finished_failure_shows_the_failure_badge(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_schedule_run_finished("nightly", False, "", "boom")

        assert window._schedule_failure_count == 1
        assert window._schedule_failures_button.isVisibleTo(window)
        assert "1" in window._schedule_failures_button.text()

        window._on_schedule_run_finished("often", False, "", "boom again")
        assert window._schedule_failure_count == 2
        assert "2" in window._schedule_failures_button.text()
    finally:
        window.close()


def test_clicking_the_failure_badge_opens_schedules_and_clears_the_count(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_schedule_run_finished("nightly", False, "", "boom")
        assert window._schedule_failures_button.isVisibleTo(window)

        opened = {}

        def _fake_exec(self):
            opened["dialog"] = self
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "aida.ui.qt.schedule_management_dialog.ScheduleManagementDialog.exec", _fake_exec
        )

        window._on_schedule_failures_clicked()

        assert "dialog" in opened
        assert window._schedule_failure_count == 0
        assert not window._schedule_failures_button.isVisibleTo(window)
    finally:
        window.close()


def test_open_schedule_management_dialog_passes_the_scheduler_bridge(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        opened = {}

        def _fake_exec(self):
            opened["bridge"] = self._scheduler_bridge
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "aida.ui.qt.schedule_management_dialog.ScheduleManagementDialog.exec", _fake_exec
        )

        window.open_schedule_management_dialog()

        assert opened["bridge"] is window.scheduler_bridge
    finally:
        window.close()


def test_close_event_stops_the_scheduler_bridge(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    window.close()
    assert window.scheduler_bridge._future is None


# --- Phase 10: workflow authoring wiring ----------------------------------


def test_open_workflow_management_dialog_opens(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        opened = {}

        def _fake_exec(self):
            opened["dialog"] = self
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr("aida.ui.qt.workflow_management_dialog.WorkflowManagementDialog.exec", _fake_exec)
        window.open_workflow_management_dialog()

        assert "dialog" in opened
    finally:
        window.close()


def test_save_conversation_as_workflow_with_no_session_shows_info(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    window = MainWindow(load_settings(), loop_thread)  # no start_kwargs => bridge.session stays None
    try:
        informed = []
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.information",
            lambda *a, **k: informed.append(True),
        )
        window._on_save_conversation_as_workflow()
        assert informed == [True]
    finally:
        window.close()


def test_save_conversation_as_workflow_with_no_user_messages_shows_info(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert pump_until(qapp, lambda: window.bridge.session is not None)
        informed = []
        monkeypatch.setattr(
            "aida.ui.qt.main_window.QMessageBox.information",
            lambda *a, **k: informed.append(True),
        )
        window._on_save_conversation_as_workflow()
        assert informed == [True]
    finally:
        window.close()


def test_save_conversation_as_workflow_derives_steps_from_user_messages(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    from aida.config.settings import list_workflow_names, load_workflow
    from aida.ui.qt.workflow_management_dialog import WorkflowFormDialog

    settings = _settings_with_profile()
    window = _make_window(
        qapp,
        loop_thread,
        settings,
        monkeypatch,
        [MockTurn(text="first reply"), MockTurn(text="second reply")],
        profile_name="mock-profile",
    )
    try:
        assert pump_until(qapp, lambda: window.bridge.session is not None)
        window.bridge.send("first prompt")
        assert pump_until(qapp, lambda: not window.bridge.is_busy)
        window.bridge.send("second prompt")
        assert pump_until(qapp, lambda: not window.bridge.is_busy)

        # Drive the real WorkflowFormDialog constructor (cheap, builds real
        # widgets) so the derived draft is genuinely what the form was
        # seeded with — only exec() is stubbed, to avoid a real modal loop.
        captured_draft = {}
        original_init = WorkflowFormDialog.__init__

        def _capturing_init(self, *, settings, workflow=None, is_edit=None, parent=None):
            captured_draft["workflow"] = workflow
            captured_draft["is_edit"] = is_edit
            original_init(self, settings=settings, workflow=workflow, is_edit=is_edit, parent=parent)
            self._name_edit.setText("from-chat")  # the draft's own name is blank; fill it in like a user would

        monkeypatch.setattr(WorkflowFormDialog, "__init__", _capturing_init)
        monkeypatch.setattr(WorkflowFormDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

        window._on_save_conversation_as_workflow()

        draft = captured_draft["workflow"]
        assert captured_draft["is_edit"] is False
        assert [s.prompt for s in draft.steps] == ["first prompt", "second prompt"]
        assert "from-chat" in list_workflow_names()
        assert [s.prompt for s in load_workflow("from-chat").steps] == ["first prompt", "second prompt"]
    finally:
        window.close()


# --- Phase 10: deferring scheduled jobs to the user ------------------------


def test_typing_marks_activity_and_unsent_text(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        activity = window.scheduler_bridge.activity
        activity.last_activity_monotonic -= 10_000
        assert activity.should_defer() is None  # idle, nothing typed

        window.input_box.set_text("half a prompt")
        qapp.processEvents()

        assert activity.has_unsent_text is True
        deferral = activity.should_defer()
        assert deferral is not None
        assert "unsent text" in deferral.reason
    finally:
        window.close()


def test_clearing_the_input_box_clears_unsent_text(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window.input_box.set_text("something")
        qapp.processEvents()
        assert window.scheduler_bridge.activity.has_unsent_text is True

        window.input_box.set_text("")
        qapp.processEvents()
        assert window.scheduler_bridge.activity.has_unsent_text is False
    finally:
        window.close()


def test_turn_start_and_finish_track_turn_in_flight(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert pump_until(qapp, lambda: window.bridge.session is not None)
        assert window.scheduler_bridge.activity.turn_in_flight is False

        window._on_turn_started()
        assert window.scheduler_bridge.activity.turn_in_flight is True

        window._on_turn_finished()
        assert window.scheduler_bridge.activity.turn_in_flight is False
    finally:
        window.close()


def test_pending_badge_appears_and_clears_from_the_snapshot(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_schedule_deferred_changed({"nightly": "waiting 240s for you to finish"})
        assert window._schedule_pending_button.isVisibleTo(window)
        assert "1 job waiting" in window._schedule_pending_button.text()
        assert "nightly" in window._schedule_pending_button.toolTip()

        window._on_schedule_deferred_changed({"nightly": "x", "other": "y"})
        assert "2 jobs waiting" in window._schedule_pending_button.text()

        window._on_schedule_deferred_changed({})
        assert not window._schedule_pending_button.isVisibleTo(window)
    finally:
        window.close()


def test_schedule_run_started_reports_in_the_status_bar(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        window._on_schedule_run_started("nightly")
        assert "nightly" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_quiet_period_comes_from_settings(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = _settings_with_profile()
    settings.app.scheduler_quiet_period_seconds = 42
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert window.scheduler_bridge.activity.quiet_period_seconds == 42
    finally:
        window.close()


def test_retiring_a_bridge_mid_turn_clears_turn_in_flight(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """A bridge retired mid-turn suppresses its own turn_finished, so
    nothing else would ever clear the flag — and the scheduler would
    defer every job forever."""
    settings = _settings_with_profile()
    window = _make_window(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile")
    try:
        assert pump_until(qapp, lambda: window.bridge.session is not None)
        window.scheduler_bridge.activity.turn_in_flight = True

        window._restart_session(workspace_name=None, profile_name="mock-profile", resume_conversation_id=None)

        assert window.scheduler_bridge.activity.turn_in_flight is False
    finally:
        window.close()


def test_manage_users_new_name_switches_like_the_toolbar_box_does(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Picking a name in the dialog and typing one in the toolbar must end
    in the same place — including the new chat a switch implies."""
    from aida.ui.qt import users_dialog as users_dialog_module
    from aida.ui.qt._qt import QDialog as _QDialog

    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert pump_until(qapp, lambda: settings.app.last_profile_name == "mock-profile")
        first_conversation_id = window.bridge.session.recorder.conversation_id

        def _fake_exec(self):
            self.new_active_user = "Jan"
            return _QDialog.DialogCode.Accepted

        monkeypatch.setattr(users_dialog_module.UsersDialog, "exec", _fake_exec)
        window.open_users_dialog()

        assert pump_until(
            qapp,
            lambda: window.bridge.session is not None
            and window.bridge.session.recorder.conversation_id != first_conversation_id,
        )
        assert settings.app.active_user == "Jan"
        assert window.bridge.session.recorder.user == "Jan"
        assert window.user_selector.current_user() == "Jan"
    finally:
        window.close()


def test_renaming_the_active_user_does_not_restart_the_session(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """A rename is a spelling fix, not a switch. The open conversation was
    relabelled in place by the UPDATE, so restarting would cost the user
    their chat for nothing."""
    from aida.ui.qt import users_dialog as users_dialog_module
    from aida.ui.qt._qt import QDialog as _QDialog

    settings = _settings_with_profile()
    settings.app.active_user = "Jam"
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert pump_until(qapp, lambda: settings.app.last_profile_name == "mock-profile")
        conversation_id = window.bridge.session.recorder.conversation_id

        def _fake_exec(self):
            self._store.rename_user("Jam", "Jan", timestamp="2026-01-03")
            self.changed = True
            self.renamed_from, self.renamed_to = "Jam", "Jan"
            return _QDialog.DialogCode.Accepted

        monkeypatch.setattr(users_dialog_module.UsersDialog, "exec", _fake_exec)
        window.open_users_dialog()

        assert settings.app.active_user == "Jan"
        assert window.bridge.session.recorder.conversation_id == conversation_id
    finally:
        window.close()


def test_a_declared_user_survives_switching_away_before_typing_anything(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "Creating second user seems to remove first user."

    A session's conversation row is created empty and deleted again if
    nothing is ever sent, so a name whose only conversation was that empty
    one vanished from the toolbar the moment the user switched away. The
    remembered list is unioned with the names the conversations carry, so
    it can only ever add — never contradict the database.
    """
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert pump_until(qapp, lambda: settings.app.last_profile_name == "mock-profile")

        window.user_selector._combo.setCurrentText("Alice")
        window.user_selector._combo.lineEdit().editingFinished.emit()
        assert pump_until(qapp, lambda: settings.app.active_user == "Alice")

        window.user_selector._combo.setCurrentText("Bob")
        window.user_selector._combo.lineEdit().editingFinished.emit()
        assert pump_until(qapp, lambda: settings.app.active_user == "Bob")

        assert set(window._known_users()) >= {"Alice", "Bob"}, "Alice must not have vanished"
    finally:
        window.close()


def test_switching_user_filters_the_sidebar_to_that_user(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: selecting a newly created user still listed the old
    chats. The sidebar filter has to follow the toolbar."""
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert pump_until(qapp, lambda: settings.app.last_profile_name == "mock-profile")
        store = ConversationStore()
        try:
            conv = store.create_conversation(timestamp="2026-01-01", title="old unlabelled chat")
            store.append_message(conv, Message(role="user", content="hi"), timestamp="2026-01-02")
        finally:
            store.close()
        window._refresh_conversations_sidebar()
        assert window.sidebar.count >= 1

        window.user_selector._combo.setCurrentText("Alice")
        window.user_selector._combo.lineEdit().editingFinished.emit()
        assert pump_until(qapp, lambda: settings.app.active_user == "Alice")
        window._refresh_conversations_sidebar()

        assert window.sidebar._user_filter.currentText() == "Alice"
        assert window.sidebar.count == 0, "a brand-new user has no history to show"
    finally:
        window.close()


def test_moving_a_conversation_to_a_user_relabels_and_remembers_the_name(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    window = _make_window(
        qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")], profile_name="mock-profile"
    )
    try:
        assert pump_until(qapp, lambda: settings.app.last_profile_name == "mock-profile")
        store = ConversationStore()
        try:
            conv = store.create_conversation(timestamp="2026-01-01", title="misfiled chat")
            store.append_message(conv, Message(role="user", content="hi"), timestamp="2026-01-02")
        finally:
            store.close()

        window._on_move_conversations_to_user([conv], "Carol")

        store = ConversationStore()
        try:
            assert store.get_conversation(conv).user == "Carol"
        finally:
            store.close()
        assert "Carol" in window._known_users(), "a name used only here must still be offered"
    finally:
        window.close()
