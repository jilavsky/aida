from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aida.coding.runner import run_python_script, run_subprocess


@pytest.mark.asyncio
async def test_run_subprocess_captures_stdout_and_returncode(tmp_path: Path):
    result = await run_subprocess([sys.executable, "-c", "print('hi')"], cwd=tmp_path, timeout=10.0)
    assert result.returncode == 0
    assert "hi" in result.stdout
    assert not result.timed_out


@pytest.mark.asyncio
async def test_run_subprocess_captures_stderr(tmp_path: Path):
    result = await run_subprocess(
        [sys.executable, "-c", "import sys; sys.stderr.write('oops')"], cwd=tmp_path, timeout=10.0
    )
    assert "oops" in result.stderr


@pytest.mark.asyncio
async def test_run_subprocess_surfaces_nonzero_returncode(tmp_path: Path):
    result = await run_subprocess(
        [sys.executable, "-c", "import sys; sys.exit(3)"], cwd=tmp_path, timeout=10.0
    )
    assert result.returncode == 3
    assert not result.timed_out


@pytest.mark.asyncio
async def test_run_subprocess_kills_a_runaway_process_on_timeout(tmp_path: Path):
    result = await run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=0.2
    )
    assert result.timed_out is True
    assert result.returncode != 0


@pytest.mark.asyncio
async def test_run_subprocess_on_started_receives_the_live_process(tmp_path: Path):
    """Phase 9 GUI Kill button: ChatBridge needs a live Process handle to
    cancel a run before its timeout — on_started is how it gets one."""
    seen = []
    result = await run_subprocess(
        [sys.executable, "-c", "print('hi')"], cwd=tmp_path, timeout=10.0, on_started=seen.append
    )
    assert len(seen) == 1
    assert seen[0].pid is not None
    assert not result.timed_out


@pytest.mark.asyncio
async def test_run_subprocess_reports_duration(tmp_path: Path):
    result = await run_subprocess([sys.executable, "-c", "pass"], cwd=tmp_path, timeout=10.0)
    assert result.duration_seconds >= 0.0


@pytest.mark.asyncio
async def test_run_subprocess_uses_given_cwd(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("hello", encoding="utf-8")
    result = await run_subprocess(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path('marker.txt').read_text())"],
        cwd=tmp_path,
        timeout=10.0,
    )
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_run_python_script_runs_the_given_script_with_args(tmp_path: Path):
    script = tmp_path / "echo_args.py"
    script.write_text("import sys; print(' '.join(sys.argv[1:]))", encoding="utf-8")
    result = await run_python_script(script, ["a", "b"], cwd=tmp_path, timeout=10.0)
    assert result.returncode == 0
    assert "a b" in result.stdout


@pytest.mark.asyncio
async def test_run_python_script_defaults_to_sys_executable(tmp_path: Path):
    script = tmp_path / "print_version.py"
    script.write_text("import sys; print(sys.version_info[0])", encoding="utf-8")
    result = await run_python_script(script, cwd=tmp_path, timeout=10.0)
    assert result.stdout.strip() == str(sys.version_info[0])
