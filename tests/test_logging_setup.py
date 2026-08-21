"""Tests for aida.config.logging_setup.

Review finding: ``root.setLevel(level.upper())`` raised ``ValueError`` on a
typo'd ``log_level:`` in config.yaml. That happens during startup, before
any handler exists, so the app simply refused to launch — with a traceback
and no hint that one line of YAML was the cause. It also contradicts this
project's "old configs must always load" rule (aida.config.settings's
docstring): a bad *value* deserves the same treatment as a stale one.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aida.config import logging_setup
from aida.config.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_logger_state():
    root = logging.getLogger("aida")
    previous_level = root.level
    previous_configured = logging_setup._configured
    yield
    root.setLevel(previous_level)
    logging_setup._configured = previous_configured


def test_a_valid_level_is_applied(tmp_path: Path):
    configure_logging("DEBUG", log_dir=tmp_path)
    assert logging.getLogger("aida").level == logging.DEBUG


def test_a_lowercase_level_still_works(tmp_path: Path):
    configure_logging("warning", log_dir=tmp_path)
    assert logging.getLogger("aida").level == logging.WARNING


def test_a_typo_falls_back_to_info_instead_of_raising(tmp_path: Path):
    configure_logging("DEBGU", log_dir=tmp_path)
    assert logging.getLogger("aida").level == logging.INFO


def test_a_typo_is_warned_about(tmp_path: Path, caplog):
    with caplog.at_level(logging.WARNING, logger="aida.config"):
        configure_logging("VERBOSE", log_dir=tmp_path)
    assert any("VERBOSE" in record.getMessage() for record in caplog.records)


def test_configure_logging_returns_the_log_path(tmp_path: Path):
    path = configure_logging("INFO", log_dir=tmp_path)
    assert path == tmp_path / logging_setup.LOG_FILENAME
