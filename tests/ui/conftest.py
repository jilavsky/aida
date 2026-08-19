"""Fixtures shared by tests/ui/*.

Two things every GUI test needs, set up once here rather than per-file:

1. ``QT_QPA_PLATFORM=offscreen`` — no real display needed (works in CI and
   this sandbox alike); must be set before any ``QApplication`` is
   constructed, which is why it happens at conftest *import* time (pytest
   imports conftest.py before collecting/running any test in this
   directory), not inside a fixture body.
2. A single process-wide ``QApplication`` — Qt does not allow more than one,
   so it's created once (session-scoped) and reused by every test.

If PySide6 isn't installed (e.g. someone ran plain ``pip install -e ".[dev]"``
without the ``gui`` extra), every test under ``tests/ui`` is skipped with a
clear reason instead of erroring collection for the whole suite.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pyside6 = pytest.importorskip("PySide6", reason="PySide6 not installed — `pip install -e '.[gui]'` to run GUI tests")

from aida.ui.qt._qt import QApplication  # noqa: E402 (must follow the importorskip)


@pytest.fixture(scope="session")
def qapp():
    """The one ``QApplication`` for the whole test session."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def loop_thread():
    """A fresh, started ``AsyncLoopThread`` per test — cheap enough (a
    thread + an asyncio loop) to not bother sharing, and per-test isolation
    means one test's leftover scheduled work can never bleed into another's."""
    from aida.ui.qt.bridge import AsyncLoopThread

    thread = AsyncLoopThread()
    thread.start()
    thread.wait_until_ready()
    yield thread
    thread.stop()
