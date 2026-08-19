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
