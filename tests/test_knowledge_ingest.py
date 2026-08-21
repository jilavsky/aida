"""Tests for aida.knowledge.rag.ingest — a tiny fixture corpus + the
deterministic MockEmbeddings fake embedder, no network."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from aida.config.settings import KnowledgeBaseConfig
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.ingest import (
    EMBED_BATCH_SIZE,
    INGESTIBLE_SUFFIXES,
    normalize_source_folder,
    rebuild,
    update,
)
from aida.providers.mock_embeddings import MockEmbeddings


def _kb(source_folder: Path, **overrides) -> KnowledgeBaseConfig:
    defaults = dict(name="test-kb", source_folders=[str(source_folder)], embedding_profile="mock")
    defaults.update(overrides)
    return KnowledgeBaseConfig(**defaults)


@pytest.mark.asyncio
async def test_rebuild_ingests_every_discovered_file(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nContent A.")
    (corpus / "b.txt").write_text("Content B.")
    (corpus / "ignored.png").write_bytes(b"\x89PNG")  # not an ingestible suffix

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())

    assert len(result.added_files) == 2
    assert result.chunk_count == 2
    assert kb_index.chunk_count(conn) == 2
    conn.close()


# --- a source_folders entry pointing at a single file, not a folder --------
# (real request: "index just this one file" without making a folder for it —
# previously reported as a missing folder even though the file existed.)


@pytest.mark.asyncio
async def test_rebuild_ingests_a_single_file_source_entry(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    single_file = corpus / "0 Instrument devise notes.md"
    single_file.write_text("# Instrument\n\nDevice notes content.")

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(single_file), MockEmbeddings())

    assert result.missing_folders == []
    assert result.added_files == [str(single_file)]
    assert result.chunk_count == 1
    conn.close()


@pytest.mark.asyncio
async def test_rebuild_reports_a_single_file_entry_with_a_non_ingestible_suffix_as_missing(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    image = corpus / "diagram.png"
    image.write_bytes(b"\x89PNG")

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(image), MockEmbeddings())

    # The path itself exists — it's just not a text format this can chunk —
    # so it's not reported as "missing"; it simply contributes no chunks.
    assert result.missing_folders == []
    assert result.added_files == []
    assert result.chunk_count == 0
    conn.close()


@pytest.mark.asyncio
async def test_update_skips_an_unchanged_single_file_entry(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    single_file = corpus / "notes.md"
    single_file.write_text("# Notes\n\nContent.")

    conn = kb_index.connect(tmp_path / "kb.db")
    kb = _kb(single_file)
    await rebuild(conn, kb, MockEmbeddings())

    embedder = MockEmbeddings()
    result = await update(conn, kb, embedder)

    assert result.added_files == []
    assert embedder.calls == [], "an unchanged single-file entry must not be re-embedded"
    conn.close()


@pytest.mark.asyncio
async def test_rebuild_prunes_files_no_longer_present(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    doomed = corpus / "will_be_deleted.md"
    doomed.write_text("# Doomed\n\nThis file will be removed before the next rebuild.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())
    assert kb_index.chunk_count(conn) == 1

    doomed.unlink()
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())

    assert str(doomed) in result.removed_files
    assert kb_index.chunk_count(conn) == 0
    conn.close()


@pytest.mark.asyncio
async def test_update_skips_unchanged_files_no_reembedding(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nContent A.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())

    embedder = MockEmbeddings()
    result = await update(conn, _kb(corpus), embedder)

    assert result.added_files == []
    assert result.updated_files == []
    assert embedder.calls == [], "an unchanged file must not be re-embedded"
    conn.close()


@pytest.mark.asyncio
async def test_update_reembeds_only_the_touched_file(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    a = corpus / "a.md"
    a.write_text("# A\n\nOriginal content A.")
    b = corpus / "b.md"
    b.write_text("# B\n\nContent B, never touched.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())

    # Ensure a strictly later mtime than the first write.
    time.sleep(0.05)
    a.write_text("# A\n\nUpdated content A, now different.")

    embedder = MockEmbeddings()
    result = await update(conn, _kb(corpus), embedder)

    assert result.updated_files == [str(a)]
    assert result.added_files == []
    assert embedder.calls == [["Updated content A, now different."]] or all(
        "Updated content" in text for call in embedder.calls for text in call
    )
    stored = kb_index.all_chunks(conn)
    assert any("Updated content A" in c.text for c in stored)
    assert any("Content B" in c.text for c in stored), "the untouched file's chunks must survive"
    conn.close()


@pytest.mark.asyncio
async def test_update_adds_a_new_file_and_removes_a_deleted_one(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    a = corpus / "a.md"
    a.write_text("# A\n\nContent A.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus), MockEmbeddings())

    a.unlink()
    b = corpus / "b.md"
    b.write_text("# B\n\nContent B.")

    result = await update(conn, _kb(corpus), MockEmbeddings())

    assert result.added_files == [str(b)]
    assert result.removed_files == [str(a)]
    conn.close()


@pytest.mark.asyncio
async def test_ingest_skips_unreadable_file_without_aborting_the_pass(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "good.md").write_text("# Good\n\nReadable content.")
    bad = corpus / "bad.pdf"
    bad.write_bytes(b"not actually a pdf")  # will fail to parse

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())

    assert any("bad.pdf" in entry for entry in result.skipped_files)
    assert any("good.md" in entry for entry in result.added_files)
    conn.close()


def test_ingestible_suffixes_cover_planned_formats():
    for suffix in [".md", ".txt", ".rst", ".py", ".pdf", ".docx"]:
        assert suffix in INGESTIBLE_SUFFIXES


# --- real-use bug: a `file://` URI pasted into source_folders ---------------
# (Obsidian's "Copy as URI"/a file manager's "Copy Path" produces this; a
# knowledge base configured with one silently indexed zero files, with no
# error anywhere, because Path("file:///Users/...").is_dir() is just False.)


def test_normalize_source_folder_strips_file_uri_scheme():
    assert normalize_source_folder("file:///Users/jan/notes") == "/Users/jan/notes"


def test_normalize_source_folder_percent_decodes():
    assert normalize_source_folder("file:///Users/jan/USAXS%20notes") == "/Users/jan/USAXS notes"


def test_normalize_source_folder_leaves_plain_path_alone():
    assert normalize_source_folder("/Users/jan/notes") == "/Users/jan/notes"
    assert normalize_source_folder("  /Users/jan/notes  ") == "/Users/jan/notes"


def test_normalize_source_folder_strips_leading_slash_from_windows_drive_uri():
    """A Windows file URI is `file:///C:/Users/...` — urlparse().path keeps
    the leading slash, giving "/C:/Users/..." which PureWindowsPath parses
    as a *relative* path with a literal folder named "C:", not the C:
    drive. Must be stripped so a Windows user's pasted URI round-trips to a
    real absolute path instead of silently failing the same way the
    un-normalized URI did before this module existed."""
    assert normalize_source_folder("file:///C:/Users/jan/notes") == "C:/Users/jan/notes"
    assert normalize_source_folder("file:///C:/Users/jan/USAXS%20notes") == "C:/Users/jan/USAXS notes"


def test_normalize_source_folder_never_silently_falls_back_to_the_cwd():
    """A malformed file:// string — found via a Windows CI test bug where
    f"file://{windows_path}" glued backslashes straight onto the scheme
    with no "/" boundary — leaves urlparse() with an empty path component.
    Path("") silently resolves to the current working directory; returning
    the unmodified original string instead means the entry fails
    _folder_is_usable and gets reported as missing, rather than silently
    ingesting whatever directory happens to be the cwd."""
    malformed = "file://C:\\Users\\runneradmin\\AppData\\Local\\Temp\\corpus"
    assert normalize_source_folder(malformed) == malformed


@pytest.mark.asyncio
async def test_rebuild_ingests_a_folder_configured_as_a_file_uri(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nContent A.")

    conn = kb_index.connect(tmp_path / "kb.db")
    # Path.as_uri() (not f"file://{corpus}") — a real file:// URI needs
    # forward slashes and, on Windows, a leading slash before the drive
    # letter ("file:///C:/Users/..."); naively f-stringing a WindowsPath
    # produces "file://C:\\Users\\..." which urlparse can't parse into a
    # path at all (no "/" after the scheme means the whole remainder is
    # read as netloc, not path) — normalize_source_folder silently fell
    # back to "" -> the current working directory, and ingest picked up
    # the entire repo (169 files) instead of the fixture corpus.
    kb = _kb(corpus, source_folders=[corpus.as_uri()])
    result = await rebuild(conn, kb, MockEmbeddings())

    assert len(result.added_files) == 1
    assert result.missing_folders == []
    conn.close()


@pytest.mark.asyncio
async def test_rebuild_reports_a_folder_that_does_not_exist(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(missing), MockEmbeddings())

    assert result.missing_folders == [str(missing)]
    assert result.added_files == []
    assert result.chunk_count == 0


@pytest.mark.asyncio
async def test_rebuild_with_one_good_and_one_missing_folder_ingests_the_good_one(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nContent A.")
    missing = tmp_path / "does-not-exist"

    conn = kb_index.connect(tmp_path / "kb.db")
    kb = _kb(corpus, source_folders=[str(corpus), str(missing)])
    result = await rebuild(conn, kb, MockEmbeddings())

    assert len(result.added_files) == 1
    assert result.missing_folders == [str(missing)]
    conn.close()


@pytest.mark.skipif(sys.platform == "win32", reason="chmod-based permission denial isn't meaningful on Windows")
@pytest.mark.asyncio
async def test_rebuild_reports_a_folder_it_cannot_actually_list(tmp_path: Path):
    """A folder can pass `is_dir()` while still being unreadable — the case
    found investigating a real report: a cloud-synced (iCloud Drive)
    Obsidian vault raised `PermissionError` the moment something tried to
    enumerate it, well after the `file://` URI bug (fixed separately) had
    already been ruled out. Simulated here with chmod rather than an actual
    cloud mount."""
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    (unreadable / "a.md").write_text("# A\n\nContent A.")
    os.chmod(unreadable, 0o000)
    try:
        conn = kb_index.connect(tmp_path / "kb.db")
        result = await rebuild(conn, _kb(unreadable), MockEmbeddings())

        assert result.missing_folders == [str(unreadable)]
        assert result.added_files == []
        conn.close()
    finally:
        os.chmod(unreadable, 0o755)  # so tmp_path cleanup can actually remove it


# --- embedding batching ----------------------------------------------------
#
# Review finding: every chunk of a file went into one embeddings.create
# call. A 200-page PDF at chunk_size 1000 is ~1000 inputs in a single
# request — over Ollama's practical limit and near OpenAI's — so the request
# failed and, since ingest records a failing file as skipped, the whole
# document was silently dropped from the index.


class _BatchRecordingEmbeddings(MockEmbeddings):
    """MockEmbeddings that remembers the size of each request it received."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.batch_sizes: list[int] = []

    async def embed(self, texts):
        self.batch_sizes.append(len(texts))
        return await super().embed(texts)


