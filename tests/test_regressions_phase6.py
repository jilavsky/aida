"""Regression tests for the bugs found in the end-of-Phase-6 code review.

Each test here pins down a specific defect that was verified to exist
before its fix, so a later refactor can't quietly reintroduce it. Grouped by
the bug, not by the module, because several fixes touch more than one file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aida.artifacts.base import FileArtifact, ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.persistence.cleanup import delete_conversation
from aida.persistence.records import record_file_path, write_transcript
from aida.persistence.store import ConversationStore
from aida.providers.anthropic_ import to_anthropic_params
from aida.providers.base import Message, ToolCall
from aida.workspace.files import default_file_tools
from aida.workspace.safety import SafetyGuard

T = "2026-08-20T00:00:00"


# --- A: deleting a conversation must not delete the user's own files -------


def test_delete_conversation_keeps_files_outside_aidas_own_artifact_store(tmp_path: Path):
    """The bug: every recorded artifact path was unlinked, but the
    ``artifacts`` table also holds paths to files AIDA does not own — the
    user's source image that ``read_file`` reported, and the report
    ``write_file`` wrote into their target folder. Deleting a conversation
    (or the GUI's bulk "older than N days" cleanup) destroyed both."""
    store = ConversationStore(tmp_path / "aida.db")
    owned_dir = tmp_path / "aida-artifacts"
    artifact_store = ArtifactStore(base_dir=owned_dir)

    user_folder = tmp_path / "usaxs_data"
    user_folder.mkdir()
    user_source = user_folder / "figure_from_instrument.png"
    user_source.write_bytes(b"precious original data")
    user_report = user_folder / "analysis.md"
    user_report.write_text("# results", encoding="utf-8")

    conv = store.create_conversation(timestamp=T)
    aida_owned = artifact_store.save_image(ImageArtifact(data=b"plot", mime_type="image/png"))
    store.append_artifact_from_object(conv, aida_owned, call_id="c1", timestamp=T)
    store.append_artifact_from_object(
        conv,
        ImageArtifact(data=b"", mime_type="image/png", path=str(user_source)),
        call_id="c2",
        timestamp=T,
    )
    store.append_artifact_from_object(
        conv,
        FileArtifact(path=str(user_report), mime_type="text/markdown"),
        call_id="c3",
        timestamp=T,
    )

    result = delete_conversation(
        store, conv, records_dir=tmp_path / "records", artifacts_dir=owned_dir
    )

    assert user_source.exists(), "the user's own source image must survive conversation deletion"
    assert user_report.exists(), "a report written into the user's target folder must survive too"
    assert not Path(aida_owned.path).exists(), "AIDA's own artifact copy should still be cleaned up"
    assert result.deleted_artifact_files == [aida_owned.path]
    assert sorted(result.skipped_external_files) == sorted([str(user_source), str(user_report)])
    store.close()


def test_delete_conversation_is_not_fooled_by_a_symlink_into_the_store(tmp_path: Path):
    """Containment is checked on resolved paths, so a recorded path that
    merely *points into* the artifacts dir via a symlink elsewhere doesn't
    get treated as AIDA-owned (and vice versa)."""
    store = ConversationStore(tmp_path / "aida.db")
    owned_dir = tmp_path / "aida-artifacts"
    owned_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    real_file = outside / "user.png"
    real_file.write_bytes(b"user data")
    link = owned_dir / "looks-owned.png"
    link.symlink_to(real_file)

    conv = store.create_conversation(timestamp=T)
    store.append_artifact_from_object(
        conv,
        ImageArtifact(data=b"", mime_type="image/png", path=str(link)),
        call_id="c1",
        timestamp=T,
    )

    delete_conversation(store, conv, records_dir=tmp_path / "records", artifacts_dir=owned_dir)

    assert real_file.exists(), (
        "a symlink under the artifacts dir must not authorize deleting its target"
    )
    store.close()


# --- B: Anthropic parallel tool results go back in ONE user message --------


def test_parallel_tool_results_are_coalesced_into_one_user_message():
    """The bug: one user message per tool result. The Anthropic API wants
    every result for a single assistant turn's ``tool_use`` blocks in one
    user message; splitting them trains the model out of parallel tool
    calls — exactly the "plot all of these" pattern pyIrena MCP work uses."""
    messages = [
        Message(role="user", content="plot both"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="a1", name="plot", arguments={"n": 1}),
                ToolCall(id="a2", name="plot", arguments={"n": 2}),
            ],
        ),
        Message(role="tool", content="ok1", tool_call_id="a1", name="plot"),
        Message(role="tool", content="ok2", tool_call_id="a2", name="plot"),
        Message(role="user", content="thanks"),
    ]

    _system, out = to_anthropic_params(messages)

    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "user", "user"]
    tool_result_message = out[2]
    blocks = tool_result_message["content"]
    assert [b["type"] for b in blocks] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in blocks] == ["a1", "a2"], "order must be preserved"


