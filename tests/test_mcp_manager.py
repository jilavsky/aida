"""Integration tests for aida.mcp.manager.McpManager — the piece that wires
real MCP servers (via McpServerHandle) into agent-loop-compatible
NativeTools. Uses the real tests/mock_mcp_server.py subprocess, same as
test_mcp_server.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.config.settings import McpServerConfig
from aida.mcp.manager import McpManager, namespaced_tool_name

MOCK_SERVER_PATH = Path(__file__).parent / "mock_mcp_server.py"


def _mock_server_config(name: str = "mock-mcp", *, skills: list[str] | None = None) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command=sys.executable,
        args=[str(MOCK_SERVER_PATH)],
        skills=skills or [],
    )


def test_namespaced_tool_name():
    assert namespaced_tool_name("pyirena", "plot_saxs") == "pyirena.plot_saxs"


@pytest.mark.asyncio
async def test_start_all_returns_namespaced_tools(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        assert "mock-mcp.echo_text" in tools
        assert "mock-mcp.get_image" in tools
        assert tools["mock-mcp.echo_text"].schema.name == "mock-mcp.echo_text"
        assert manager.running_server_names == ["mock-mcp"]
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_echo_text_tool_round_trips(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.echo_text"].func({"message": "hi"})
        assert result.is_error is False
        assert "echo: hi" in result.content
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_get_image_tool_saves_artifact_and_returns_path(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.get_image"].func({})

        assert result.is_error is False
        images = [a for a in result.artifacts if isinstance(a, ImageArtifact)]
        assert len(images) == 1
        assert images[0].path is not None
        assert Path(images[0].path).exists()
        assert Path(images[0].path).read_bytes() == images[0].data
        # The text fed back to the model must describe the image, never
        # embed its raw bytes.
        assert "image/png" in result.content
        assert str(images[0].data) not in result.content
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_always_fails_tool_is_error(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.always_fails"].func({})
        assert result.is_error is True
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_failing_server_is_isolated_not_fatal(tmp_path):
    good = _mock_server_config("mock-mcp")
    bad = McpServerConfig(name="broken", command="definitely-not-a-real-executable")
    manager = McpManager([good, bad], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        assert any(name.startswith("mock-mcp.") for name in tools)
        assert not any(name.startswith("broken.") for name in tools)
        assert "broken" in manager.start_errors
        assert manager.running_server_names == ["mock-mcp"]
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_calling_tool_after_aclose_reports_not_running(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    tools = await manager.start_all()
    tool = tools["mock-mcp.echo_text"]
    await manager.aclose()

    result = await tool.func({"message": "hi"})
    assert result.is_error is True
    assert "not running" in result.content


def test_skills_deduplicated_across_servers():
    manager = McpManager(
        [
            _mock_server_config("a", skills=["saxs-basics", "shared"]),
            _mock_server_config("b", skills=["shared", "waxs-basics"]),
        ]
    )
    assert manager.skills() == ["saxs-basics", "shared", "waxs-basics"]


def test_enabled_server_names_reflects_construction_not_start():
    manager = McpManager([_mock_server_config("a"), _mock_server_config("b")])
    assert set(manager.enabled_server_names) == {"a", "b"}
    assert manager.running_server_names == []


# --- per-tool disable (Phase 7) ---------------------------------------------


@pytest.mark.asyncio
async def test_disabled_tool_is_excluded_from_start_all(tmp_path):
    config = _mock_server_config()
    config.disabled_tools = ["always_fails"]
    manager = McpManager([config], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        assert "mock-mcp.always_fails" not in tools
        assert "mock-mcp.echo_text" in tools, "other tools on the same server are unaffected"
    finally:
        await manager.aclose()


# --- per-tool confirm (Phase 7) ---------------------------------------------


@pytest.mark.asyncio
async def test_confirm_flagged_tool_calls_the_confirm_callback(tmp_path):
    config = _mock_server_config()
    config.confirm_tools = ["echo_text"]
    calls = []

    async def confirm(request):
        calls.append(request)
        return True

    manager = McpManager([config], artifact_store=ArtifactStore(base_dir=tmp_path), confirm_callback=confirm)
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.echo_text"].func({"message": "hi"})
        assert result.is_error is False
        assert len(calls) == 1
        assert calls[0].path == "mock-mcp.echo_text"
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_confirm_denied_produces_an_error_result_not_a_raised_exception(tmp_path):
    config = _mock_server_config()
    config.confirm_tools = ["echo_text"]

    async def deny(_request):
        return False

    manager = McpManager([config], artifact_store=ArtifactStore(base_dir=tmp_path), confirm_callback=deny)
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.echo_text"].func({"message": "hi"})
        assert result.is_error is True
        assert "declined" in result.content
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_tool_without_confirm_flag_never_calls_the_confirm_callback(tmp_path):
    config = _mock_server_config()  # confirm_tools left empty
    called = []

    async def confirm(request):
        called.append(request)
        return True

    manager = McpManager([config], artifact_store=ArtifactStore(base_dir=tmp_path), confirm_callback=confirm)
    try:
        tools = await manager.start_all()
        await tools["mock-mcp.echo_text"].func({"message": "hi"})
        assert called == []
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_default_confirm_callback_denies(tmp_path):
    """No confirm_callback given at all -> aida.core.confirmation.deny_all
    -> a confirm-flagged tool is always refused, never silently allowed."""
    config = _mock_server_config()
    config.confirm_tools = ["echo_text"]
    manager = McpManager([config], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.echo_text"].func({"message": "hi"})
        assert result.is_error is True
    finally:
        await manager.aclose()


# --- live per-server control (Phase 7) --------------------------------------


@pytest.mark.asyncio
async def test_start_server_then_stop_server(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_server("mock-mcp")
        assert "mock-mcp.echo_text" in tools
        assert manager.running_server_names == ["mock-mcp"]

        await manager.stop_server("mock-mcp")
        assert manager.running_server_names == []
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_start_server_is_idempotent(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        first = await manager.start_server("mock-mcp")
        second = await manager.start_server("mock-mcp")
        assert set(first) == set(second)
        assert manager.running_server_names == ["mock-mcp"]
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_start_server_unknown_name_raises(tmp_path):
    from aida.mcp.server import McpServerError

    manager = McpManager([], artifact_store=ArtifactStore(base_dir=tmp_path))
    with pytest.raises(McpServerError, match="not configured"):
        await manager.start_server("does-not-exist")


@pytest.mark.asyncio
async def test_stop_server_not_running_is_a_noop(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    await manager.stop_server("mock-mcp")  # must not raise
    assert manager.running_server_names == []


@pytest.mark.asyncio
async def test_restart_server_recovers_tools(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        await manager.start_server("mock-mcp")
        tools = await manager.restart_server("mock-mcp")
        assert "mock-mcp.echo_text" in tools
        assert manager.running_server_names == ["mock-mcp"]
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_add_server_config_then_start_it_live(tmp_path):
    """The GUI's "Add Server" dialog takes effect in the current session,
    not only after a restart."""
    manager = McpManager([], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        assert manager.enabled_server_names == []
        manager.add_server_config(_mock_server_config())
        assert manager.enabled_server_names == ["mock-mcp"]
        tools = await manager.start_server("mock-mcp")
        assert "mock-mcp.echo_text" in tools
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_remove_server_config_stops_it_first(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    await manager.start_server("mock-mcp")
    await manager.remove_server_config("mock-mcp")
    assert manager.running_server_names == []
    assert manager.enabled_server_names == []


# --- test_connection (Phase 7) ----------------------------------------------


@pytest.mark.asyncio
async def test_connection_test_against_a_working_server(tmp_path):
    manager = McpManager([], artifact_store=ArtifactStore(base_dir=tmp_path))
    result = await manager.test_connection(_mock_server_config())
    assert result.ok is True
    assert result.tool_count > 0
    assert result.elapsed_seconds >= 0
    assert manager.running_server_names == [], "a standalone test must not leave a handle registered"


@pytest.mark.asyncio
async def test_connection_test_against_a_broken_server(tmp_path):
    manager = McpManager([], artifact_store=ArtifactStore(base_dir=tmp_path))
    bad = McpServerConfig(name="broken", command="definitely-not-a-real-executable")
    result = await manager.test_connection(bad)
    assert result.ok is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_connection_test_reuses_an_already_running_server_instantly(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        await manager.start_server("mock-mcp")
        result = await manager.test_connection(_mock_server_config())
        assert result.ok is True
        assert result.tool_count > 0
    finally:
        await manager.aclose()


# --- recent_calls (Phase 7 log panel) ---------------------------------------


@pytest.mark.asyncio
async def test_recent_calls_are_most_recent_first_across_servers(tmp_path):
    """``recent_calls`` merges + sorts by ``ToolCallRecord.recorded_at``
    across *every* running handle, not just within one server's own
    ``.calls`` list. A second server's history is simulated with a
    synthetic, never-started ``McpServerHandle`` (rather than a second real
    mock-mcp subprocess) deliberately: two concurrently-open real MCP
    stdio subprocesses being closed together in one ``McpManager`` hits an
    unrelated anyio/child-process-reaping cancel-scope error
    ("Attempted to exit a cancel scope that isn't the current task's
    current cancel scope") that's orthogonal to what's being tested here —
    the merge/sort logic only needs handles' ``.calls`` lists to exist, not
    a live process behind them. A never-started handle's ``.stop()`` is a
    safe no-op (``_stack``/``stderr`` are both still ``None``), so it's also
    safe to leave registered for ``manager.aclose()``'s cleanup loop."""
    from aida.mcp.server import McpServerHandle, ToolCallRecord

    manager = McpManager([_mock_server_config("a")], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        await tools["a.echo_text"].func({"message": "first"})

        fake_b = McpServerHandle(_mock_server_config("b"))
        fake_b.calls.append(
            ToolCallRecord(
                tool_name="echo_text", duration_seconds=0.01, is_error=False, arguments={"message": "second"}
            )
        )
        manager._handles["b"] = fake_b

        await tools["a.echo_text"].func({"message": "third"})

        calls = manager.recent_calls()
        messages = [record.arguments.get("message") for _server, record in calls]
        assert messages == ["third", "second", "first"]
        assert calls[0][0] == "a"
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_recent_calls_respects_limit(tmp_path):
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        for i in range(5):
            await tools["mock-mcp.echo_text"].func({"message": str(i)})
        assert len(manager.recent_calls(limit=2)) == 2
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_recent_calls_content_preview_has_no_raw_image_bytes(tmp_path):
    """The raw inspector's data source: image content is summarized (mime
    type + base64 length), never the actual base64 payload."""
    manager = McpManager([_mock_server_config()], artifact_store=ArtifactStore(base_dir=tmp_path))
    try:
        tools = await manager.start_all()
        await tools["mock-mcp.get_image"].func({})
        _server, record = manager.recent_calls()[0]
        image_preview = next(p for p in record.content_preview if p["type"] == "image")
        assert image_preview["mime_type"] == "image/png"
        assert image_preview["base64_length"] > 0
        assert "data" not in image_preview
    finally:
        await manager.aclose()
