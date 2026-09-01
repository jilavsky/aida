"""Subprocess execution for Phase 9's ``run_python_script``/``run_command``
native tools (``aida.coding.tools``).

Genuinely new code — no existing "kill a runaway subprocess on timeout"
implementation exists anywhere else in ``aida`` (``aida.mcp.server``'s
``asyncio.wait_for``-wrapped calls time out an already-managed MCP session,
not a bare process; ``aida.workspace.files``'s ``_run_blocking`` bounds a
thread-pool call, which ``asyncio.to_thread`` can't cancel underneath it).
Never ``shell=True`` (PLAN.md's explicit rule) — ``asyncio.create_subprocess_exec``
takes an argv list, so there's no shell to inject into in the first place.

Two properties this module is responsible for, both of which a plain
``create_subprocess_exec`` + ``proc.kill()`` does *not* give you:

**Stop must stop the whole tree.** Every run is launched in its own process
group (POSIX) or job object (Windows), and Stop/timeout terminates the group
rather than the one process that was launched. Killing only the direct child
left anything it had spawned — a ``multiprocessing`` pool, a plotting
backend, a ``subprocess`` of its own — running with no UI affordance left to
stop it and no record that it existed.

**Output must be bounded.** ``communicate()`` buffers both pipes with no cap
and returns only at exit, so a script printing inside a loop could grow the
capture until the host ran out of memory, and a script killed on timeout
returned *nothing* — the output that would have explained why it hung was
discarded along with it. Both streams are now drained incrementally into a
bounded buffer that keeps the head and the tail, and whatever was captured
before a kill is returned.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RUN_TIMEOUT_SECONDS = 30.0

#: Bytes retained per stream (stdout and stderr each). Beyond this the
#: middle is dropped and a marker inserted — see ``_BoundedCapture``. Sized
#: so a normally chatty script is captured verbatim while a runaway loop
#: cannot grow the process's memory: the model would not read a megabyte of
#: repeated output anyway, and the useful parts of a long log are almost
#: always its beginning (what it set out to do) and its end (where it went
#: wrong).
MAX_CAPTURED_BYTES = 256_000

#: How long a terminated process group is given to exit on its own before
#: being killed outright. SIGTERM first lets a script's own cleanup
#: (``finally`` blocks, temp-file removal, a partially written output file
#: being closed) run; SIGKILL after this is the guarantee that Stop stops.
TERMINATE_GRACE_SECONDS = 3.0


@dataclass
class RunResult:
    """One completed (or killed) run.

    ``stdout``/``stderr`` hold whatever was captured *before* the process
    ended, including on a timeout or a Stop — they used to be returned empty
    in those cases, which threw away the output most likely to explain the
    hang. ``output_truncated`` says the capture hit ``MAX_CAPTURED_BYTES``
    and dropped the middle of at least one stream."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    duration_seconds: float
    output_truncated: bool = False


class _BoundedCapture:
    """Accumulates a stream, keeping at most ``limit`` bytes.

    Retains the first and last halves and drops the middle, with a marker
    saying how much went missing — a plain "first N bytes" cap loses the
    traceback, and a plain "last N bytes" one loses the setup that led to
    it."""

    def __init__(self, limit: int = MAX_CAPTURED_BYTES) -> None:
        self._limit = limit
        self._head = bytearray()
        self._tail = bytearray()
        self._dropped = 0

    @property
    def truncated(self) -> bool:
        return self._dropped > 0

    def feed(self, chunk: bytes) -> None:
        half = self._limit // 2
        if len(self._head) < half:
            take = half - len(self._head)
            self._head.extend(chunk[:take])
            chunk = chunk[take:]
        if not chunk:
            return
        self._tail.extend(chunk)
        if len(self._tail) > half:
            overflow = len(self._tail) - half
            del self._tail[:overflow]
            self._dropped += overflow

    def text(self) -> str:
        if not self._dropped:
            return bytes(self._head + self._tail).decode("utf-8", errors="replace")
        marker = f"\n... [{self._dropped} bytes of output omitted — capture limited to {self._limit} bytes] ...\n"
        return (
            bytes(self._head).decode("utf-8", errors="replace")
            + marker
            + bytes(self._tail).decode("utf-8", errors="replace")
        )


