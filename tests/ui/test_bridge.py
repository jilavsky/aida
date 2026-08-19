"""Tests for aida.ui.qt.bridge.ChatBridge — the async-generator-to-Qt-signal
adapter every widget builds on. Uses a real background asyncio loop thread
(AsyncLoopThread) and a real MockProvider, same as the CLI's own tests —
only the "is this delivered as a Qt signal on the right thread" part is
new here.
"""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import ProviderProfile, load_settings
from aida.providers.mock import MockProvider, MockTurn
from aida.ui.qt.bridge import ChatBridge
from tests.ui._qt_test_utils import pump_until


def _settings_with_profile(name: str = "mock-profile") -> object:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(name=name, kind="openai_compat", model="mock-model")
    return settings


def test_start_success_fires_session_ready(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings_with_profile()

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    failed = []
    bridge.startup_failed.connect(failed.append)

    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: ready or failed), "session_ready/startup_failed never fired"
    assert ready and not failed
    assert bridge.session is not None

    bridge.shutdown()


def test_start_unknown_profile_fires_startup_failed(qapp, loop_thread, aida_home: Path, records_home: Path):
    settings = load_settings()  # no profiles configured

    bridge = ChatBridge(loop_thread)
    failed = []
    bridge.startup_failed.connect(failed.append)

    bridge.start(settings, profile_name="does-not-exist")
    assert pump_until(qapp, lambda: failed), "startup_failed never fired"
    assert "does-not-exist" in failed[0]
    assert bridge.session is None


def test_send_emits_events_in_order_and_turn_finished(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hello there")]))
    settings = _settings_with_profile()

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: ready)

    events = []
    finished = []
    bridge.event_received.connect(events.append)
    bridge.turn_finished.connect(lambda: finished.append(True))

    bridge.send("hello")
    assert pump_until(qapp, lambda: finished), "turn_finished never fired"

    event_types = [type(e).__name__ for e in events]
    assert event_types == ["TextStarted", "TextDelta", "TextFinished", "MessageFinished"]
    assert events[1].text == "hello there"

    bridge.shutdown()


def test_send_before_session_ready_is_a_safe_noop(qapp, loop_thread, aida_home: Path, records_home: Path):
    bridge = ChatBridge(loop_thread)
    bridge.send("too early")  # must not raise, must not hang
    qapp.processEvents()


def test_startup_failure_leaves_session_none_and_send_is_a_noop(
    qapp, loop_thread, aida_home: Path, records_home: Path
):
    settings = load_settings()
    bridge = ChatBridge(loop_thread)
    failed = []
    bridge.startup_failed.connect(failed.append)
    bridge.start(settings, profile_name="nope")
    assert pump_until(qapp, lambda: failed)

    events = []
    bridge.event_received.connect(events.append)
    bridge.send("hello")
    qapp.processEvents()
    assert events == []


def test_switch_profile_success(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    settings = load_settings()
    settings.providers.profiles["a"] = ProviderProfile(name="a", kind="openai_compat", model="model-a")
    settings.providers.profiles["b"] = ProviderProfile(name="b", kind="openai_compat", model="model-b")
    providers = {"a": MockProvider([MockTurn(text="from a")]), "b": MockProvider([MockTurn(text="from b")])}
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: providers[profile.name])

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    bridge.start(settings, profile_name="a")
    assert pump_until(qapp, lambda: ready)

    switched = []
    bridge.profile_switched.connect(switched.append)
    bridge.switch_profile("b")
    assert pump_until(qapp, lambda: switched)
    assert switched == ["b"]
    assert bridge.session.profile_name == "b"

    bridge.shutdown()


def test_switch_profile_unknown_fires_failure_signal(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings_with_profile()

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: ready)

    failures = []
    bridge.profile_switch_failed.connect(failures.append)
    bridge.switch_profile("does-not-exist")
    assert pump_until(qapp, lambda: failures)
    assert "does-not-exist" in failures[0]
    assert bridge.session.profile_name == "mock-profile"  # unchanged

    bridge.shutdown()


def test_shutdown_closes_provider(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    closed = []

    class _TrackingProvider(MockProvider):
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: _TrackingProvider([MockTurn(text="hi")]))
    settings = _settings_with_profile()

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: ready)

    bridge.shutdown()
    assert closed == [True]


def test_shutdown_before_start_is_a_safe_noop(qapp, loop_thread, aida_home: Path, records_home: Path):
    bridge = ChatBridge(loop_thread)
    bridge.shutdown()  # must not raise, must not hang


def test_cancel_forwards_to_the_session(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    """``ChatBridge.cancel`` (wired to InputBox's Stop button in
    MainWindow) is a thin synchronous forward to ``ChatSession.cancel`` —
    the actual mid-stream interruption semantics belong to
    ``aida.core.agent.AgentLoop`` and are covered by its own tests from
    earlier phases; what's specific to Phase 5 is that the GUI's wiring
    actually reaches it, which is what this pins down."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings_with_profile()

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: ready)

    cancelled = []
    monkeypatch.setattr(bridge.session, "cancel", lambda: cancelled.append(True))
    bridge.cancel()
    assert cancelled == [True]

    bridge.shutdown()


def test_cancel_before_session_ready_is_a_safe_noop(qapp, loop_thread, aida_home: Path, records_home: Path):
    bridge = ChatBridge(loop_thread)
    bridge.cancel()  # must not raise even with no session yet
