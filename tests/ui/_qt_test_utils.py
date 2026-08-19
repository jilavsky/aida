"""Small shared helpers for tests/ui/* — not a conftest.py (so tests can
``from tests.ui._qt_test_utils import pump_until`` explicitly) but only
ever imported by test files, never by ``src/aida``."""

from __future__ import annotations

import time
from collections.abc import Callable


def pump_until(qapp, predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01) -> bool:
    """Process Qt events (including queued cross-thread signal delivery)
    until ``predicate()`` is true or ``timeout`` seconds pass. Returns
    whether ``predicate()`` ended up true. This project has no pytest-qt
    dependency (PLAN.md's "no additions without demonstrated need") — this
    is the plain-Qt equivalent of ``qtbot.waitSignal``, sufficient for tests
    that just need to wait for a background-thread signal to land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    qapp.processEvents()
    return predicate()
