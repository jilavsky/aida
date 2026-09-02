from __future__ import annotations

from pathlib import Path

from aida.core.proc_lock import try_acquire_scheduler_lock


def test_acquires_when_unheld(tmp_path: Path):
    lock_path = tmp_path / "scheduler.lock"
    with try_acquire_scheduler_lock(lock_path) as acquired:
        assert acquired is True


def test_second_concurrent_attempt_fails(tmp_path: Path):
    """Two independent open()s of the same lock file — modeling a GUI
    instance and a separate `aida schedule watch` process both pointed at
    the same ~/.aida — must not both believe they hold it."""
    lock_path = tmp_path / "scheduler.lock"
    with try_acquire_scheduler_lock(lock_path) as first:
        assert first is True
        with try_acquire_scheduler_lock(lock_path) as second:
            assert second is False


def test_lock_is_released_after_the_with_block(tmp_path: Path):
    lock_path = tmp_path / "scheduler.lock"
    with try_acquire_scheduler_lock(lock_path) as first:
        assert first is True

    with try_acquire_scheduler_lock(lock_path) as second:
        assert second is True


def test_lock_released_even_if_the_block_raises(tmp_path: Path):
    lock_path = tmp_path / "scheduler.lock"
    try:
        with try_acquire_scheduler_lock(lock_path) as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    with try_acquire_scheduler_lock(lock_path) as second:
        assert second is True


def test_creates_parent_directory_if_missing(tmp_path: Path):
    lock_path = tmp_path / "nested" / "dir" / "scheduler.lock"
    with try_acquire_scheduler_lock(lock_path) as acquired:
        assert acquired is True
    assert lock_path.exists()
