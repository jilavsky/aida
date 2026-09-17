"""A small cross-process, non-blocking advisory lock over
``~/.aida/scheduler.lock`` (planning/phase10_scheduling_design.md §7): at
most one process's scheduler loop may actually execute a due run at a time,
even when a GUI instance and an ``aida schedule watch`` CLI instance are
both pointed at the same ``~/.aida``.

Deliberately non-blocking and re-acquired every tick rather than held for
the scheduler's whole lifetime: the scheduler already polls every N
seconds (``aida.core.scheduler_runtime``), so "try, and skip this tick if
someone else holds it" fits that model far better than a long-held lock a
crashed process could leave stuck — ``flock``/``LK_NBLCK`` are released by
the OS the moment the holding process's file descriptor closes (including
on a crash), so there is no stale-lock cleanup to write.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from aida.config.paths import scheduler_lock_path

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def try_acquire_scheduler_lock(path: Path | None = None) -> Iterator[bool]:
    """Yields ``True`` if the lock was acquired for the duration of the
    ``with`` block (the caller may run its due schedules this tick),
    ``False`` if another process holds it right now (the caller should
    skip this tick entirely rather than wait). Always releases whatever it
    acquired on the way out, even on an exception inside the block."""
    lock_path = path or scheduler_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        acquired = _try_lock(fh)
        try:
            yield acquired
        finally:
            if acquired:
                _unlock(fh)


class ConfigLockTimeoutError(RuntimeError):
    """Raised when another process held a config file's write lock for
    longer than the timeout. Should be rare in practice — a config save is a
    fast atomic-write, not a long-held open — so this signals something
    genuinely stuck rather than ordinary contention."""


@contextlib.contextmanager
def config_write_lock(config_path: Path, *, timeout: float = 2.0) -> Iterator[None]:
    """Cross-process lock guarding writes to a single ``~/.aida`` config
    file (``mcp.json``, ``schedules.yaml``, etc.), so two AIDA instances
    saving the same file around the same moment are serialized rather than
    racing. Unlike ``try_acquire_scheduler_lock`` (non-blocking, "skip this
    tick" semantics), a config save is a discrete user-initiated action that
    should briefly wait for the other instance's save to finish rather than
    silently drop the user's edit — hence blocking-with-timeout instead of
    skip. The lock file lives next to the file it guards
    (``mcp.json.lock``), matching how ``scheduler.lock`` lives next to
    ``aida.db``."""
    lock_path = config_path.with_name(config_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with lock_path.open("a+") as fh:
        acquired = _try_lock(fh)
        while not acquired:
            if time.monotonic() >= deadline:
                raise ConfigLockTimeoutError(
                    f"timed out waiting for the write lock on {config_path}"
                )
            time.sleep(0.05)
            acquired = _try_lock(fh)
        try:
            yield
        finally:
            _unlock(fh)


__all__ = ["ConfigLockTimeoutError", "config_write_lock", "try_acquire_scheduler_lock"]