def test_single_tool_result_still_produces_one_block():
    messages = [
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a1", name="t")]),
        Message(role="tool", content="done", tool_call_id="a1", name="t"),
    ]
    _system, out = to_anthropic_params(messages)
    assert len(out) == 2
    assert out[1]["content"] == [{"type": "tool_result", "tool_use_id": "a1", "content": "done"}]


def test_empty_tool_result_content_is_replaced():
    """The API rejects an empty tool_result content block."""
    messages = [
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a1", name="t")]),
        Message(role="tool", content="", tool_call_id="a1", name="t"),
    ]
    _system, out = to_anthropic_params(messages)
    assert out[1]["content"][0]["content"] == "(no output)"


def test_tool_results_from_separate_turns_are_not_merged():
    """Coalescing must only span *consecutive* tool messages — two separate
    assistant turns keep their own result messages."""
    messages = [
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a1", name="t")]),
        Message(role="tool", content="r1", tool_call_id="a1", name="t"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a2", name="t")]),
        Message(role="tool", content="r2", tool_call_id="a2", name="t"),
    ]
    _system, out = to_anthropic_params(messages)
    assert [m["role"] for m in out] == ["assistant", "user", "assistant", "user"]
    assert len(out[1]["content"]) == 1
    assert len(out[3]["content"]) == 1


# --- C: artifact filenames are untrusted input ------------------------------


def test_artifact_filename_cannot_escape_the_store(tmp_path: Path):
    """An MCP server controls ``filename`` (via ResourceLink.name / the
    mime-derived audio name). ``../../escaped.txt`` used to write outside
    ``~/.aida/artifacts/`` entirely."""
    base = tmp_path / "artifacts"
    store = ArtifactStore(base_dir=base)

    artifact = store.save_file(FileArtifact(data=b"x", filename="../../escaped.txt"))

    written = Path(artifact.path).resolve()
    assert written.parent == base.resolve()
    assert written.name == "escaped.txt"
    assert not (tmp_path.parent / "escaped.txt").exists()


@pytest.mark.parametrize("hostile", ["/etc/passwd", "..", ".", "sub/dir/x.png", "win\\dir\\y.png"])
def test_artifact_filenames_reduce_to_a_bare_basename(tmp_path: Path, hostile: str):
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    artifact = store.save_file(FileArtifact(data=b"x", filename=hostile))
    assert Path(artifact.path).parent.resolve() == (tmp_path / "artifacts").resolve()


def test_same_named_artifacts_do_not_overwrite_each_other(tmp_path: Path):
    """``aida.mcp.results`` names every audio block ``audio.<subtype>``, so
    two of them in one conversation collided and the first one's bytes were
    replaced by the second's."""
    store = ArtifactStore(base_dir=tmp_path / "artifacts")

    first = store.save_file(FileArtifact(data=b"one", filename="audio.wav"))
    second = store.save_file(FileArtifact(data=b"two", filename="audio.wav"))

    assert first.path != second.path
    assert Path(first.path).read_bytes() == b"one"
    assert Path(second.path).read_bytes() == b"two"


# --- E: sidecar copies -------------------------------------------------------


def test_copy_to_target_is_idempotent_for_identical_content(tmp_path: Path):
    """``write_transcript`` re-copies the same images on *every* transcript
    export (i.e. after every message). Uniquifying unconditionally would
    grow ``fig (1).png``, ``fig (2).png``, ... without bound."""
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    artifact = store.save_image(ImageArtifact(data=b"same", mime_type="image/png"))
    target = tmp_path / "sidecar"

    first = store.copy_to_target(artifact, target)
    for _ in range(5):
        again = store.copy_to_target(artifact, target)
        assert again == first
    assert len(list(target.iterdir())) == 1


def test_copy_to_target_keeps_both_when_content_differs(tmp_path: Path):
    """Two different figures sharing a basename used to collapse into one
    image in the report."""
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    target = tmp_path / "sidecar"

    a = store.save_image(
        ImageArtifact(data=b"figure-a", mime_type="image/png", filename="plot.png")
    )
    b = store.save_image(
        ImageArtifact(data=b"figure-b", mime_type="image/png", filename="plot.png")
    )
    # Force the collision at the *target* even though the store already
    # uniquified the sources. Path.replace() (not .rename()) because the
    # destination already exists here (it's `a`'s own stored file) —
    # os.rename()/Path.rename() only overwrite an existing destination on
    # POSIX; on Windows it raises FileExistsError instead. Path.replace()
    # wraps os.replace(), documented to overwrite atomically on both.
    dest_a = store.copy_to_target(a, target)
    Path(b.path).replace(Path(b.path).parent / dest_a.name)
    b.path = str(Path(b.path).parent / dest_a.name)
    dest_b = store.copy_to_target(b, target)

    assert dest_a != dest_b
    assert dest_a.read_bytes() == b"figure-a"
    assert dest_b.read_bytes() == b"figure-b"


def test_transcript_links_point_at_the_real_sidecar_filenames(tmp_path: Path):
    """The transcript built its image links from the *source* basename. Now
    that a genuine collision renames the copy, the link has to follow."""
    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    records_dir = tmp_path / "records"
    conv = store.create_conversation(timestamp=T, sidecar_dirname="figures")

    a = artifact_store.save_image(
        ImageArtifact(data=b"aaa", mime_type="image/png", filename="plot.png")
    )
    b = artifact_store.save_image(
        ImageArtifact(data=b"bbb", mime_type="image/png", filename="plot.png")
    )
    Path(b.path).rename(Path(b.path).parent / "plot.png_tmp")
    # Path.replace(), not .rename(): the destination (a's own "plot.png")
    # already exists — see test_copy_to_target_keeps_both_when_content_differs's
    # comment for why .rename() alone fails on Windows here.
    (Path(b.path).parent / "plot.png_tmp").replace(Path(a.path).parent / "plot.png")
    # both artifacts now claim the basename "plot.png" from different dirs
    b.path = str(Path(a.path).parent / "plot.png")

    for artifact, call in ((a, "c1"), (b, "c2")):
        store.append_artifact_from_object(conv, artifact, call_id=call, timestamp=T)
        store.append_message(
            conv,
            Message(role="tool", content="[image]", tool_call_id=call, name="plot"),
            timestamp=T,
        )

    path = record_file_path(records_dir, conv, None)
    write_transcript(
        path=path,
        records_dir=records_dir,
        artifact_store=artifact_store,
        conversation_id=conv,
        title=None,
        workspace_name=None,
        profile_name=None,
        messages=store.load_messages(conv),
        artifacts=store.load_artifacts(conv),
        sidecar_dirname="figures",
    )

    text = path.read_text(encoding="utf-8")
    sidecar = records_dir / "figures" / conv[:8]
    linked = [
        line.split("(")[-1].rstrip(")").split("/")[-1]
        for line in text.splitlines()
        if line.startswith("![")
    ]
    assert len(linked) == 2
    for name in linked:
        assert (sidecar / name).exists(), f"transcript links {name} but no such file was copied"
    store.close()


# --- G: search_text truncation marker ---------------------------------------


def test_search_text_reports_truncation_when_the_last_file_fills_the_quota(tmp_path: Path):
    """Hitting the cap on the final candidate file left the loop with
    nothing after it to notice, so a capped result set looked complete."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "only.txt").write_text("\n".join(["needle"] * 20), encoding="utf-8")

    guard = SafetyGuard(allowed_roots=[root], mode="relaxed")
    tools = default_file_tools(guard, max_search_matches=5)
    result = asyncio.run(tools["search_text"].func({"path": str(root), "query": "needle"}))

    table = result.artifacts[0]
    assert len(table.rows) == 6, "5 matches plus one truncation marker row"
    assert "truncated at 5" in str(table.rows[-1][0])
