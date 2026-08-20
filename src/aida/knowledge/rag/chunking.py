"""Split extracted document text into retrievable chunks (Phase 8).

Two entry points: ``chunk_markdown`` (heading-aware — a ``.md`` file's own
``#``/``##``/... structure becomes chunk boundaries, so retrieval can report
which section a passage came from) and ``chunk_plain_text`` (paragraph-
boundary splitting for everything else — PDF/DOCX-extracted prose, plain
``.txt``, code). Both respect a ``chunk_size`` character budget with
``overlap`` characters of trailing context carried into the next chunk, so
a sentence spanning a boundary isn't orphaned on either side.

An Obsidian vault needs nothing extra here: it's just a folder of ``.md``
files, and wikilinks (``[[Note]]``) are plain text as far as chunking is
concerned — no vault-specific parsing (PLAN.md's "Obsidian vault as a
first-class source type" is satisfied by pointing a knowledge base's
``source_folders`` at the vault, not by a separate ingestion path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


@dataclass
class Chunk:
    """One retrievable piece of a source document."""

    text: str
    heading: str | None
    chunk_index: int


def _split_by_size(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """Split ``text`` into <=``chunk_size`` pieces on paragraph boundaries
    where possible, carrying ``overlap`` trailing characters from one piece
    into the next. A single paragraph longer than ``chunk_size`` on its own
    (a huge unbroken block of PDF-extracted text, for instance) still gets
    a hard split in a second pass — paragraph boundaries are preferred, not
    guaranteed."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size or not current:
            current = candidate
        else:
            pieces.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{para}" if tail else para
    if current:
        pieces.append(current)

    final: list[str] = []
    for piece in pieces:
        if len(piece) <= chunk_size:
            final.append(piece)
            continue
        start = 0
        while start < len(piece):
            end = start + chunk_size
            final.append(piece[start:end])
            start = end - overlap if overlap else end
    return final


def chunk_plain_text(
    text: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[Chunk]:
    """Paragraph/size-boundary chunking for non-Markdown text — no heading
    structure to key off of, so ``heading`` is always ``None``."""
    return [
        Chunk(text=piece, heading=None, chunk_index=i)
        for i, piece in enumerate(_split_by_size(text, chunk_size=chunk_size, overlap=overlap))
    ]


def chunk_markdown(
    text: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[Chunk]:
    """Heading-aware chunking for Markdown. Splits on ATX heading (``#``
    .. ``######``) boundaries first, then further splits any section still
    over ``chunk_size`` the same way ``chunk_plain_text`` does. Text before
    the first heading (or a document with no headings at all) is chunked
    as a headingless section rather than dropped or erroring.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return chunk_plain_text(text, chunk_size=chunk_size, overlap=overlap)

    sections: list[tuple[str | None, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        heading_line = f"{match.group(1)} {heading}"
        sections.append((heading, f"{heading_line}\n\n{body}" if body else heading_line))

    chunks: list[Chunk] = []
    index = 0
    for heading, section_text in sections:
        for piece in _split_by_size(section_text, chunk_size=chunk_size, overlap=overlap):
            chunks.append(Chunk(text=piece, heading=heading, chunk_index=index))
            index += 1
    return chunks


__all__ = ["Chunk", "DEFAULT_CHUNK_OVERLAP", "DEFAULT_CHUNK_SIZE", "chunk_markdown", "chunk_plain_text"]
