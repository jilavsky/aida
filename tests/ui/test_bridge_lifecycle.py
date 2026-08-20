"""Regression tests for the session-restart race found in the Phase 6 review.

The scenario: the user switches workspace (or resumes a conversation, or
closes the window) while the current session is still being built —
plausible in practice because ``start_session`` launches MCP subprocesses,
which is slow. Before the fix, ``ChatBridge.shutdown()`` short-circuited on
``self.session is None``, so the in-flight start finished *unowned* (leaking
its MCP subprocesses and SQLite connection), and then emitted
``session_ready`` into a window that had already replaced that bridge —
where ``MainWindow._on_session_ready`` read ``self.bridge.session`` (the new,
not-yet-started bridge, i.e. ``None``) and raised ``AttributeError`` out of a
Qt slot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aida.config.settings import ProviderProfile, Settings, load_settings
from aida.providers.mock import MockProvider, MockTurn
from aida.ui.qt._qt import QMessageBox
from aida.ui.qt.bridge import ChatBridge
from aida.ui.qt.main_window import MainWindow
from tests.ui._qt_test_utils import pump_until


def _settings_with_profile(name: str = "mock-profile") -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(name=name, kind="openai_compat", model="mock-model")
    return settings


def _ready_window(qapp, loop_thread, monkeypatch, **start_kwargs) -> MainWindow:
    """A started, fully-settled MainWindow. Settling matters: a window left
    with an undelivered queued signal has it delivered by the *next* test's
    ``processEvents()`` — which is how an unrelated startup failure ended up
    opening a modal dialog inside another test and hanging the run."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    window = MainWindow(_settings_with_profile(), loop_thread, start_kwargs=start_kwargs)
    assert pump_until(
        qapp,
        lambda: window.statusBar().currentMessage().startswith("Ready")
        or window.statusBar().currentMessage() == "Startup failed",
    )
    return window


def test_main_window_survives_session_ready_with_no_session(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """``_on_session_ready`` used to raise AttributeError whenever the
    current bridge had no session, leaving the window stuck on
    "Starting session…" with every panel unrefreshed."""
    window = _ready_window(qapp, loop_thread, monkeypatch, profile_name="mock-profile")
    try:
        window.bridge.session = None
        window._on_session_ready()  # must not raise
    finally:
        window.bridge.session = None  # already torn down below; don't double-close
        window.bridge.shutdown()
        window.close()
        qapp.processEvents()


def test_shutdown_closes_a_session_that_was_still_starting(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """A bridge shut down mid-start must still close whatever that start
    produced, instead of walking away and leaking it."""
    import aida.cli.chat as chat_module
    import aida.ui.qt.bridge as bridge_module

    release = asyncio.Event()
    real_start = chat_module.start_session
    closed: list[str] = []

    async def slow_start(settings, **kwargs):
        await release.wait()
        session, mcp_manager = await real_start(settings, **kwargs)
        real_aclose = session.aclose

        async def recording_aclose() -> None:
            closed.append("session")
            await real_aclose()

        session.aclose = recording_aclose
        return session, mcp_manager

    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr(bridge_module, "start_session", slow_start)

    settings = _settings_with_profile()
    bridge = ChatBridge(loop_thread)
    bridge.start(settings, profile_name="mock-profile")
    assert bridge.session is None, "precondition: the start is still blocked"

    # Shut down while the start is still blocked, then let it complete.
    loop_thread.loop.call_soon_threadsafe(release.set)
    bridge.shutdown(timeout=10.0)

    assert bridge.session is not None, "the start did complete — the point is that it got closed anyway"
    assert bridge._closed is True
    assert closed == ["session"], "a session finished after shutdown() must still be closed, not leaked"


def test_a_superseded_bridge_cannot_drive_the_window(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """After ``_restart_session``, the old bridge must be fully
    disconnected — otherwise its late signals reach handlers that resolve
    state through ``self.bridge`` and act on the wrong session's data."""
    window = _ready_window(qapp, loop_thread, monkeypatch, profile_name="mock-profile")
    try:
        old_bridge = window.bridge

        # A stale bridge must not be able to open dialogs at the user.
        dialogs: list[str] = []
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialogs.append("critical"))
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: dialogs.append("warning"))

        window._restart_session(workspace_name=None, profile_name="mock-profile", resume_conversation_id=None)
        assert window.bridge is not old_bridge

        # Emitting on the retired bridge must reach nothing: no crash, no
        # dialog, and no state driven by a session that's already closed.
        old_bridge.session_ready.emit()
        old_bridge.startup_failed.emit("stale failure")
        old_bridge.turn_failed.emit("stale turn failure")
        old_bridge.event_received.emit(object())
        qapp.processEvents()

        assert dialogs == [], "a superseded bridge still reached MainWindow's dialog handlers"
        assert pump_until(qapp, lambda: window.statusBar().currentMessage().startswith("Ready"))
    finally:
        window.bridge.shutdown()
        window.close()
        qapp.processEvents()


def test_shutdown_is_idempotent(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    """``_restart_session`` shuts a bridge down, and ``closeEvent`` may shut
    the same one down again — the second call must not double-close."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    bridge = ChatBridge(loop_thread)
    bridge.start(_settings_with_profile(), profile_name="mock-profile")
    bridge.shutdown(timeout=10.0)
    bridge.shutdown(timeout=10.0)  # must not raise
    assert bridge._closed is True
