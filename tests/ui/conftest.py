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

import gc
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


@pytest.fixture(autouse=True)
def _drain_qt_garbage_after_each_test(qapp):
    """Deterministically collect and drain whatever this test's widget
    tree leaves behind, right after the test — instead of leaving it to
    Python's GC to reap whenever it happens to run.

    None of the ~50+ tests that build a real ``MainWindow`` (or a bare
    ``MessageBubble`` with its own single-shot ``_render_timer`` — see
    ``aida.ui.qt.chat_panel``) tear it down via ``deleteLater()``; most
    just let the local variable go out of scope at the end of the test
    function. Every GUI test in this directory shares one process-wide
    ``QApplication`` (Qt disallows more than one), so a widget tree
    collected by Python's *cyclic* GC — which does not run on every
    refcount drop, only once allocation counters cross its generational
    thresholds — can be destroyed many tests later, during a *different*
    test's ``qapp.processEvents()`` call. If that widget still had an
    active ``QTimer`` registered with the event dispatcher at the moment
    the cyclic collector finally reaps it, the now-freed QObject can still
    have a pending timer entry in Qt's internal timer list; the next timer
    tick after that delivers the event to freed memory. That is a native
    crash — a segfault on Linux, an access violation on Windows — not a
    Python exception, so nothing catches it: this reproduced repeatedly as
    a `Fatal Python error: Segmentation fault` inside
    `QCoreApplication::notifyInternal2`/`QTimerInfoList::activateTimers`,
    always several hundred tests into a run combining ``tests/`` and
    ``tests/ui`` in one process (see
    planning/improvement_plan_2026-08.md's "Known issues" section), never
    when a handful of files ran alone — consistent with needing enough
    accumulated garbage to cross the cyclic collector's threshold.

    Forcing a collection pass plus a couple of ``processEvents()`` calls
    right after every test means each test's own garbage is reaped (and
    any ``deleteLater()``-queued deletions actually run) inside a window
    where nothing else is mid-dispatch, instead of surviving to detonate
    during some later, unrelated test."""
    yield
    gc.collect()
    qapp.processEvents()
    gc.collect()
    qapp.processEvents()


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
