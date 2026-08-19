"""Phase 3's stated acceptance criterion, automated:

    "Keystone test (automated, mock-mcp): agent loop with MockProvider
    requests the image tool -> PNG arrives as ImageArtifact, valid
    decodable bytes, saved to artifacts dir, ImageArtifactCreated event
    emitted, and the LLM receives the text-policy representation — all
    asserted"

This is the single test that proves the whole Phase 3 promise end to end:
a model calls an MCP tool, a PNG comes back, and AIDA hands the rest of the
system a real typed ``ImageArtifact`` — never a base64 blob smuggled through
a text string. Everything here is real except the LLM: a real subprocess
MCP server (tests/mock_mcp_server.py), a real ArtifactStore writing to a
real temp directory, and the real AgentLoop/McpManager/results.py
production code path. Only the model is a scripted MockProvider, per
PLAN.md §7 ("MockProvider ... makes the agent loop testable without any
model").
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.config.settings import McpServerConfig
from aida.core.agent import AgentLoop
from aida.core.events import ImageArtifactCreated, MessageFinished, TextFinished, ToolCallFinished
from aida.mcp.manager import McpManager
from aida.providers.base import CompletionSettings, Message
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from tests.mock_mcp_server import TINY_PNG_BYTES

MOCK_SERVER_PATH = Path(__file__).parent / "mock_mcp_server.py"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _assert_valid_png(data: bytes) -> None:
    """A dependency-free "is this actually a decodable PNG" check (no
    Pillow in aida's dependency set — PLAN.md §8's "no additions without
    demonstrated need"): verifies the 8-byte PNG signature and that the
    first chunk is a well-formed IHDR (width/height/bit-depth header)."""
    assert data.startswith(PNG_SIGNATURE)
    length = int.from_bytes(data[8:12], "big")
    chunk_type = data[12:16]
    assert chunk_type == b"IHDR"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    assert length == 13  # IHDR is always exactly 13 bytes
    assert width > 0
    assert height > 0


@pytest.mark.asyncio
async def test_keystone_mcp_image_round_trip(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    store = ArtifactStore(base_dir=artifacts_dir)
    server_config = McpServerConfig(name="mock-mcp", command=sys.executable, args=[str(MOCK_SERVER_PATH)])
    manager = McpManager([server_config], artifact_store=store)

    try:
        mcp_tools = await manager.start_all()
        assert "mock-mcp.get_image" in mcp_tools

        provider = MockProvider(
            [
                MockTurn(
                    text="let me get that plot",
                    tool_calls=[MockToolCall(name="mock-mcp.get_image", id="call_1")],
                ),
                MockTurn(text="here is the plot you asked for"),
            ]
        )
        loop = AgentLoop(provider, CompletionSettings(model="mock-model"), tools=mcp_tools)
        messages = [Message(role="user", content="plot dataset X")]

        events = [e async for e in loop.run(messages)]

        # 1. A real ImageArtifactCreated event was emitted for this call.
        created = next(e for e in events if isinstance(e, ImageArtifactCreated))
        assert created.call_id == "call_1"
        assert created.mime_type == "image/png"
        assert created.path is not None

        # 2. The bytes on disk are the real, valid, decodable PNG the mock
        #    server returned — not a placeholder, not corrupted by the
        #    base64 round trip.
        saved_path = Path(created.path)
        assert saved_path.exists()
        assert saved_path.is_relative_to(artifacts_dir)
        on_disk = saved_path.read_bytes()
        assert on_disk == TINY_PNG_BYTES
        _assert_valid_png(on_disk)

        # 3. The store's own bookkeeping agrees.
        saved_artifact = next(
            a
            for a in store.list_metadata()
            if a.id == created.artifact_id
        )
        assert saved_artifact.kind == "ImageArtifact"
        assert saved_artifact.path == created.path

        # 4. The ToolCallFinished result — what actually reaches the model —
        #    is the text-policy description, never the raw bytes or base64.
        tool_finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert tool_finished.call_id == "call_1"
        assert "image/png" in str(tool_finished.result)
        assert str(TINY_PNG_BYTES) not in str(tool_finished.result)
        assert created.path in str(tool_finished.result)

        # 5. What was actually appended to conversation history for the
        #    model's next turn is that same safe text, not an ImageArtifact
        #    object or raw bytes leaking into the message list.
        tool_message = next(m for m in messages if m.role == "tool" and m.tool_call_id == "call_1")
        assert isinstance(tool_message.content, str)
        assert "image/png" in tool_message.content

        # 6. The turn completed normally and the loop kept going after the
        #    tool call, exactly as a real chat session would.
        assert any(isinstance(e, TextFinished) and e.text == "here is the plot you asked for" for e in events)
        assert any(isinstance(e, MessageFinished) for e in events)
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_keystone_artifact_type_is_real_image_artifact_not_text(tmp_path):
    """A narrower, very literal check of the keystone promise: calling the
    MCP tool function directly returns a ToolResult whose artifacts list
    contains a genuine ImageArtifact instance — not a string anywhere."""
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    server_config = McpServerConfig(name="mock-mcp", command=sys.executable, args=[str(MOCK_SERVER_PATH)])
    manager = McpManager([server_config], artifact_store=store)
    try:
        tools = await manager.start_all()
        result = await tools["mock-mcp.get_image"].func({})

        assert len(result.artifacts) == 1
        artifact = result.artifacts[0]
        assert isinstance(artifact, ImageArtifact)
        assert isinstance(artifact.data, bytes)
        assert artifact.data == TINY_PNG_BYTES
        assert not result.is_error
    finally:
        await manager.aclose()
