"""Integration tests for aida.mcp.server.McpServerHandle, run against the
real subprocess in tests/mock_mcp_server.py (real stdio JSON-RPC — not a
mocked ClientSession). These are the Phase 3 "discovery, execution, typed
conversion for every content type, timeout handling, restart-after-crash"
tests from planning/phase03_mcp.md.

Note: this deliberately does NOT use an async pytest fixture for
start/stop. An ``AsyncExitStack`` holding anyio task groups open (as
``McpServerHandle`` does) must be entered and exited from the *same*
asyncio task; pytest-asyncio's fixture teardown runs as a separate task
from the test body, which trips anyio's "exit cancel scope in a different
task" guard. Each test starts and stops its own handle instead, all within
its own test-body task.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact, JsonArtifact, TextArtifact
from aida.config import secrets
from aida.config.settings import McpServerConfig
from aida.mcp.manager import McpManager
from aida.mcp.results import convert_result
from aida.mcp.server import McpServerError, McpServerHandle, resolve_env_secrets
from tests.test_secrets import _use_memory_backend

MOCK_SERVER_PATH = Path(__file__).parent / "mock_mcp_server.py"

ALL_TOOL_NAMES = {
    "echo_text",
    "get_image",
    "get_json_data",
    "get_multi_part",
    "echo_env",
    "get_cwd",
    "always_fails",
    "hang_forever",
    "crash_process",
}


def _mock_config(*, name: str = "mock-mcp", command: str | None = None) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command=command or sys.executable,
        args=[str(MOCK_SERVER_PATH)] if command is None else [],
    )


@asynccontextmanager
async def _running_handle(**kwargs) -> AsyncIterator[McpServerHandle]:
    h = McpServerHandle(_mock_config(), call_timeout_seconds=kwargs.pop("call_timeout_seconds", 5.0))
    try:
        await h.start()
        yield h
    finally:
        await h.stop()


@pytest.mark.asyncio
async def test_start_discovers_all_tools():
    async with _running_handle() as handle:
        assert {t.name for t in handle.list_tools()} == ALL_TOOL_NAMES
        assert handle.is_running


@pytest.mark.asyncio
async def test_start_captures_server_instructions():
    """A FastMCP server can declare `instructions=` specifically to teach
    an LLM how to use its own tools (pyirena-mcp ships a detailed one) —
    AIDA used to call session.initialize() and discard the result
    entirely, so this was never captured anywhere."""
    async with _running_handle() as handle:
        assert handle.instructions is not None
        assert "mock server instructions" in handle.instructions.lower()


@pytest.mark.asyncio
async def test_instructions_cleared_after_stop():
    handle = McpServerHandle(_mock_config(), call_timeout_seconds=5.0)
    await handle.start()
    assert handle.instructions is not None
    await handle.stop()
    assert handle.instructions is None


@pytest.mark.asyncio
async def test_start_is_idempotent():
    async with _running_handle() as handle:
        first = {t.name for t in handle.list_tools()}
        second = {t.name for t in await handle.start()}
        assert first == second


@pytest.mark.asyncio
async def test_call_tool_unknown_raises():
    async with _running_handle() as handle:
        with pytest.raises(McpServerError):
            await handle.call_tool("does_not_exist", {})


@pytest.mark.asyncio
async def test_call_tool_before_start_raises():
    h = McpServerHandle(_mock_config())
    with pytest.raises(McpServerError):
        await h.call_tool("echo_text", {"message": "hi"})


@pytest.mark.asyncio
async def test_echo_text_round_trips_through_convert_result():
    async with _running_handle() as handle:
        result = await handle.call_tool("echo_text", {"message": "hi"})
    artifacts = convert_result(result)
    text_artifacts = [a for a in artifacts if isinstance(a, TextArtifact)]
    assert len(text_artifacts) == 1
    assert text_artifacts[0].text == "echo: hi"


@pytest.mark.asyncio
async def test_get_image_round_trips_to_real_decodable_bytes():
    from tests.mock_mcp_server import TINY_PNG_BYTES

    async with _running_handle() as handle:
        result = await handle.call_tool("get_image", {})
    artifacts = convert_result(result)

    images = [a for a in artifacts if isinstance(a, ImageArtifact)]
    assert len(images) == 1
    assert images[0].data == TINY_PNG_BYTES
    assert images[0].mime_type == "image/png"


@pytest.mark.asyncio
async def test_get_multi_part_preserves_text_and_image_in_order():
    async with _running_handle() as handle:
        result = await handle.call_tool("get_multi_part", {})
    artifacts = convert_result(result)

    # The tool returns exactly [TextContent, ImageContent]; convert_result
    # may additionally append a JsonArtifact if structuredContent is also
    # populated (FastMCP's auto-wrap behaviour, verified empirically to
    # differ by return type) — assert on the two content-block artifacts
    # specifically rather than the list length.
    assert isinstance(artifacts[0], TextArtifact)
    assert isinstance(artifacts[1], ImageArtifact)


@pytest.mark.asyncio
async def test_get_json_data_produces_text_or_json_artifact():
    # get_json_data returns a plain dict from a FastMCP tool. Assert on the
    # artifact's usable content rather than pinning exactly which artifact
    # type carries it (FastMCP's structuredContent behaviour for dict
    # returns is an implementation detail of the SDK, not of results.py).
    async with _running_handle() as handle:
        result = await handle.call_tool("get_json_data", {})
    artifacts = convert_result(result)
    assert len(artifacts) >= 1
    assert any(
        (isinstance(a, JsonArtifact) and a.data.get("sample_id") == "S001")
        or (isinstance(a, TextArtifact) and "S001" in a.text)
        for a in artifacts
    )


@pytest.mark.asyncio
async def test_always_fails_reports_error_result():
    async with _running_handle() as handle:
        result = await handle.call_tool("always_fails", {})
        record = handle.calls[-1]
    assert result.isError is True
    assert record.is_error is True


@pytest.mark.asyncio
async def test_hang_forever_times_out_and_is_isolated():
    async with _running_handle(call_timeout_seconds=1.0) as handle:
        handle.call_timeout_seconds = 1.0

        with pytest.raises(McpServerError, match="timed out"):
            await handle.call_tool("hang_forever", {})

        # Failure isolation: the server (and handle) is still usable
        # afterwards for a *different* call — one hung tool call doesn't
        # take the whole server down.
        result = await handle.call_tool("echo_text", {"message": "still alive"})
        assert result.content[0].text == "echo: still alive"


@pytest.mark.asyncio
async def test_stderr_capture_records_real_subprocess_output():
    # FastMCP logs each incoming request to stderr by default — a real,
    # unforced signal that StderrCapture is actually wired to the real
    # subprocess fd (not a no-op), which matters because the naive
    # write()/flush()-only approach silently doesn't work here (see
    # StderrCapture's docstring for the AttributeError this replaced).
    async with _running_handle() as handle:
        await handle.call_tool("echo_text", {"message": "hi"})
        tail = handle.stderr.tail()
    assert any("CallToolRequest" in line for line in tail)


@pytest.mark.asyncio
async def test_call_records_diagnostics():
    async with _running_handle() as handle:
        await handle.call_tool("echo_text", {"message": "hi"})
        record = handle.calls[-1]
    assert record.tool_name == "echo_text"
    assert record.duration_seconds >= 0
    assert record.payload_bytes > 0


@pytest.mark.asyncio
async def test_restart_relaunches_and_rediscovers_tools():
    async with _running_handle() as handle:
        await handle.call_tool("echo_text", {"message": "before restart"})

        tools = await handle.restart()

        assert handle.is_running
        assert {t.name for t in tools} == ALL_TOOL_NAMES
        result = await handle.call_tool("echo_text", {"message": "after restart"})
        assert result.content[0].text == "echo: after restart"


@pytest.mark.asyncio
async def test_restart_after_real_process_crash_recovers():
    # Real crash, not a simulated one: crash_process() hard-kills the
    # subprocess with os._exit() before any JSON-RPC response is sent, so
    # the pending call_tool() fails from a closed stream, and restart() has
    # to actually relaunch a fresh subprocess to work again.
    async with _running_handle() as handle:
        with pytest.raises(McpServerError):
            await handle.call_tool("crash_process", {})

        tools = await handle.restart()
        assert {t.name for t in tools} == ALL_TOOL_NAMES

        result = await handle.call_tool("echo_text", {"message": "back from the dead"})
        assert result.content[0].text == "echo: back from the dead"


@pytest.mark.asyncio
async def test_stop_before_start_is_a_no_op():
    h = McpServerHandle(_mock_config())
    await h.stop()  # must not raise
    assert not h.is_running


@pytest.mark.asyncio
async def test_start_failure_raises_mcp_server_error():
    h = McpServerHandle(_mock_config(command="definitely-not-a-real-executable"))
    with pytest.raises(McpServerError):
        await h.start()
    assert not h.is_running


# --- B6: secrets in mcp.json env blocks -------------------------------------


def test_resolve_env_secrets_passes_plain_values_through():
    assert resolve_env_secrets({"FOO": "bar"}) == {"FOO": "bar"}


def test_resolve_env_secrets_resolves_keyring_prefix(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("my-token", "sk-real-value")
    resolved = resolve_env_secrets({"API_TOKEN": "keyring:my-token"})
    assert resolved == {"API_TOKEN": "sk-real-value"}


def test_resolve_env_secrets_resolves_secret_prefix(monkeypatch):
    """"secret:" is accepted too — mirrors provider profiles' own
    secret_ref terminology, rather than only the "keyring:" spelling."""
    _use_memory_backend(monkeypatch)
    secrets.set_secret("my-token", "sk-real-value")
    resolved = resolve_env_secrets({"API_TOKEN": "secret:my-token"})
    assert resolved == {"API_TOKEN": "sk-real-value"}


def test_resolve_env_secrets_mixes_plain_and_referenced_values(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("my-token", "sk-real-value")
    resolved = resolve_env_secrets({"PLAIN": "unchanged", "API_TOKEN": "keyring:my-token"})
    assert resolved == {"PLAIN": "unchanged", "API_TOKEN": "sk-real-value"}


def test_resolve_env_secrets_missing_secret_raises(monkeypatch):
    _use_memory_backend(monkeypatch)
    with pytest.raises(McpServerError, match="no-such-secret"):
        resolve_env_secrets({"API_TOKEN": "keyring:no-such-secret"})


@pytest.mark.asyncio
async def test_start_raises_for_missing_secret_without_spawning(monkeypatch):
    """A missing/misspelled secret reference fails start() synchronously,
    same as any other bad server config — isolated to just that one
    server by the caller (McpManager.start_all/start_server already wrap
    handle.start() in try/except McpServerError)."""
    _use_memory_backend(monkeypatch)
    config = McpServerConfig(
        name="mock-mcp",
        command=sys.executable,
        args=[str(MOCK_SERVER_PATH)],
        env={"API_TOKEN": "keyring:never-stored"},
    )
    h = McpServerHandle(config)
    with pytest.raises(McpServerError, match="never-stored"):
        await h.start()
    assert not h.is_running


@pytest.mark.asyncio
async def test_start_resolves_keyring_env_value_and_child_sees_the_real_value(monkeypatch):
    """End-to-end: a "keyring:NAME" env value is resolved to the real
    secret *before* the subprocess is spawned, and that subprocess (via
    the mock server's echo_env tool) actually receives the real value —
    not the "keyring:NAME" reference string itself."""
    _use_memory_backend(monkeypatch)
    secrets.set_secret("my-mock-token", "sk-actual-secret-value")
    config = McpServerConfig(
        name="mock-mcp",
        command=sys.executable,
        args=[str(MOCK_SERVER_PATH)],
        env={"MOCK_API_TOKEN": "keyring:my-mock-token"},
    )
    h = McpServerHandle(config)
    try:
        await h.start()
        result = await h.call_tool("echo_env", {"name": "MOCK_API_TOKEN"})
    finally:
        await h.stop()
    artifacts = convert_result(result)
    text_artifacts = [a for a in artifacts if isinstance(a, TextArtifact)]
    assert len(text_artifacts) == 1
    assert text_artifacts[0].text == "sk-actual-secret-value"


# --- cwd / scratch-folder wiring (bug report: "Agents seem to be saving
# temporary files ... in random places") ------------------------------------


def test_scratch_env_defaults_empty_without_cwd():
    from aida.mcp.server import _scratch_env_defaults

    assert _scratch_env_defaults(None) == {}


def test_scratch_env_defaults_sets_all_three_temp_vars(tmp_path: Path):
    from aida.mcp.server import _scratch_env_defaults

    assert _scratch_env_defaults(tmp_path) == {
        "TMPDIR": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }


@pytest.mark.asyncio
async def test_start_with_cwd_launches_the_subprocess_there(tmp_path: Path):
    """End-to-end: a handle started with ``cwd=`` actually spawns its
    subprocess in that directory — the first of the two root causes behind
    "agents scatter temp files": nothing was ever passed to
    StdioServerParameters, so every server inherited AIDA's own cwd."""
    h = McpServerHandle(_mock_config(), cwd=tmp_path)
    try:
        await h.start()
        result = await h.call_tool("get_cwd", {})
    finally:
        await h.stop()
    artifacts = convert_result(result)
    text_artifacts = [a for a in artifacts if isinstance(a, TextArtifact)]
    assert len(text_artifacts) == 1
    assert os.path.realpath(text_artifacts[0].text) == os.path.realpath(str(tmp_path))


@pytest.mark.asyncio
async def test_start_with_cwd_gives_the_child_that_tmpdir(tmp_path: Path):
    """End-to-end: a handle started with ``cwd=`` launches its subprocess
    with TMPDIR (and TEMP/TMP) pointed at that folder — the second of the
    two root causes behind "agents scatter temp files": the ``mcp`` SDK's
    own env defaults never inherit TMPDIR/TEMP/TMP from AIDA's process."""
    h = McpServerHandle(_mock_config(), cwd=tmp_path)
    try:
        await h.start()
        result = await h.call_tool("echo_env", {"name": "TMPDIR"})
    finally:
        await h.stop()
    artifacts = convert_result(result)
    text_artifacts = [a for a in artifacts if isinstance(a, TextArtifact)]
    assert len(text_artifacts) == 1
    assert text_artifacts[0].text == str(tmp_path)


@pytest.mark.asyncio
async def test_explicit_env_still_overrides_the_scratch_tmpdir_default(tmp_path: Path):
    """A server config that sets TMPDIR itself must win over the scratch
    default — same "explicit config always wins" rule as everything else
    in this env-merging path."""
    config = McpServerConfig(
        name="mock-mcp",
        command=sys.executable,
        args=[str(MOCK_SERVER_PATH)],
        env={"TMPDIR": "/explicitly/configured"},
    )
    h = McpServerHandle(config, cwd=tmp_path)
    try:
        await h.start()
        result = await h.call_tool("echo_env", {"name": "TMPDIR"})
    finally:
        await h.stop()
    artifacts = convert_result(result)
    text_artifacts = [a for a in artifacts if isinstance(a, TextArtifact)]
    assert len(text_artifacts) == 1
    assert text_artifacts[0].text == "/explicitly/configured"


# --- a server that starts but never answers initialize ---------------------
#
# Review finding: ClientSession was constructed with no read_timeout_seconds
# and start() did a bare `await ready.wait()`, so a subprocess that launched
# and then went quiet (bad env, hung import, anything reading stdin) blocked
# start() -> start_all() -> the whole session forever. stop() hung too: the
# serving task never reached its stop-event wait, so setting that event did
# nothing and `await self._serve_task` never returned. There was no recovery
# path at all — the CLI never reached its prompt, the GUI sat on
# "Starting session…".


def _quiet_server_config(name: str = "quiet-mcp") -> McpServerConfig:
    """A process that starts fine and then blocks on stdin forever, never
    replying to the MCP initialize handshake."""
    return McpServerConfig(
        name=name, command=sys.executable, args=["-c", "import sys; sys.stdin.read()"]
    )


@pytest.mark.asyncio
async def test_start_times_out_on_a_server_that_never_answers_initialize():
    handle = McpServerHandle(_quiet_server_config(), startup_timeout_seconds=1.0, stop_timeout_seconds=2.0)
    try:
        with pytest.raises(McpServerError) as excinfo:
            await handle.start()
    finally:
        await handle.stop()

    assert "did not finish starting" in str(excinfo.value)
    assert handle.is_running is False


@pytest.mark.asyncio
async def test_stop_returns_after_a_startup_timeout():
    """stop() must stay callable — and *return* — after a wedged start, since
    that is exactly the path McpManager/ChatBridge take on the way out."""
    handle = McpServerHandle(_quiet_server_config(), startup_timeout_seconds=1.0, stop_timeout_seconds=2.0)
    with pytest.raises(McpServerError):
        await handle.start()

    await asyncio.wait_for(handle.stop(), timeout=10.0)


@pytest.mark.asyncio
async def test_a_wedged_server_does_not_block_the_others_from_starting():
    """Failure isolation, the whole point: one silent server contributes no
    tools and is recorded in start_errors; the healthy one still comes up."""
    manager = McpManager(
        [_quiet_server_config(), _mock_config(name="mock-mcp")], startup_timeout_seconds=1.0
    )
    try:
        tools = await manager.start_all()
        running = list(manager.running_server_names)
        start_errors = dict(manager.start_errors)
    finally:
        await manager.aclose()  # clears the handle list, so snapshot above

    assert "quiet-mcp" in start_errors
    assert "did not finish starting" in start_errors["quiet-mcp"]
    assert running == ["mock-mcp"]
    assert any(name.startswith("mock-mcp") for name in tools)