@pytest.mark.asyncio
async def test_a_large_file_is_embedded_in_bounded_batches(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # ~300 chunks at chunk_size 100.
    (corpus / "big.txt").write_text("\n\n".join(f"paragraph {i} " + "x" * 90 for i in range(300)))

    embedder = _BatchRecordingEmbeddings()
    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(corpus, chunk_size=100, chunk_overlap=10), embedder)
    conn.close()

    assert result.skipped_files == []
    assert result.chunk_count > EMBED_BATCH_SIZE
    assert len(embedder.batch_sizes) > 1  # actually split, not one giant call
    assert max(embedder.batch_sizes) <= EMBED_BATCH_SIZE
    assert sum(embedder.batch_sizes) == result.chunk_count  # every chunk embedded exactly once


@pytest.mark.asyncio
async def test_batching_preserves_chunk_order(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "big.txt").write_text("\n\n".join(f"paragraph {i} " + "y" * 90 for i in range(150)))

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(corpus, chunk_size=100, chunk_overlap=10), MockEmbeddings())
    rows = conn.execute(
        "SELECT chunk_index FROM chunks WHERE source_path = ? ORDER BY rowid",
        [str(corpus / "big.txt")],
    ).fetchall()
    conn.close()

    assert [row[0] for row in rows] == sorted(row[0] for row in rows)


