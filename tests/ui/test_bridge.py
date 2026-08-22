"""Tests for aida.ui.qt.bridge.ChatBridge — the async-generator-to-Qt-signal
adapter every widget builds on. Uses a real background asyncio loop thread
(AsyncLoopThread) and a real MockProvider, same as the CLI's own tests —
only the "is this delivered as a Qt signal on the right thread" part is
new here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aida.config.settings import ProviderProfile, load_settings
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from aida.ui.qt.bridge import ChatBridge
from aida.workspace.safety import ConfirmationRequest
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


# --- confirmation_requested (Phase 6 SafetyGuard bridging) -------------------


def test_confirm_bridges_signal_to_a_resolvable_future(qapp, loop_thread, aida_home: Path, records_home: Path):
    """``ChatBridge._confirm`` runs on the background asyncio thread; this
    pins down that emitting confirmation_requested delivers (request,
    future) to a Qt-thread receiver, and that resolving the plain
    concurrent.futures.Future there is what unblocks the awaiting
    coroutine — without going through a real tool call at all."""
    bridge = ChatBridge(loop_thread)
    received = []
    bridge.confirmation_requested.connect(lambda request, future: received.append((request, future)))

    request = ConfirmationRequest(action="write", path="/tmp/x", detail="Write /tmp/x?")
    outer_future = asyncio.run_coroutine_threadsafe(bridge._confirm(request), loop_thread.loop)

    assert pump_until(qapp, lambda: received), "confirmation_requested never fired"
    inner_request, inner_future = received[0]
    assert inner_request is request

    inner_future.set_result(True)
    assert pump_until(qapp, lambda: outer_future.done())
    assert outer_future.result(timeout=1) is True


def test_confirm_denial_resolves_false(qapp, loop_thread, aida_home: Path, records_home: Path):
    bridge = ChatBridge(loop_thread)
    received = []
    bridge.confirmation_requested.connect(lambda request, future: received.append((request, future)))

    request = ConfirmationRequest(action="delete", path="/tmp/x", detail="Delete /tmp/x?")
    outer_future = asyncio.run_coroutine_threadsafe(bridge._confirm(request), loop_thread.loop)
    assert pump_until(qapp, lambda: received)

    received[0][1].set_result(False)
    assert pump_until(qapp, lambda: outer_future.done())
    assert outer_future.result(timeout=1) is False


def test_start_defaults_confirm_callback_to_bridge_confirm(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch, tmp_path: Path
):
    """End-to-end: a write_file tool call to a path outside the (empty)
    allowed-folders set triggers SafetyGuard's confirmation, which — because
    ChatBridge.start defaults confirm_callback to self._confirm — surfaces
    as confirmation_requested rather than silently denying (the CLI's
    deny_all-less default) or hanging."""
    target = tmp_path / "note.txt"
    monkeypatch.setattr(
        "aida.cli.chat.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(
                    tool_calls=[
                        MockToolCall(
                            name="write_file",
                            id="call_1",
                            arguments={"path": str(target), "content": "hi"},
                        )
                    ]
                ),
                MockTurn(text="done"),
            ]
        ),
    )
    settings = _settings_with_profile()

    bridge = ChatBridge(loop_thread)
    ready = []
    bridge.session_ready.connect(lambda: ready.append(True))
    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: ready)

    confirmations = []

    def _approve(request, future):
        confirmations.append(request)
        future.set_result(True)

    bridge.confirmation_requested.connect(_approve)

    finished = []
    bridge.turn_finished.connect(lambda: finished.append(True))
    bridge.send("please write the file")
    assert pump_until(qapp, lambda: finished), "turn_finished never fired"

    assert len(confirmations) == 1
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hi"

    bridge.shutdown()


# --- run_script / cancel_script_run (Phase 9 code editor) -------------------


def test_run_script_emits_finished_with_a_real_result(qapp, loop_thread, tmp_path: Path):
    script = tmp_path / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")

    bridge = ChatBridge(loop_thread)
    finished = []
    bridge.script_run_finished.connect(finished.append)
    bridge.run_script(str(script), [], interpreter=None, cwd=str(tmp_path), timeout=10.0)

    assert pump_until(qapp, lambda: finished)
    assert not finished[0].timed_out
    assert "hi" in finished[0].stdout


def test_cancel_script_run_kills_a_sleeping_script(qapp, loop_thread, tmp_path: Path):
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")

    bridge = ChatBridge(loop_thread)
    finished = []
    bridge.script_run_finished.connect(finished.append)
    bridge.run_script(str(script), [], interpreter=None, cwd=str(tmp_path), timeout=30.0)

    # Give the subprocess a moment to actually start before killing it.
    assert pump_until(qapp, lambda: bridge._running_script_proc is not None, timeout=5.0)
    bridge.cancel_script_run()

    assert pump_until(qapp, lambda: finished, timeout=10.0)
    assert finished[0].returncode != 0


def test_cancel_script_run_with_nothing_running_is_a_safe_noop(qapp, loop_thread):
    bridge = ChatBridge(loop_thread)
    bridge.cancel_script_run()  # must not raise


# --- provider/embedding profile validation (U2 "Test" button) ---------------


def test_validate_provider_profile_emits_profile_validated(qapp, loop_thread, monkeypatch):
    from aida.config.settings import ProviderProfile
    from aida.providers.profiles import ProfileValidation

    async def fake_validate_profile(profile, *, timeout=10.0):
        return ProfileValidation(name=profile.name, ok=True, detail="reachable (fake)")

    monkeypatch.setattr("aida.ui.qt.bridge.validate_profile", fake_validate_profile)

    bridge = ChatBridge(loop_thread)
    results = []
    bridge.profile_validated.connect(lambda name, result: results.append((name, result)))

    bridge.validate_provider_profile(ProviderProfile(name="argo-claude", kind="anthropic", model="claude-x"))

    assert pump_until(qapp, lambda: results)
    name, result = results[0]
    assert name == "argo-claude"
    assert result.ok
    assert result.detail == "reachable (fake)"


def test_validate_provider_profile_surfaces_a_failed_ping(qapp, loop_thread, monkeypatch):
    from aida.config.settings import ProviderProfile
    from aida.providers.profiles import ProfileValidation

    async def fake_validate_profile(profile, *, timeout=10.0):
        return ProfileValidation(name=profile.name, ok=False, detail="not reachable (fake)")

    monkeypatch.setattr("aida.ui.qt.bridge.validate_profile", fake_validate_profile)

    bridge = ChatBridge(loop_thread)
    results = []
    bridge.profile_validated.connect(lambda name, result: results.append((name, result)))

    bridge.validate_provider_profile(ProviderProfile(name="local", kind="openai_compat", model="llama"))

    assert pump_until(qapp, lambda: results)
    assert results[0][1].ok is False


def test_validate_embedding_provider_profile_emits_embedding_profile_validated(qapp, loop_thread, monkeypatch):
    from aida.config.settings import EmbeddingProfile
    from aida.providers.profiles import ProfileValidation

    async def fake_validate_embedding_profile(profile, *, timeout=10.0):
        return ProfileValidation(name=profile.name, ok=True, detail="reachable (fake)")

    monkeypatch.setattr("aida.ui.qt.bridge.validate_embedding_profile", fake_validate_embedding_profile)

    bridge = ChatBridge(loop_thread)
    results = []
    bridge.embedding_profile_validated.connect(lambda name, result: results.append((name, result)))

    bridge.validate_embedding_provider_profile(EmbeddingProfile(name="local-embed", kind="openai_compat", model="nomic"))

    assert pump_until(qapp, lambda: results)
    name, result = results[0]
    assert name == "local-embed"
    assert result.ok
