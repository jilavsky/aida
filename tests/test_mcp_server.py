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

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact, JsonArtifact, TextArtifact
from aida.config.settings import McpServerConfig
from aida.mcp.results import convert_result
from aida.mcp.server import McpServerError, McpServerHandle

MOCK_SERVER_PATH = Path(__file__).parent / "mock_mcp_server.py"

ALL_TOOL_NAMES = {
    "echo_text",
    "get_image",
    "get_json_data",
    "get_multi_part",
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
