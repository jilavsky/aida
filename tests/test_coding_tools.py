"""Tests for aida.coding.tools.default_coding_tools — mirrors
tests/test_workspace_files.py's "call tool.func(...) directly, no AgentLoop
needed" convention."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aida.coding.tools import RUN_OUTPUT_DISPLAY_MAX_CHARS, default_coding_tools
from aida.config.settings import WorkspaceConfig
from aida.workspace.command_allowlist import CommandAllowlist
from aida.workspace.safety import ConfirmationRequest, SafetyGuard


async def _approve(_request: ConfirmationRequest) -> bool:
    return True


async def _deny(_request: ConfirmationRequest) -> bool:
    return False


def _guard(
    root: Path, *, mode: str = "relaxed", allowlist: list[str] | None = None, confirm=_approve
) -> SafetyGuard:
    return SafetyGuard(
        allowed_roots=[root],
        mode=mode,
        confirm_callback=confirm,
        command_allowlist=CommandAllowlist(allowlist or []),
    )


def _workspace(root: Path, **overrides) -> WorkspaceConfig:
    defaults = dict(name="ws", target_folder=str(root))
    defaults.update(overrides)
    return WorkspaceConfig(**defaults)


async def _call(tools, name: str, **arguments):
    return await tools[name].func(arguments)


# --- registration gating ----------------------------------------------------


def test_no_tools_registered_when_workspace_is_none(tmp_path: Path):
    tools = default_coding_tools(_guard(tmp_path), workspace=None)
    assert tools == {}


def test_no_tools_registered_when_scripting_disabled(tmp_path: Path):
    workspace = _workspace(tmp_path, scripting_enabled=False)
    tools = default_coding_tools(_guard(tmp_path), workspace=workspace)
    assert tools == {}


def test_both_tools_registered_by_default(tmp_path: Path):
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path))
    assert set(tools) == {"run_python_script", "run_command"}


# --- run_python_script -------------------------------------------------------


@pytest.mark.asyncio
async def test_run_python_script_happy_path(tmp_path: Path):
    script = tmp_path / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path))

    result = await _call(tools, "run_python_script", path=str(script))
    assert not result.is_error
    assert "hi" in result.content


@pytest.mark.asyncio
async def test_run_python_script_passes_args(tmp_path: Path):
    script = tmp_path / "echo_args.py"
    script.write_text("import sys; print(sys.argv[1:])", encoding="utf-8")
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path))

    result = await _call(tools, "run_python_script", path=str(script), args=["a", "b"])
    assert "'a', 'b'" in result.content


@pytest.mark.asyncio
async def test_run_python_script_uses_workspace_interpreter(tmp_path: Path):
    script = tmp_path / "print_exe.py"
    script.write_text("import sys; print(sys.executable)", encoding="utf-8")
    workspace = _workspace(tmp_path, python_interpreter=sys.executable)
    tools = default_coding_tools(_guard(tmp_path), workspace=workspace)

    result = await _call(tools, "run_python_script", path=str(script))
    assert sys.executable in result.content


@pytest.mark.asyncio
async def test_run_python_script_outside_allowed_folder_confirms_then_runs(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    script = outside / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    tools = default_coding_tools(_guard(allowed), workspace=_workspace(allowed))

    result = await _call(tools, "run_python_script", path=str(script))
    assert not result.is_error
    assert "hi" in result.content


@pytest.mark.asyncio
async def test_run_python_script_declined_is_an_error_result(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    script = outside / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    tools = default_coding_tools(_guard(allowed, confirm=_deny), workspace=_workspace(allowed))

    result = await _call(tools, "run_python_script", path=str(script))
    assert result.is_error


@pytest.mark.asyncio
async def test_run_python_script_not_a_file_is_an_error(tmp_path: Path):
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path))
    result = await _call(tools, "run_python_script", path=str(tmp_path))
    assert result.is_error


@pytest.mark.asyncio
async def test_run_python_script_timeout_is_flagged_as_error(tmp_path: Path, monkeypatch):
    import aida.coding.tools as tools_module

    async def _fake_run(*_a, **_kw):
        from aida.coding.runner import RunResult

        return RunResult(stdout="", stderr="", returncode=-9, timed_out=True, duration_seconds=1.0)

    monkeypatch.setattr(tools_module, "_run_python_script", _fake_run)
    script = tmp_path / "sleep.py"
    script.write_text("pass", encoding="utf-8")
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path))

    result = await _call(tools, "run_python_script", path=str(script))
    assert result.is_error
    assert "TIMED OUT" in result.content


# --- B5: configurable script/command timeout --------------------------------


@pytest.mark.asyncio
async def test_run_python_script_defaults_to_workspace_timeout(tmp_path: Path, monkeypatch):
    """No ``timeout`` argument given -> falls back to
    workspace.script_timeout_seconds, not the runner's own hardcoded
    default."""
    import aida.coding.tools as tools_module

    captured: dict = {}

    async def _fake_run(*_a, **kwargs):
        from aida.coding.runner import RunResult

        captured.update(kwargs)
        return RunResult(stdout="", stderr="", returncode=0, timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(tools_module, "_run_python_script", _fake_run)
    script = tmp_path / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    workspace = _workspace(tmp_path, script_timeout_seconds=120.0)
    tools = default_coding_tools(_guard(tmp_path), workspace=workspace)

    await _call(tools, "run_python_script", path=str(script))
    assert captured["timeout"] == 120.0


@pytest.mark.asyncio
async def test_run_python_script_requested_timeout_is_capped_by_workspace(
    tmp_path: Path, monkeypatch
):
    """A model-requested ``timeout`` larger than the workspace's configured
    ceiling is clamped down to it, never allowed to raise it."""
    import aida.coding.tools as tools_module

    captured: dict = {}

    async def _fake_run(*_a, **kwargs):
        from aida.coding.runner import RunResult

        captured.update(kwargs)
        return RunResult(stdout="", stderr="", returncode=0, timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(tools_module, "_run_python_script", _fake_run)
    script = tmp_path / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    workspace = _workspace(tmp_path, script_timeout_seconds=30.0)
    tools = default_coding_tools(_guard(tmp_path), workspace=workspace)

    await _call(tools, "run_python_script", path=str(script), timeout=9999)
    assert captured["timeout"] == 30.0


@pytest.mark.asyncio
async def test_run_python_script_requested_timeout_below_ceiling_is_honored(
    tmp_path: Path, monkeypatch
):
    """A smaller-than-ceiling request (a script that should fail fast) is
    respected rather than always snapping to the workspace maximum."""
    import aida.coding.tools as tools_module

    captured: dict = {}

    async def _fake_run(*_a, **kwargs):
        from aida.coding.runner import RunResult

        captured.update(kwargs)
        return RunResult(stdout="", stderr="", returncode=0, timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(tools_module, "_run_python_script", _fake_run)
    script = tmp_path / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    workspace = _workspace(tmp_path, script_timeout_seconds=120.0)
    tools = default_coding_tools(_guard(tmp_path), workspace=workspace)

    await _call(tools, "run_python_script", path=str(script), timeout=5)
    assert captured["timeout"] == 5.0


# --- run_command --------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_command_allowlisted_relaxed_mode_runs_without_confirmation(tmp_path: Path):
    guard = _guard(tmp_path, mode="relaxed", allowlist=[f"{sys.executable} -c *"], confirm=_deny)
    tools = default_coding_tools(guard, workspace=_workspace(tmp_path))

    result = await _call(tools, "run_command", command=f"{sys.executable} -c \"print('hi')\"")
    assert not result.is_error
    assert "hi" in result.content


@pytest.mark.asyncio
async def test_run_command_not_allowlisted_requests_confirmation_and_honors_denial(tmp_path: Path):
    guard = _guard(tmp_path, mode="relaxed", confirm=_deny)  # empty allowlist
    tools = default_coding_tools(guard, workspace=_workspace(tmp_path))

    result = await _call(tools, "run_command", command="rm -rf /")
    assert result.is_error


@pytest.mark.asyncio
async def test_run_command_not_allowlisted_approved_still_runs(tmp_path: Path):
    guard = _guard(tmp_path, mode="relaxed", confirm=_approve)  # empty allowlist
    tools = default_coding_tools(guard, workspace=_workspace(tmp_path))

    result = await _call(tools, "run_command", command=f"{sys.executable} -c \"print('hi')\"")
    assert not result.is_error
    assert "hi" in result.content


@pytest.mark.asyncio
async def test_run_command_defaults_cwd_to_workspace_target_folder(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("hello", encoding="utf-8")
    guard = _guard(tmp_path, mode="relaxed", allowlist=[f"{sys.executable} *"])
    tools = default_coding_tools(guard, workspace=_workspace(tmp_path))

    result = await _call(
        tools,
        "run_command",
        command=f"{sys.executable} -c \"import pathlib; print(pathlib.Path('marker.txt').read_text())\"",
    )
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_run_command_no_cwd_and_no_workspace_folder_is_an_error(tmp_path: Path):
    workspace = WorkspaceConfig(name="ws")  # no target/source folders at all
    guard = _guard(tmp_path, mode="relaxed", allowlist=["ls"])
    tools = default_coding_tools(guard, workspace=workspace)

    result = await _call(tools, "run_command", command="ls")
    assert result.is_error
    assert "No cwd given" in result.content


@pytest.mark.asyncio
async def test_run_command_requested_timeout_is_capped_by_workspace(tmp_path: Path, monkeypatch):
    import aida.coding.tools as tools_module

    captured: dict = {}

    async def _fake_run(*_a, **kwargs):
        from aida.coding.runner import RunResult

        captured.update(kwargs)
        return RunResult(stdout="", stderr="", returncode=0, timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(tools_module, "run_subprocess", _fake_run)
    guard = _guard(tmp_path, mode="relaxed", allowlist=[f"{sys.executable} -c *"])
    workspace = _workspace(tmp_path, script_timeout_seconds=10.0)
    tools = default_coding_tools(guard, workspace=workspace)

    await _call(tools, "run_command", command=f"{sys.executable} -c \"print('hi')\"", timeout=999)
    assert captured["timeout"] == 10.0


@pytest.mark.asyncio
async def test_run_command_bad_quoting_is_an_error_not_a_crash(tmp_path: Path):
    guard = _guard(tmp_path, mode="relaxed", allowlist=['bad "quote'])
    tools = default_coding_tools(guard, workspace=_workspace(tmp_path))

    result = await _call(tools, "run_command", command='bad "quote')
    assert result.is_error


# --- output size cap (planning/improvement_plan_2026-09.md §1: stdout/stderr
# went to the model uncapped, up to 256,000 bytes each) ----------------------

_PRINT_N_CHARS = (
    "import sys; sys.stdout.write('x' * {n}); sys.stdout.write('\\n')"
)


@pytest.mark.asyncio
async def test_short_output_is_not_truncated(tmp_path: Path):
    tools = default_coding_tools(
        _guard(tmp_path), workspace=_workspace(tmp_path), scratch_dir=tmp_path / "scratch"
    )

    script = tmp_path / "print_short.py"
    script.write_text(_PRINT_N_CHARS.format(n=100), encoding="utf-8")
    result = await _call(tools, "run_python_script", path=str(script))

    assert "x" * 100 in result.content
    assert "omitted" not in result.content
    assert "saved to" not in result.content


@pytest.mark.asyncio
async def test_oversized_stdout_is_capped_and_spilled_to_scratch(tmp_path: Path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path), scratch_dir=scratch)

    n_chars = RUN_OUTPUT_DISPLAY_MAX_CHARS + 5_000
    script = tmp_path / "print_long.py"
    script.write_text(_PRINT_N_CHARS.format(n=n_chars), encoding="utf-8")
    result = await _call(tools, "run_python_script", path=str(script))

    assert "x" * n_chars not in result.content
    assert "x" * 100 in result.content
    assert "saved to" in result.content
    assert "read_file" in result.content

    spilled = list((scratch / "tool-results").glob("stdout-*.txt"))
    assert len(spilled) == 1
    assert "x" * n_chars in spilled[0].read_text(encoding="utf-8")
    assert str(spilled[0]) in result.content


@pytest.mark.asyncio
async def test_oversized_stdout_without_a_scratch_dir_falls_back_to_truncation(tmp_path: Path):
    tools = default_coding_tools(_guard(tmp_path), workspace=_workspace(tmp_path))

    n_chars = RUN_OUTPUT_DISPLAY_MAX_CHARS + 5_000
    script = tmp_path / "print_long.py"
    script.write_text(_PRINT_N_CHARS.format(n=n_chars), encoding="utf-8")
    result = await _call(tools, "run_python_script", path=str(script))

    assert "x" * n_chars not in result.content
    assert "omitted" in result.content
    assert "saved to" not in result.content