# --- a file that disappears mid-pass --------------------------------------
#
# Review finding: this path.stat() sat outside the try, so a file deleted
# between discovery and the mtime check raised straight out of _run_ingest
# and aborted the entire pass, rather than skipping the one file.


@pytest.mark.asyncio
async def test_a_file_deleted_between_discovery_and_stat_skips_only_that_file(
    tmp_path: Path, monkeypatch
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("Content A.")
    doomed = corpus / "gone.txt"
    doomed.write_text("Content B.")
    (corpus / "c.txt").write_text("Content C.")

    # Let discovery see the file (that pass stats it once, via is_file()),
    # then make it vanish — the exact window the bug lived in.
    real_stat = Path.stat
    seen: dict[str, int] = {}

    def _stat(self, *args, **kwargs):
        if self == doomed:
            seen[str(self)] = seen.get(str(self), 0) + 1
            if seen[str(self)] > 1:
                raise FileNotFoundError(2, "No such file or directory", str(doomed))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _stat)

    conn = kb_index.connect(tmp_path / "kb.db")
    result = await rebuild(conn, _kb(corpus), MockEmbeddings())
    conn.close()

    assert sorted(Path(p).name for p in result.added_files) == ["a.txt", "c.txt"]
    assert len(result.skipped_files) == 1
    assert "gone.txt" in result.skipped_files[0]
