"""Tests for aida.mcp.results, built against real mcp.types objects (not
guessed shapes) — see the module docstring in results.py for the mapping
this enforces. The central assertion across this file is the Phase 3
keystone promise: an ImageContent block becomes an ImageArtifact with real
decoded bytes, never a text string.
"""

from __future__ import annotations

import base64

from mcp import types

from aida.artifacts.base import FileArtifact, ImageArtifact, JsonArtifact, TextArtifact
from aida.mcp.results import convert_result

RAW_IMAGE_BYTES = b"not-really-a-png-but-real-bytes"
RAW_AUDIO_BYTES = b"not-really-a-wav-but-real-bytes"
RAW_BLOB_BYTES = b"arbitrary-blob-bytes"


def _result(*blocks: types.ContentBlock, structured=None, is_error=False) -> types.CallToolResult:
    return types.CallToolResult(content=list(blocks), structuredContent=structured, isError=is_error)


def test_text_content_becomes_text_artifact():
    result = _result(types.TextContent(type="text", text="hello world"))
    artifacts = convert_result(result)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], TextArtifact)
    assert artifacts[0].text == "hello world"


def test_image_content_becomes_image_artifact_with_decoded_bytes():
    encoded = base64.b64encode(RAW_IMAGE_BYTES).decode()
    result = _result(types.ImageContent(type="image", data=encoded, mimeType="image/png"))

    artifacts = convert_result(result)

    assert len(artifacts) == 1
    art = artifacts[0]
    assert isinstance(art, ImageArtifact)
    # The keystone assertion: real decoded bytes, not the base64 text.
    assert art.data == RAW_IMAGE_BYTES
    assert art.data != encoded
    assert art.mime_type == "image/png"


def test_audio_content_becomes_file_artifact_with_decoded_bytes():
    encoded = base64.b64encode(RAW_AUDIO_BYTES).decode()
    result = _result(types.AudioContent(type="audio", data=encoded, mimeType="audio/wav"))

    artifacts = convert_result(result)

    assert len(artifacts) == 1
    art = artifacts[0]
    assert isinstance(art, FileArtifact)
    assert art.data == RAW_AUDIO_BYTES
    assert art.mime_type == "audio/wav"
    assert art.filename == "audio.wav"


def test_resource_link_becomes_file_artifact_without_local_bytes():
    result = _result(
        types.ResourceLink(
            type="resource_link", name="data.csv", uri="file:///tmp/data.csv", mimeType="text/csv"
        )
    )

    artifacts = convert_result(result)

    assert len(artifacts) == 1
    art = artifacts[0]
    assert isinstance(art, FileArtifact)
    assert art.path is None
    assert art.mime_type == "text/csv"
    assert art.filename == "data.csv"


def test_embedded_resource_with_text_becomes_text_artifact():
    resource = types.TextResourceContents(
        uri="file:///tmp/a.txt", text="hi there", mimeType="text/plain"
    )
    result = _result(types.EmbeddedResource(type="resource", resource=resource))

    artifacts = convert_result(result)

    assert len(artifacts) == 1
    assert isinstance(artifacts[0], TextArtifact)
    assert artifacts[0].text == "hi there"


def test_embedded_resource_with_blob_becomes_file_artifact_with_decoded_bytes():
    encoded = base64.b64encode(RAW_BLOB_BYTES).decode()
    resource = types.BlobResourceContents(
        uri="file:///tmp/a.bin", blob=encoded, mimeType="application/octet-stream"
    )
    result = _result(types.EmbeddedResource(type="resource", resource=resource))

    artifacts = convert_result(result)

    assert len(artifacts) == 1
    art = artifacts[0]
    assert isinstance(art, FileArtifact)
    assert art.data == RAW_BLOB_BYTES
    assert art.mime_type == "application/octet-stream"


def test_structured_content_appends_json_artifact():
    result = _result(
        types.TextContent(type="text", text="ok"),
        structured={"sample_id": "S001", "rg": 34.2},
    )

    artifacts = convert_result(result)

    assert len(artifacts) == 2
    assert isinstance(artifacts[0], TextArtifact)
    assert isinstance(artifacts[1], JsonArtifact)
    assert artifacts[1].data == {"sample_id": "S001", "rg": 34.2}


def test_no_structured_content_means_no_json_artifact():
    result = _result(types.TextContent(type="text", text="ok"))
    artifacts = convert_result(result)
    assert len(artifacts) == 1
    assert not any(isinstance(a, JsonArtifact) for a in artifacts)


def test_multi_part_result_preserves_order():
    encoded = base64.b64encode(RAW_IMAGE_BYTES).decode()
    result = _result(
        types.TextContent(type="text", text="Here is the plot:"),
        types.ImageContent(type="image", data=encoded, mimeType="image/png"),
    )

    artifacts = convert_result(result)

    assert len(artifacts) == 2
    assert isinstance(artifacts[0], TextArtifact)
    assert isinstance(artifacts[1], ImageArtifact)


def test_error_result_content_still_converts_as_text():
    # isError=True results carry their error message as ordinary TextContent
    # (verified against the mock server's always_fails tool) — conversion
    # doesn't special-case it, callers check result.isError separately.
    result = _result(types.TextContent(type="text", text="RuntimeError: boom"), is_error=True)
    artifacts = convert_result(result)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], TextArtifact)
    assert "boom" in artifacts[0].text
