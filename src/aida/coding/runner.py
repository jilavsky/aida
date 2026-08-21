"""Subprocess execution for Phase 9's ``run_python_script``/``run_command``
native tools (``aida.coding.tools``).

Genuinely new code — no existing "kill a runaway subprocess on timeout"
implementation exists anywhere else in ``aida`` (``aida.mcp.server``'s
``asyncio.wait_for``-wrapped calls time out an already-managed MCP session,
not a bare process; ``aida.workspace.files``'s ``_run_blocking`` bounds a
thread-pool call, which ``asyncio.to_thread`` can't cancel underneath it).
Never ``shell=True`` (PLAN.md's explicit rule) — ``asyncio.create_subprocess_exec``
takes an argv list, so there's no shell to inject into in the first place.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RUN_TIMEOUT_SECONDS = 30.0


@dataclass
class RunResult:
    """One completed (or killed) run. ``stdout``/``stderr`` are empty on a
    timeout — the process is killed mid-``communicate()``, and whatever it
    had already written is not reliably recoverable at that point; the
    caller already has ``timed_out=True`` to explain why."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    duration_seconds: float


async def run_subprocess(
    argv: list[str],
    *,
    cwd: str | Path,
    timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    on_started: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> RunResult:
    """``on_started`` (Phase 9 GUI Kill button): called with the live
    ``Process`` the moment it's launched, so a caller that needs to cancel
    a run *before* the timeout (``ChatBridge.cancel_script_run``) has
    something to call ``.kill()`` on — this function's own timeout handling
    doesn't need it, since it already holds the same reference locally."""
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if on_started is not None:
        on_started(proc)
    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        stdout_bytes, stderr_bytes = b"", b""
        timed_out = True

    return RunResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        returncode=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        duration_seconds=time.monotonic() - start,
    )


async def run_python_script(
    path: str | Path,
    args: list[str] | None = None,
    *,
    interpreter: str | None = None,
    cwd: str | Path,
    timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    on_started: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> RunResult:
    """``interpreter`` is a direct path to a python executable (a
    workspace's ``python_interpreter``, e.g. a conda/venv env's own
    ``bin/python``) — ``None`` falls back to ``sys.executable`` (whatever
    AIDA's own process is running under)."""
    argv = [interpreter or sys.executable, str(path), *(args or [])]
    return await run_subprocess(argv, cwd=cwd, timeout=timeout, on_started=on_started)


__all__ = ["DEFAULT_RUN_TIMEOUT_SECONDS", "RunResult", "run_python_script", "run_subprocess"]
