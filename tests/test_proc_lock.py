from __future__ import annotations

from pathlib import Path

import pytest

from aida.core.proc_lock import (
    ConfigLockTimeoutError,
    config_write_lock,
    try_acquire_scheduler_lock,
)


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


# --- config_write_lock ---------------------------------------------------


def test_config_lock_acquires_immediately_when_unheld(tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    with config_write_lock(config_path):
        pass  # no TimeoutError means it acquired well within the timeout


def test_config_lock_lives_next_to_the_config_file(tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    with config_write_lock(config_path):
        assert (tmp_path / "mcp.json.lock").exists()


def test_config_lock_times_out_when_already_held(tmp_path: Path):
    """A single-threaded nested acquire can't ever succeed (the outer
    ``with`` only releases after the inner one returns), so this exercises
    the timeout path the same way two real processes racing for the same
    save would — just deterministically, with a short timeout."""
    config_path = tmp_path / "mcp.json"
    with (
        config_write_lock(config_path),
        pytest.raises(ConfigLockTimeoutError),
        config_write_lock(config_path, timeout=0.2),
    ):
        pass


def test_config_lock_released_after_the_with_block(tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    with config_write_lock(config_path):
        pass
    with config_write_lock(config_path, timeout=0.2):
        pass  # would time out if the first block leaked the lock


def test_config_lock_released_even_if_the_block_raises(tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    try:
        with config_write_lock(config_path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with config_write_lock(config_path, timeout=0.2):
        pass