async def _drain(stream: asyncio.StreamReader | None, capture: _BoundedCapture) -> None:
    """Read one pipe to EOF into a bounded buffer.

    Draining *concurrently* (rather than via ``communicate()`` at the end)
    is what makes the capture bounded in memory and what leaves usable
    output behind when a run is killed."""
    if stream is None:
        return
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            return
        capture.feed(chunk)


def _process_group_kwargs() -> dict[str, object]:
    """Launch flags that put the child in its own process group / job
    object, so the whole tree can be signalled as a unit.

    POSIX: ``start_new_session=True`` calls ``setsid()`` in the child, making
    it a session and group leader — ``os.killpg`` then reaches everything it
    spawns. Windows: ``CREATE_NEW_PROCESS_GROUP`` is the closest equivalent
    available without a job-object dependency; ``Process.kill()`` on Windows
    maps to ``TerminateProcess``, which does not walk the tree, so the group
    flag is what lets a ``taskkill /T`` fallback work."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def _terminate_tree(proc: asyncio.subprocess.Process) -> None:
    """End ``proc`` and everything it spawned: signal the group, give it a
    moment to unwind, then kill what is left.

    Every step is best-effort. A process that exited between the check and
    the signal raises ``ProcessLookupError``; a platform that refuses the
    group signal falls back to the single process, which is still strictly
    better than not trying."""
    if proc.returncode is not None:
        return

    if sys.platform == "win32":
        # No killpg on Windows. taskkill /T /F walks the tree; if it is
        # unavailable, fall back to killing the process itself.
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=TERMINATE_GRACE_SECONDS)
        except (OSError, TimeoutError):
            with _suppress_process_gone():
                proc.kill()
        with _suppress_process_gone():
            await proc.wait()
        return

    group_signalled = False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        group_signalled = True
    except (ProcessLookupError, PermissionError, OSError):
        with _suppress_process_gone():
            proc.terminate()

    # Give the tree a chance to shut down cleanly before forcing it.
    try:
        await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SECONDS)
        return
    except TimeoutError:
        pass

    if group_signalled:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            with _suppress_process_gone():
                proc.kill()
    else:
        with _suppress_process_gone():
            proc.kill()
    with _suppress_process_gone():
        await proc.wait()


def _suppress_process_gone():
    """Every step of tearing a process tree down races the tree exiting on
    its own; a process that is already gone is a success, not an error."""
    return contextlib.suppress(ProcessLookupError, OSError)


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
    something to act on — this function's own timeout handling doesn't need
    it, since it already holds the same reference locally.

    A caller that kills the process directly still only reaches the one
    process; ``ChatBridge`` calls ``terminate_run`` (below) instead, which
    takes down the group."""
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_process_group_kwargs(),
    )
    if on_started is not None:
        on_started(proc)

    out_capture = _BoundedCapture()
    err_capture = _BoundedCapture()
    # Started before the wait so both pipes are drained continuously: a
    # process that fills a pipe buffer would otherwise block forever on its
    # own write, and the capture would grow without bound if it did not.
    drains = asyncio.gather(
        _drain(proc.stdout, out_capture), _drain(proc.stderr, err_capture), return_exceptions=True
    )

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        await _terminate_tree(proc)

    # Whatever the readers managed to collect is kept either way — this is
    # what makes a timed-out run's partial output available instead of the
    # empty strings the old communicate()-then-kill path returned.
    try:
        await asyncio.wait_for(drains, timeout=TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        drains.cancel()

    return RunResult(
        stdout=out_capture.text(),
        stderr=err_capture.text(),
        returncode=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        duration_seconds=time.monotonic() - start,
        output_truncated=out_capture.truncated or err_capture.truncated,
    )


async def terminate_run(proc: asyncio.subprocess.Process) -> None:
    """Stop a run started by ``run_subprocess``, including anything it
    spawned. This is what a Stop button should call: ``proc.kill()`` alone
    reaches only the process that was launched, leaving its children behind
    with nothing left in the UI able to stop them."""
    await _terminate_tree(proc)


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


__all__ = [
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "MAX_CAPTURED_BYTES",
    "RunResult",
    "run_python_script",
    "run_subprocess",
    "terminate_run",
]
