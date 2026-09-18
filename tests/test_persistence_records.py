from __future__ import annotations

from pathlib import Path

from aida.artifacts.store import ArtifactStore
from aida.persistence.records import (
    record_file_path,
    render_transcript,
    sidecar_dir,
    slugify,
    write_transcript,
)
from aida.persistence.store import ArtifactRecord
from aida.providers.base import Message

CONV_ID = "abcdef1234567890"


def test_slugify_basic():
    assert slugify("USAXS Analysis Run #1") == "usaxs-analysis-run-1"


def test_slugify_empty_falls_back():
    assert slugify("###") == "untitled"


def test_slugify_truncates():
    long_title = "x" * 200
    assert len(slugify(long_title)) <= 60


def test_record_file_path_uses_title_slug_and_short_id():
    path = record_file_path(Path("/records"), CONV_ID, "My Analysis")
    assert path.name == "my-analysis-abcdef12.md"


def test_record_file_path_without_title_uses_short_id():
    path = record_file_path(Path("/records"), CONV_ID, None)
    assert path.name == "abcdef12-abcdef12.md"


def test_sidecar_dir_is_per_conversation():
    d = sidecar_dir(Path("/records"), "figures", CONV_ID)
    assert d == Path("/records/figures/abcdef12")


def test_render_transcript_includes_metadata_header():
    text = render_transcript(
        conversation_id=CONV_ID,
        title="My Analysis",
        workspace_name="use-pyirena",
        profile_name="argo-claude",
        messages=[],
        artifacts=[],
        sidecar_dirname="figures",
    )
    assert "# My Analysis" in text
    assert "use-pyirena" in text
    assert "argo-claude" in text
    assert CONV_ID in text


def test_render_transcript_skips_system_message():
    messages = [Message(role="system", content="you are a helpful assistant")]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=[],
        sidecar_dirname="figures",
    )
    assert "helpful assistant" not in text


def test_render_transcript_includes_user_and_assistant_turns():
    messages = [
        Message(role="user", content="plot dataset X"),
        Message(role="assistant", content="Here is the plot."),
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=[],
        sidecar_dirname="figures",
    )
    assert "## User" in text
    assert "plot dataset X" in text
    assert "## Assistant" in text
    assert "Here is the plot." in text


def test_render_transcript_links_image_artifact_for_tool_message():
    messages = [
        Message(
            role="tool",
            content="[image artifact abc: image/png]",
            tool_call_id="call_1",
            name="get_image",
        ),
    ]
    artifacts = [
        ArtifactRecord(
            id="abc",
            conversation_id=CONV_ID,
            call_id="call_1",
            kind="ImageArtifact",
            path="/home/user/.aida/artifacts/abc.png",
            mime_type="image/png",
            created_at="2026-08-19T00:00:00",
        )
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=artifacts,
        sidecar_dirname="figures",
    )
    assert f"![abc](figures/{CONV_ID[:8]}/abc.png)" in text


def test_write_transcript_creates_real_file_with_working_image_link(tmp_path: Path):
    records_dir = tmp_path / "records"
    artifacts_base = tmp_path / "aida-artifacts"
    store = ArtifactStore(base_dir=artifacts_base)

    # Simulate an already-saved artifact on disk (as McpManager would leave it).
    saved_path = artifacts_base / "abc.png"
    artifacts_base.mkdir(parents=True, exist_ok=True)
    saved_path.write_bytes(b"fake-png-bytes")

    messages = [
        Message(role="user", content="plot dataset X"),
        Message(
            role="assistant",
            content="",
            tool_calls=[],
        ),
        Message(
            role="tool", content="[image artifact abc]", tool_call_id="call_1", name="get_image"
        ),
        Message(role="assistant", content="Here is the plot."),
    ]
    artifacts = [
        ArtifactRecord(
            id="abc",
            conversation_id=CONV_ID,
            call_id="call_1",
            kind="ImageArtifact",
            path=str(saved_path),
            mime_type="image/png",
            created_at="2026-08-19T00:00:00",
        )
    ]

    path = record_file_path(records_dir, CONV_ID, "My Analysis")
    result_path = write_transcript(
        path=path,
        records_dir=records_dir,
        artifact_store=store,
        conversation_id=CONV_ID,
        title="My Analysis",
        workspace_name="use-pyirena",
        profile_name="argo-claude",
        messages=messages,
        artifacts=artifacts,
    )

    assert result_path == path
    assert path.exists()
    text = path.read_text(encoding="utf-8")

    # The image link is relative and actually resolves to a real file next
    # to the .md file — this is the literal Phase 4 acceptance criterion
    # ("~/Documents/Aida/ contains a readable MD transcript with a working
    # image link").
    link_target = records_dir / f"figures/{CONV_ID[:8]}/abc.png"
    assert link_target.exists()
    assert link_target.read_bytes() == b"fake-png-bytes"
    assert f"figures/{CONV_ID[:8]}/abc.png" in text


