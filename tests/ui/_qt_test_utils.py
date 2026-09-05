"""Small shared helpers for tests/ui/* — not a conftest.py (so tests can
``from tests.ui._qt_test_utils import pump_until`` explicitly) but only
ever imported by test files, never by ``src/aida``."""

from __future__ import annotations

import time
from collections.abc import Callable


def pump_until(
    qapp, predicate: Callable[[], bool], timeout: float = 15.0, interval: float = 0.01
) -> bool:
    """Process Qt events (including queued cross-thread signal delivery)
    until ``predicate()`` is true or ``timeout`` seconds pass. Returns
    whether ``predicate()`` ended up true. This project has no pytest-qt
    dependency (PLAN.md's "no additions without demonstrated need") — this
    is the plain-Qt equivalent of ``qtbot.waitSignal``, sufficient for tests
    that just need to wait for a background-thread signal to land.

    CI flake (Windows, Python 3.11): a scheduler-bridge test timed out at
    the previous 5s default even though the tick it was waiting on (start
    a due schedule -> AsyncLoopThread -> real background asyncio work ->
    a queued cross-thread signal back to Qt) had genuinely nothing slow in
    it — the teardown log showed the tick finally completing *after* the
    test had already failed and its fixtures had started unwinding.
    Windows CI runners are measurably slower/more variable for thread and
    first-touch asyncio setup than Linux/macOS ones; 5s left too little
    margin for that. Raised well above what any of these tests need in
    the normal case — a passing test still returns the moment its
    predicate goes true, so this only buys slow CI more rope, never slows
    down a healthy run."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    qapp.processEvents()
    return predicate()
