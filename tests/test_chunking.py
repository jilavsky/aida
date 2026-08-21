"""Tests for aida.knowledge.rag.chunking."""

from __future__ import annotations

from aida.knowledge.rag.chunking import chunk_markdown, chunk_plain_text, normalize_chunk_params


def test_plain_text_short_enough_is_one_chunk():
    chunks = chunk_plain_text("A short paragraph.", chunk_size=1000)
    assert len(chunks) == 1
    assert chunks[0].text == "A short paragraph."
    assert chunks[0].heading is None
    assert chunks[0].chunk_index == 0


def test_plain_text_empty_produces_no_chunks():
    assert chunk_plain_text("") == []
    assert chunk_plain_text("   \n\n  ") == []


def test_plain_text_splits_on_paragraph_boundaries():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_plain_text(text, chunk_size=30, overlap=0)
    assert len(chunks) > 1
    assert all(len(c.text) <= 40 for c in chunks)  # some slack for overlap-free short splits


def test_plain_text_overlap_carries_trailing_context():
    text = "Paragraph one is here.\n\nParagraph two is here.\n\nParagraph three is here."
    chunks = chunk_plain_text(text, chunk_size=35, overlap=10)
    # Some suffix of an earlier chunk should reappear at the start of the next.
    assert any(chunks[i].text[:10] in chunks[i - 1].text for i in range(1, len(chunks)))


def test_plain_text_hard_splits_a_single_oversized_paragraph():
    huge_paragraph = "word " * 500  # one paragraph, way over any reasonable chunk_size
    chunks = chunk_plain_text(huge_paragraph, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)


def test_markdown_splits_on_headings():
    text = "# Intro\n\nIntro text.\n\n## Details\n\nDetail text.\n\n## More\n\nMore text."
    chunks = chunk_markdown(text, chunk_size=1000)
    headings = [c.heading for c in chunks]
    assert headings == ["Intro", "Details", "More"]


def test_markdown_preamble_before_first_heading_is_kept():
    text = "Some preamble text.\n\n# First Heading\n\nBody."
    chunks = chunk_markdown(text, chunk_size=1000)
    assert chunks[0].heading is None
    assert "preamble" in chunks[0].text.lower()
    assert chunks[1].heading == "First Heading"


def test_markdown_with_no_headings_falls_back_to_plain_chunking():
    text = "Just prose, no headings at all, split across two paragraphs.\n\nSecond paragraph here."
    chunks = chunk_markdown(text, chunk_size=1000)
    assert len(chunks) == 1
    assert chunks[0].heading is None


def test_markdown_oversized_section_still_gets_split():
    text = "# Big Section\n\n" + ("Detail sentence. " * 200)
    chunks = chunk_markdown(text, chunk_size=300, overlap=30)
    assert len(chunks) > 1
    assert all(c.heading == "Big Section" for c in chunks)


def test_markdown_chunk_index_is_sequential_across_sections():
    text = "# A\n\nBody A.\n\n# B\n\nBody B."
    chunks = chunk_markdown(text, chunk_size=1000)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_markdown_handles_headings_up_to_level_six():
    text = "###### Deep Heading\n\nDeep body text."
    chunks = chunk_markdown(text, chunk_size=1000)
    assert chunks[0].heading == "Deep Heading"


# --- overlap >= chunk_size used to spin forever ----------------------------
#
# Review finding: the hard-split loop advanced by (chunk_size - overlap)
# characters, so an overlap at or above the chunk size never advanced and
# appended a piece on every pass until memory ran out. Reachable straight
# from the Knowledge Bases dialog (chunk size spins down to 100, overlap up
# to 100,000, independently) and from `aida kb add --chunk-size 100`; ingest
# runs on the GUI's shared AsyncLoopThread, so it took the chat session with
# it. These tests are the ones that would hang the suite on the old code —
# pytest's 30s global timeout is the backstop.


def test_overlap_equal_to_chunk_size_terminates():
    chunks = chunk_plain_text("word " * 400, chunk_size=100, overlap=100)
    assert 0 < len(chunks) < 10_000


def test_overlap_larger_than_chunk_size_terminates():
    chunks = chunk_plain_text("word " * 400, chunk_size=100, overlap=100_000)
    assert 0 < len(chunks) < 10_000


def test_markdown_with_a_degenerate_overlap_terminates():
    text = "# Heading\n\n" + ("sentence. " * 400)
    chunks = chunk_markdown(text, chunk_size=120, overlap=500)
    assert 0 < len(chunks) < 10_000


def test_normalize_chunk_params_clamps_to_a_terminating_pair():
    assert normalize_chunk_params(100, 100) == (100, 99)
    assert normalize_chunk_params(100, 100_000) == (100, 99)
    assert normalize_chunk_params(0, 0) == (1, 0)
    assert normalize_chunk_params(1000, -5) == (1000, 0)
    assert normalize_chunk_params(1000, 150) == (1000, 150)  # a sane pair is untouched
