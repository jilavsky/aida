"""Tests for aida.knowledge.rag.chunking."""

from __future__ import annotations

from aida.knowledge.rag.chunking import chunk_markdown, chunk_plain_text


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
