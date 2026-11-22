"""Caption pairing from page Markdown in reading order.

A vision model transcribing a page writes each figure as an image
placeholder (``![fig-1](fig-1)``) followed by its caption, in **reading
order**. On a two-column page that is exactly what the built-in extractor
(``aida.documents.figures``) cannot know: it pairs an image with the
nearest caption text, which is regularly the neighbouring column. The
functions here take the figures the built-in extractor already wrote and
replace their captions with the ones the page Markdown puts next to each
placeholder.
"""

from __future__ import annotations

import dataclasses
import re

from aida.documents.figures import FigureEntry

IMAGE_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
CAPTION_RE = re.compile(
    r"^\s*(?:\*\*|_)?(?P<kind>Fig(?:ure)?|Table|Scheme|Chart|Plate)\s*\.?\s*"
    r"(?P<number>[0-9]+[a-z]?)",
    re.IGNORECASE,
)


def caption_near(lines: list[str], position: int) -> str:
    """The caption for an image placeholder at ``lines[position]``: the next
    non-empty line if it reads as a caption, else the previous one (tables
    put theirs above). A non-caption neighbour stops the search."""
    for step in (1, -1):
        index = position + step
        while 0 <= index < len(lines):
            candidate = lines[index].strip()
            if candidate:
                if CAPTION_RE.match(candidate):
                    return candidate.strip("*_ ")
                break
            index += step
    return ""


def label_from_caption(caption: str) -> str:
    """The label of a caption line ("Figure 3", "Table 2"), or ``""``."""
    match = CAPTION_RE.match(caption)
    if not match:
        return ""
    kind = match.group("kind").lower()
    kind = "Figure" if kind.startswith("fig") else kind.capitalize()
    return f"{kind} {match.group('number')}"


def captions_in_reading_order(markdown: str) -> list[str]:
    """One entry per image placeholder in ``markdown``, in order: its caption,
    or ``""`` when none is next to it."""
    lines = markdown.splitlines()
    captions: list[str] = []
    for position, line in enumerate(lines):
        for _ in IMAGE_PLACEHOLDER_RE.findall(line):
            captions.append(caption_near(lines, position))
    return captions


def apply_page_captions(
    entries: list[FigureEntry], page_markdown: dict[int, str]
) -> tuple[list[FigureEntry], list[int]]:
    """Re-caption the built-in extractor's ``entries`` from ``page_markdown``
    (1-based page number -> Markdown of that page).

    A page is used only when its placeholder count equals the number of
    entries on it; otherwise the pairing would be a guess, and the built-in
    result is kept. A page with one figure becomes ``confidence="high"``;
    with several, figures are matched in order and keep the built-in
    extractor's confidence, since the image order on the page is not
    guaranteed to be the reading order. Returns the new list and the pages
    that were re-captioned.
    """
    by_page: dict[int, list[int]] = {}
    for index, entry in enumerate(entries):
        by_page.setdefault(entry.page, []).append(index)
    result = list(entries)
    used: list[int] = []
    for page, markdown in page_markdown.items():
        indices = by_page.get(page, [])
        captions = captions_in_reading_order(markdown)
        if not indices or len(captions) != len(indices):
            continue
        for index, caption in zip(indices, captions, strict=True):
            label = label_from_caption(caption)
            if not label:
                continue
            confidence = "high" if len(indices) == 1 else entries[index].confidence
            result[index] = dataclasses.replace(
                entries[index], label=label, caption=caption, confidence=confidence
            )
        used.append(page)
    return result, used


__all__ = [
    "CAPTION_RE",
    "IMAGE_PLACEHOLDER_RE",
    "apply_page_captions",
    "caption_near",
    "captions_in_reading_order",
    "label_from_caption",
]
