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
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from aida.ui.qt._qt import QMessageBox
from aida.ui.qt.bridge import ChatBridge
from aida.ui.qt.main_window import MainWindow
from tests.ui._qt_test_utils import pump_until


def _settings_with_profile(name: str = "mock-profile") -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(
        name=name, kind="openai_compat", model="mock-model"
    )
    return settings


def _ready_window(qapp, loop_thread, monkeypatch, **start_kwargs) -> MainWindow:
    """A started, fully-settled MainWindow. Settling matters: a window left
    with an undelivered queued signal has it delivered by the *next* test's
    ``processEvents()`` — which is how an unrelated startup failure ended up
    opening a modal dialog inside another test and hanging the run."""
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")])
    )
    window = MainWindow(_settings_with_profile(), loop_thread, start_kwargs=start_kwargs)
    assert pump_until(
        qapp,
        lambda: (
            window.statusBar().currentMessage().startswith("Ready")
            or window.statusBar().currentMessage() == "Startup failed"
        ),
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

    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")])
    )
    monkeypatch.setattr(bridge_module, "start_session", slow_start)

    settings = _settings_with_profile()
    bridge = ChatBridge(loop_thread)
    bridge.start(settings, profile_name="mock-profile")
    assert bridge.session is None, "precondition: the start is still blocked"

    # Shut down while the start is still blocked, then let it complete.
    loop_thread.loop.call_soon_threadsafe(release.set)
    bridge.shutdown(timeout=10.0)

    assert bridge.session is not None, (
        "the start did complete — the point is that it got closed anyway"
    )
    assert bridge._closed is True
    assert closed == ["session"], (
        "a session finished after shutdown() must still be closed, not leaked"
    )


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

        window._restart_session(
            workspace_name=None, profile_name="mock-profile", resume_conversation_id=None
        )
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


def test_shutdown_is_idempotent(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """``_restart_session`` shuts a bridge down, and ``closeEvent`` may shut
    the same one down again — the second call must not double-close."""
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")])
    )
    bridge = ChatBridge(loop_thread)
    bridge.start(_settings_with_profile(), profile_name="mock-profile")
    bridge.shutdown(timeout=10.0)
    bridge.shutdown(timeout=10.0)  # must not raise
    assert bridge._closed is True


# --- retiring a bridge must actually disconnect it -------------------------
#
# Review finding: MainWindow._unwire_bridge_signals relied on
# bridge.disconnect(self), which only drops connections whose *receiver* is
# the window. Two connections weren't: `event_received ->
# self.chat_panel.handle_event` (receiver is the chat panel) and the two
# turn_started/turn_finished lambdas (no receiver at all). Both survived the
# disconnect, so a superseded session's remaining events rendered into the
# new chat panel and flipped the new input box's busy state.