def test_render_transcript_links_file_artifact_for_tool_message():
    """Non-image artifacts (a saved script, a generated report) get linked
    the same way images already are — "what did the agent write" should be
    answerable from the transcript alone."""
    messages = [
        Message(
            role="tool",
            content="wrote fit_script.py",
            tool_call_id="call_1",
            name="save_script",
        ),
    ]
    artifacts = [
        ArtifactRecord(
            id="script1",
            conversation_id=CONV_ID,
            call_id="call_1",
            kind="FileArtifact",
            path="/home/user/.aida/artifacts/fit_script.py",
            mime_type="text/x-python",
            created_at="2026-08-19T00:00:00",
        )
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=artifacts,
        sidecar_dirname="figures",
    )
    assert f"[fit_script.py](figures/{CONV_ID[:8]}/fit_script.py)" in text
    # Not an image link — no leading "!".
    assert "![fit_script.py]" not in text


def test_render_transcript_tool_result_mode_off_hides_text_but_keeps_image():
    messages = [
        Message(
            role="tool",
            content="a huge raw JSON dump nobody wants to read",
            tool_call_id="call_1",
        ),
    ]
    artifacts = [
        ArtifactRecord(
            id="abc",
            conversation_id=CONV_ID,
            call_id="call_1",
            kind="ImageArtifact",
            path="/home/user/.aida/artifacts/abc.png",
            mime_type="image/png",
            created_at="2026-08-19T00:00:00",
        )
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=artifacts,
        sidecar_dirname="figures",
        tool_result_mode="off",
    )
    assert "a huge raw JSON dump" not in text
    assert f"![abc](figures/{CONV_ID[:8]}/abc.png)" in text


def test_render_transcript_tool_result_mode_off_skips_empty_heading():
    """A tool result with no artifact and mode "off" should leave no trace
    at all — not even an empty "## Tool result" heading."""
    messages = [
        Message(role="user", content="run the calibration"),
        Message(role="tool", content="calibration complete, see log.txt", tool_call_id="call_1"),
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=[],
        sidecar_dirname="figures",
        tool_result_mode="off",
    )
    assert "## Tool result" not in text
    assert "calibration complete" not in text


def test_render_transcript_tool_result_mode_summary_truncates_to_first_line():
    messages = [
        Message(
            role="tool",
            content="first line summary\nsecond line of detail\nthird line",
            tool_call_id="call_1",
        ),
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=[],
        sidecar_dirname="figures",
        tool_result_mode="summary",
    )
    assert "first line summary" in text
    assert "second line of detail" not in text


def test_render_transcript_unrecognized_tool_result_mode_falls_back_to_full():
    messages = [
        Message(role="tool", content="the full raw content", tool_call_id="call_1"),
    ]
    text = render_transcript(
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=[],
        sidecar_dirname="figures",
        tool_result_mode="bogus",
    )
    assert "the full raw content" in text


def test_write_transcript_copies_and_links_a_file_artifact(tmp_path: Path):
    records_dir = tmp_path / "records"
    artifacts_base = tmp_path / "aida-artifacts"
    store = ArtifactStore(base_dir=artifacts_base)

    artifacts_base.mkdir(parents=True, exist_ok=True)
    saved_script = artifacts_base / "fit_script.py"
    saved_script.write_text("print('hello')", encoding="utf-8")

    messages = [
        Message(role="tool", content="wrote a script", tool_call_id="call_1"),
    ]
    artifacts = [
        ArtifactRecord(
            id="script1",
            conversation_id=CONV_ID,
            call_id="call_1",
            kind="FileArtifact",
            path=str(saved_script),
            mime_type="text/x-python",
            created_at="2026-08-19T00:00:00",
        )
    ]

    path = record_file_path(records_dir, CONV_ID, None)
    write_transcript(
        path=path,
        records_dir=records_dir,
        artifact_store=store,
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=messages,
        artifacts=artifacts,
    )

    link_target = records_dir / f"figures/{CONV_ID[:8]}/fit_script.py"
    assert link_target.exists()
    assert link_target.read_text(encoding="utf-8") == "print('hello')"
    assert f"figures/{CONV_ID[:8]}/fit_script.py" in path.read_text(encoding="utf-8")


def test_write_transcript_overwrites_on_repeat_calls(tmp_path: Path):
    records_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    path = record_file_path(records_dir, CONV_ID, None)

    write_transcript(
        path=path,
        records_dir=records_dir,
        artifact_store=store,
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=[Message(role="user", content="first")],
        artifacts=[],
    )
    write_transcript(
        path=path,
        records_dir=records_dir,
        artifact_store=store,
        conversation_id=CONV_ID,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=[
            Message(role="user", content="first"),
            Message(role="assistant", content="second"),
        ],
        artifacts=[],
    )

    text = path.read_text(encoding="utf-8")
    assert "first" in text
    assert "second" in text
