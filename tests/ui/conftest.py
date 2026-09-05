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


#: Every call that blocks in Qt's own C++ event loop waiting for a human.
#: A test that reaches one of these does not fail — it *hangs*, and it hangs
#: in a way `timeout = 30` cannot rescue: pytest-timeout raises in the
#: Python main thread, which is exactly the thread parked inside a native
#: modal, so the exception is not seen until the modal returns. Which it
#: never does, because nobody is going to click it.
#:
#: This bit once: adding one entry to the sidebar's context menu made a
#: previously-safe test path pop a real dialog, and the suite sat for ten
#: minutes with no indication of which test or why. Turning that into an
#: immediate, named failure is worth far more than the modal ever was.
_BLOCKING_CALLS = [
    ("QDialog", "exec"),
    ("QMenu", "exec"),
    ("QMessageBox", "exec"),
    ("QMessageBox", "question"),
    ("QMessageBox", "information"),
    ("QMessageBox", "warning"),
    ("QMessageBox", "critical"),
    ("QInputDialog", "getText"),
    ("QInputDialog", "getInt"),
    ("QFileDialog", "getExistingDirectory"),
    ("QFileDialog", "getOpenFileName"),
    ("QFileDialog", "getSaveFileName"),
]


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    """Make a real modal an instant failure instead of a hung suite.

    Applied before each test, so a test's own ``monkeypatch.setattr`` of the
    same call still wins — this only catches the paths nobody thought to
    stub. The message names the call, which is the piece of information a
    hang gives you none of.
    """
    from aida.ui.qt import _qt

    for class_name, method in _BLOCKING_CALLS:
        target = getattr(_qt, class_name, None)
        if target is None or not hasattr(target, method):
            continue

        def _refuse(*_args, __where=f"{class_name}.{method}", **_kwargs):
            raise AssertionError(
                f"{__where} was called for real in a test. It would block on human input "
                f"and hang the suite. Stub it with monkeypatch.setattr, or call the "
                f"handler directly instead of going through the dialog."
            )

        monkeypatch.setattr(target, method, _refuse, raising=False)
    return None
