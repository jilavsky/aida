"""Tests for aida.workspace.files.default_file_tools — every tool
safety-gated, size-capped, and exercised directly the same way
tests/test_agent_loop.py drives ad hoc NativeTools (no AgentLoop needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import FileArtifact, JsonArtifact, TableArtifact, TextArtifact
from aida.workspace.files import default_file_tools
from aida.workspace.safety import ConfirmationRequest, SafetyGuard


async def _approve(_request: ConfirmationRequest) -> bool:
    return True


def _guard(root: Path, *, mode: str = "relaxed") -> SafetyGuard:
    return SafetyGuard(allowed_roots=[root], mode=mode, confirm_callback=_approve)


async def _call(tools, name: str, **arguments):
    return await tools[name].func(arguments)


# --- list_directory --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_directory_non_recursive(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "list_directory", path=str(tmp_path))
    assert not result.is_error
    table = result.artifacts[0]
    assert isinstance(table, TableArtifact)
    names = {row[0] for row in table.rows}
    assert names == {"a.txt", "sub"}


@pytest.mark.asyncio
async def test_list_directory_recursive_includes_nested_files(tmp_path: Path):
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_text("y")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "list_directory", path=str(tmp_path), recursive=True)
    table = result.artifacts[0]
    assert any("b.txt" in row[0] for row in table.rows)


@pytest.mark.asyncio
async def test_list_directory_excludes_trash_folder(tmp_path: Path):
    trash = tmp_path / "_trash"
    trash.mkdir()
    (trash / "gone.txt").write_text("bye")
    (tmp_path / "here.txt").write_text("hi")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "list_directory", path=str(tmp_path))
    names = {row[0] for row in result.artifacts[0].rows}
    assert "here.txt" in names
    assert "_trash" not in names


@pytest.mark.asyncio
async def test_list_directory_truncates_past_max_entries(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    tools = default_file_tools(_guard(tmp_path), max_list_entries=2)
    result = await _call(tools, "list_directory", path=str(tmp_path))
    table = result.artifacts[0]
    assert len(table.rows) == 3  # 2 real + 1 truncation marker
    assert "truncated" in table.rows[-1][0]


@pytest.mark.asyncio
async def test_list_directory_not_a_directory_is_error(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "list_directory", path=str(f))
    assert result.is_error


@pytest.mark.asyncio
async def test_list_directory_outside_allowed_folders_denied(tmp_path: Path):
    from aida.workspace.safety import deny_all

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    guard = SafetyGuard(allowed_roots=[allowed], confirm_callback=deny_all)
    tools = default_file_tools(guard)
    result = await _call(tools, "list_directory", path=str(outside))
    assert result.is_error  # ConfirmationDenied (confirm callback denies) -> caught -> error result


# --- find_files --------------------------------------------------------


@pytest.mark.asyncio
async def test_find_files_matches_glob_pattern(tmp_path: Path):
    (tmp_path / "a.csv").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.csv").write_text("x")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "find_files", path=str(tmp_path), pattern="*.csv")
    names = {row[0] for row in result.artifacts[0].rows}
    assert "a.csv" in names
    assert any("c.csv" in n for n in names)  # recursive by default
    assert "b.txt" not in names


@pytest.mark.asyncio
async def test_find_files_non_recursive(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.csv").write_text("x")
    (tmp_path / "a.csv").write_text("x")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "find_files", path=str(tmp_path), pattern="*.csv", recursive=False)
    names = {row[0] for row in result.artifacts[0].rows}
    assert names == {"a.csv"}


# --- search_text --------------------------------------------------------


@pytest.mark.asyncio
async def test_search_text_finds_matching_lines(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n")
    (tmp_path / "b.txt").write_text("nothing here\n")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "search_text", path=str(tmp_path), query="hello")
    rows = result.artifacts[0].rows
    assert len(rows) == 1
    assert rows[0][0] == "a.txt"
    assert rows[0][1] == 1


@pytest.mark.asyncio
async def test_search_text_case_insensitive_by_default(tmp_path: Path):
    (tmp_path / "a.txt").write_text("Hello World\n")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "search_text", path=str(tmp_path), query="hello")
    assert len(result.artifacts[0].rows) == 1


@pytest.mark.asyncio
async def test_search_text_case_sensitive_when_requested(tmp_path: Path):
    (tmp_path / "a.txt").write_text("Hello World\n")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "search_text", path=str(tmp_path), query="hello", case_sensitive=True)
    assert len(result.artifacts[0].rows) == 0


@pytest.mark.asyncio
async def test_search_text_skips_binary_files_without_crashing(tmp_path: Path):
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x01binary")
    (tmp_path / "a.txt").write_text("findme\n")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "search_text", path=str(tmp_path), query="findme")
    assert not result.is_error
    assert len(result.artifacts[0].rows) == 1


# --- read_file --------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_plain_text(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello there")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "read_file", path=str(tmp_path / "a.txt"))
    assert not result.is_error
    assert isinstance(result.artifacts[0], TextArtifact)
    assert result.artifacts[0].text == "hello there"
    assert "hello there" in result.content


@pytest.mark.asyncio
async def test_read_file_does_not_truncate_below_the_interactive_cap(tmp_path: Path):
    """Regression test: read_file used to hand read_document's already-
    truncated text through describe_for_model() with *its* smaller 4,000-
    char default, silently re-truncating anything over ~4KB (e.g. a real
    PDF/paper) long before read_document's own, much larger budget kicked
    in. A 10,000-char file — over the old 4,000-char ceiling but under the
    fixed 100,000-char interactive cap — must now come through whole."""
    text = "abcdefghij" * 1_000  # 10,000 chars
    (tmp_path / "a.txt").write_text(text)
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "read_file", path=str(tmp_path / "a.txt"))
    assert not result.is_error
    assert result.content == text
    assert "truncated" not in result.content


@pytest.mark.asyncio
async def test_read_file_still_truncates_above_the_interactive_cap(tmp_path: Path):
    """The larger interactive cap is still a cap, not an unbounded read —
    an unusually huge document (a thesis, a book) must still be truncated
    rather than context-bombing the model."""
    text = "x" * 150_000
    (tmp_path / "a.txt").write_text(text)
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "read_file", path=str(tmp_path / "a.txt"))
    assert not result.is_error
    assert "truncated" in result.content
    assert len(result.content) < len(text)


@pytest.mark.asyncio
async def test_read_file_on_a_directory_is_error(tmp_path: Path):
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "read_file", path=str(tmp_path))
    assert result.is_error


@pytest.mark.asyncio
async def test_read_file_unsupported_extension_becomes_error_result(tmp_path: Path):
    (tmp_path / "a.zip").write_bytes(b"PK\x03\x04")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "read_file", path=str(tmp_path / "a.zip"))
    assert result.is_error  # UnsupportedDocumentFormatError caught generically by AgentLoop's pattern... here directly by the tool call


@pytest.mark.asyncio
async def test_read_file_outside_allowed_folders_denied(tmp_path: Path):
    from aida.workspace.safety import deny_all

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    guard = SafetyGuard(allowed_roots=[allowed], confirm_callback=deny_all)
    tools = default_file_tools(guard)
    result = await _call(tools, "read_file", path=str(outside))
    assert result.is_error


# --- write_file --------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_creates_new_file(tmp_path: Path):
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "write_file", path=str(tmp_path / "out.txt"), content="new content")
    assert not result.is_error
    assert (tmp_path / "out.txt").read_text() == "new content"
    assert isinstance(result.artifacts[0], FileArtifact)


@pytest.mark.asyncio
async def test_write_file_creates_missing_parent_dirs(tmp_path: Path):
    tools = default_file_tools(_guard(tmp_path))
    target = tmp_path / "a" / "b" / "out.txt"
    result = await _call(tools, "write_file", path=str(target), content="hi")
    assert not result.is_error
    assert target.read_text() == "hi"


@pytest.mark.asyncio
async def test_write_file_refuses_to_clobber_without_overwrite(tmp_path: Path):
    existing = tmp_path / "out.txt"
    existing.write_text("original")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "write_file", path=str(existing), content="new")
    assert result.is_error
    assert existing.read_text() == "original"


@pytest.mark.asyncio
async def test_write_file_overwrite_true_replaces_content(tmp_path: Path):
    existing = tmp_path / "out.txt"
    existing.write_text("original")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "write_file", path=str(existing), content="new", overwrite=True)
    assert not result.is_error
    assert existing.read_text() == "new"


@pytest.mark.asyncio
async def test_write_file_confirm_mode_declined_raises_error_result(tmp_path: Path):
    from aida.workspace.safety import deny_all

    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confirm", confirm_callback=deny_all)
    tools = default_file_tools(guard)
    result = await _call(tools, "write_file", path=str(tmp_path / "out.txt"), content="x")
    assert result.is_error
    assert not (tmp_path / "out.txt").exists()


# --- create_directory --------------------------------------------------------


@pytest.mark.asyncio
async def test_create_directory(tmp_path: Path):
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "create_directory", path=str(tmp_path / "newdir" / "nested"))
    assert not result.is_error
    assert (tmp_path / "newdir" / "nested").is_dir()


# --- copy_file / move_file --------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_file(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("data")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "copy_file", source=str(src), destination=str(tmp_path / "dst.txt"))
    assert not result.is_error
    assert src.exists()
    assert (tmp_path / "dst.txt").read_text() == "data"


@pytest.mark.asyncio
async def test_move_file(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("data")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "move_file", source=str(src), destination=str(tmp_path / "dst.txt"))
    assert not result.is_error
    assert not src.exists()
    assert (tmp_path / "dst.txt").read_text() == "data"


@pytest.mark.asyncio
async def test_move_file_missing_source_is_error(tmp_path: Path):
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(
        tools, "move_file", source=str(tmp_path / "nope.txt"), destination=str(tmp_path / "dst.txt")
    )
    assert result.is_error


# --- delete_file --------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_moves_to_trash_by_default(tmp_path: Path):
    victim = tmp_path / "doomed.txt"
    victim.write_text("bye")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "delete_file", path=str(victim))
    assert not result.is_error
    assert not victim.exists()
    assert (tmp_path / "_trash" / "doomed.txt").exists()
    assert "trash" in result.content.lower()


# --- get_file_metadata --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_metadata(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "get_file_metadata", path=str(f))
    assert not result.is_error
    data = result.artifacts[0]
    assert isinstance(data, JsonArtifact)
    assert data.data["size_bytes"] == 5
    assert data.data["is_dir"] is False


@pytest.mark.asyncio
async def test_get_file_metadata_missing_path_is_error(tmp_path: Path):
    tools = default_file_tools(_guard(tmp_path))
    result = await _call(tools, "get_file_metadata", path=str(tmp_path / "nope.txt"))
    assert result.is_error


# --- _run_blocking timeout (PLAN.md "graceful handling of slow/missing network
# mounts (timeout + clear error)") -------------------------------------------
#
# Simulating a genuinely slow/hung network mount isn't practical in this
# sandbox — this instead verifies the timeout *mechanism itself* directly:
# a blocking call that legitimately takes longer than the given timeout
# raises a clear TimeoutError (not a hang, not a bare asyncio.TimeoutError
# with no context) — the same helper every bulk scan (list_directory,
# find_files, search_text) and file I/O call in this module goes through.


@pytest.mark.asyncio
async def test_run_blocking_raises_clear_timeout_error_on_a_slow_call():
    import time

    from aida.workspace.files import _run_blocking

    def _slow(*, seconds: float) -> None:
        time.sleep(seconds)

    with pytest.raises(TimeoutError, match="slow"):
        await _run_blocking(_slow, seconds=0.2, timeout=0.01)


@pytest.mark.asyncio
async def test_run_blocking_returns_normally_within_timeout():
    from aida.workspace.files import _run_blocking

    result = await _run_blocking(lambda *, value: value * 2, value=21, timeout=5.0)
    assert result == 42


# --- copy/move must not silently clobber ----------------------------------
#
# Review finding: write_file carefully refuses to replace an existing file
# without overwrite=true, but copy_file/move_file right beside it went
# straight through shutil and overwrote without asking — and unlike
# delete_file there is no _trash copy to recover from. An agent told to
# "copy the reduced data over" could destroy a file in the target folder
# with nothing to undo it.


@pytest.mark.asyncio
async def test_copy_file_refuses_to_overwrite_by_default(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("new")
    dst = tmp_path / "dst.txt"
    dst.write_text("precious")
    tools = default_file_tools(_guard(tmp_path))

    result = await _call(tools, "copy_file", source=str(src), destination=str(dst))

    assert result.is_error
    assert "overwrite=true" in result.content
    assert dst.read_text() == "precious"


@pytest.mark.asyncio
async def test_copy_file_overwrites_when_asked(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("new")
    dst = tmp_path / "dst.txt"
    dst.write_text("old")
    tools = default_file_tools(_guard(tmp_path))

    result = await _call(tools, "copy_file", source=str(src), destination=str(dst), overwrite=True)

    assert not result.is_error
    assert dst.read_text() == "new"


@pytest.mark.asyncio
async def test_move_file_refuses_to_overwrite_by_default(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("new")
    dst = tmp_path / "dst.txt"
    dst.write_text("precious")
    tools = default_file_tools(_guard(tmp_path))

    result = await _call(tools, "move_file", source=str(src), destination=str(dst))

    assert result.is_error
    assert dst.read_text() == "precious"
    assert src.exists()  # the source is left alone too


@pytest.mark.asyncio
async def test_move_file_overwrites_when_asked(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("new")
    dst = tmp_path / "dst.txt"
    dst.write_text("old")
    tools = default_file_tools(_guard(tmp_path))

    result = await _call(tools, "move_file", source=str(src), destination=str(dst), overwrite=True)

    assert not result.is_error
    assert dst.read_text() == "new"
    assert not src.exists()


@pytest.mark.asyncio
async def test_copy_into_a_directory_checks_the_file_that_would_be_written(tmp_path: Path):
    """shutil places a copy *inside* a directory destination, so the
    existence check has to resolve that path rather than test the folder."""
    src = tmp_path / "src.txt"
    src.write_text("new")
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    (target_dir / "src.txt").write_text("precious")
    tools = default_file_tools(_guard(tmp_path))

    result = await _call(tools, "copy_file", source=str(src), destination=str(target_dir))

    assert result.is_error
    assert (target_dir / "src.txt").read_text() == "precious"


@pytest.mark.asyncio
async def test_copy_into_a_directory_still_works_when_nothing_collides(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("new")
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    tools = default_file_tools(_guard(tmp_path))

    result = await _call(tools, "copy_file", source=str(src), destination=str(target_dir))

    assert not result.is_error
    assert (target_dir / "src.txt").read_text() == "new"