def test_a_retired_bridge_cannot_render_into_the_new_chat_panel(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    from aida.core.events import TextDelta, TextFinished

    window = _ready_window(qapp, loop_thread, monkeypatch, profile_name="mock-profile")
    try:
        old_bridge = window.bridge
        window._restart_session(
            workspace_name=None, profile_name="mock-profile", resume_conversation_id=None
        )
        assert pump_until(qapp, lambda: window.statusBar().currentMessage().startswith("Ready"))
        assert window.chat_panel.widget_count == 0

        old_bridge.event_received.emit(TextDelta(message_id="stale", text="ghost text"))
        old_bridge.event_received.emit(TextFinished(message_id="stale", text="ghost text"))
        qapp.processEvents()

        assert window.chat_panel.widget_count == 0, (
            "a retired bridge painted into the live chat panel"
        )
    finally:
        window.bridge.shutdown()
        window.close()
        qapp.processEvents()


def test_a_retired_bridge_cannot_flip_the_new_input_boxs_busy_state(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    window = _ready_window(qapp, loop_thread, monkeypatch, profile_name="mock-profile")
    try:
        old_bridge = window.bridge
        window._restart_session(
            workspace_name=None, profile_name="mock-profile", resume_conversation_id=None
        )
        assert pump_until(qapp, lambda: window.statusBar().currentMessage().startswith("Ready"))

        old_bridge.turn_started.emit()
        qapp.processEvents()
        assert window.input_box.is_busy is False

        window.input_box.set_busy(True)
        old_bridge.turn_finished.emit()
        qapp.processEvents()
        assert window.input_box.is_busy is True, (
            "a retired bridge cleared the live input box's busy state"
        )
    finally:
        window.input_box.set_busy(False)
        window.bridge.shutdown()
        window.close()
        qapp.processEvents()


def test_shutdown_cancels_and_waits_for_an_in_flight_turn(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Review finding: shutdown() waited for an in-flight *start* but not an
    in-flight *turn*, so "New Chat" during a running tool call closed the
    session out from under the turn still using it, and the turn's remaining
    events landed in the new panel."""
    import aida.cli.chat as chat_module
    from aida.core.tools import NativeTool, ToolResult
    from aida.providers.base import ToolSchema

    release = asyncio.Event()
    started = asyncio.Event()

    async def _blocking_tool(_args):
        started.set()
        await release.wait()
        return ToolResult(content="finally done")

    real_start = chat_module.start_session

    async def _start_with_slow_tool(settings, **kwargs):
        session, mcp_manager = await real_start(settings, **kwargs)
        session.tools["slow"] = NativeTool(
            schema=ToolSchema(name="slow", description="", parameters={"type": "object"}),
            func=_blocking_tool,
        )
        return session, mcp_manager

    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(
                    tool_calls=[
                        MockToolCall(name="slow", id="c1"),
                        MockToolCall(name="slow", id="c2"),
                    ]
                ),
                MockTurn(text="done"),
            ]
        ),
    )
    monkeypatch.setattr("aida.ui.qt.bridge.start_session", _start_with_slow_tool)

    bridge = ChatBridge(loop_thread)
    bridge.start(_settings_with_profile(), profile_name="mock-profile")
    assert pump_until(qapp, lambda: bridge.session is not None)

    events: list[object] = []
    bridge.event_received.connect(events.append)
    bridge.send("run the slow tool")
    assert pump_until(qapp, started.is_set)

    events_before = len(events)
    # Windows CI flake (real report, not a theoretical worry): this used to
    # be `call_soon_threadsafe(release.set)` immediately followed by
    # `bridge.shutdown(...)`, racing two independent, unsynchronized actions
    # on two different OS threads. `shutdown()`'s `self._closing = True` is
    # the very first thing it does, synchronously, right here on this
    # thread — but for the assertion below to hold, that write has to land
    # before the *background loop thread* finishes processing `release.set`,
    # resuming the blocked tool call, and emitting its `ToolCallFinished`
    # event, which takes several loop-thread scheduling hops versus this
    # thread's single next bytecode. That margin was apparently always wide
    # enough on Linux/macOS CI to look deterministic, and wasn't always wide
    # enough on Windows CI, which showed up as one extra event in the list —
    # a race in this test's own instrumentation, not evidence ChatBridge
    # actually leaked the event under real timing. Setting `_closing`
    # directly here, before `release` is even scheduled to fire, makes the
    # ordering this test cares about (turn in flight *and already closing*
    # must not leak further events) true by construction instead of by
    # scheduling luck — `bridge.shutdown()` below just re-sets the same
    # flag, a harmless no-op, before doing its real async cleanup.
    bridge._closing = True
    loop_thread.loop.call_soon_threadsafe(release.set)
    bridge.shutdown(timeout=10.0)

    assert bridge._turn_future is None, "shutdown() returned with a turn still in flight"
    qapp.processEvents()
    assert len(events) == events_before, "a closing bridge kept emitting events"
    # The turn unwound through its normal cancel path, so every announced
    # tool call still has a matching result (see aida.core.agent).
    announced = {
        tc.id for m in bridge.session.messages if m.role == "assistant" for tc in m.tool_calls
    }
    answered = {m.tool_call_id for m in bridge.session.messages if m.role == "tool"}
    assert announced == answered
