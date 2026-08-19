"""Logging configuration: rotating file log + console handler.

Groundwork for "which layer failed" diagnostics (PLAN.md §7, §11): every
logger name is namespaced by subsystem (``aida.provider``, ``aida.mcp``,
``aida.core``, ``aida.ui``, ...) and the formatter includes that name, so a
log line always identifies which layer produced it.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from aida.config.paths import logs_dir

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_FILENAME = "aida.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False


def configure_logging(level: str = "INFO", *, log_dir: Path | None = None) -> Path:
    """Configure the ``aida`` logger tree once per process.

    Safe to call more than once (e.g. once from config defaults, once after
    the user's real config loads with a different level) — later calls just
    adjust the level rather than duplicating handlers.
    """
    global _configured

    root = logging.getLogger("aida")
    root.setLevel(level.upper())

    target_dir = log_dir or logs_dir()
    log_path = target_dir / LOG_FILENAME

    if not _configured:
        formatter = logging.Formatter(LOG_FORMAT)

        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

        _configured = True

    return log_path


def get_logger(subsystem: str) -> logging.Logger:
    """Return a logger namespaced under ``aida.<subsystem>``.

    ``subsystem`` should be one of the recognized layer tags: ``provider``,
    ``mcp``, ``core``, ``ui``, ``config``, ``cli``, ``workspace``, ``docs``.
    """
    return logging.getLogger(f"aida.{subsystem}")
