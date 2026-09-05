"""Extracting a document's figures, and labelling them well enough to ask
for one by name.

The design constraint, from the discussion that produced this: **an
unlabelled figure is worse than no figure at all.** Handing a model a bag
of anonymous images it cannot name does not let it answer "what does
Figure 1 show" — it just spends tokens. So nothing here pushes images at
the model. Extraction produces an *index* — label, caption, page — and the
agent pulls the one or two it actually needs by label
(``aida.documents.tools``). That also turns
``aida.providers.vision.MAX_ATTACHED_IMAGES`` from a limitation into the
right budget: it now bounds a pull of two, not a truncated push of twelve.

Extraction is **lazy**: it happens the first time something asks for this
document's figures, not when the document is attached. A paper nobody asks
a figure question about costs nothing, and a 150-page PDF cannot stall the
turn it arrived on. Results are cached in the document's ``.assets``
folder, so the second question is free.

**Honest labelling matters more than complete labelling.** The label comes
from pairing an image with nearby caption text, which is reliable on
single-column documents and much less so on the two-column layouts most
journals use, where "the text block below this image" is regularly the
neighbouring column. Every entry therefore carries a ``confidence``, and a
low-confidence index says so in what the model reads, so a wrong guess can
be recognised as a guess. `planning/document_images.md` §4 records why an
OCR/layout backend would raise this ceiling.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from aida.config.logging_setup import get_logger

logger = get_logger(__name__)

INDEX_FILENAME = "index.json"

#: Smallest edge, in PDF points, an image must have on the page before it is
#: treated as a figure. A journal PDF is full of rules, logos, ornaments and
#: the publisher's mark repeated on every page; without this the index fills
#: with 20x20 specks and the real figures are lost in them.
MIN_FIGURE_EDGE_PT = 72.0

#: Most extreme width:height (or height:width) ratio still considered a
#: figure. Horizontal rules and sidebars pass the size test but are not
#: pictures of anything.
MAX_FIGURE_ASPECT = 12.0

#: How far below (or above) an image, in points, a caption may sit before it
#: stops being plausibly *its* caption.
CAPTION_SEARCH_PT = 120.0

_CAPTION_RE = re.compile(
    r"^\s*(?P<kind>Fig(?:ure)?|Table|Scheme|Chart|Plate)\s*\.?\s*(?P<number>[0-9]+[a-z]?)",
    re.IGNORECASE,
)


@dataclass
class FigureEntry:
    """One extracted figure and what we believe it is called."""

    #: "Figure 1", "Table 3" — or "image 2 (page 4)" when no caption was
    #: found. A positional fallback is still useful: it lets the agent ask
    #: for "the second image on page 4" rather than nothing at all.
    label: str
    #: The caption line, when one was matched. Empty otherwise.
    caption: str
    #: Filename inside the assets folder.
    file: str
    #: 1-based page number.
    page: int
    #: "high" when a caption was matched on a single-column page, "low" when
    #: matched on a multi-column one (where the pairing is a guess), "none"
    #: when no caption was found and the label is positional.
    confidence: str


def _looks_multi_column(blocks: list[tuple]) -> bool:
    """Whether a page's text blocks sit side by side.

    Two blocks that overlap vertically but not horizontally *are* columns —
    that is what the word means — so this needs no page-width heuristics or
    tuning constants. It matters because caption pairing degrades exactly
    here: "the text below this image" is frequently the next column, not
    the caption.
    """
    text_blocks = [b for b in blocks if len(b) > 6 and b[6] == 0]
    for i, a in enumerate(text_blocks):
        for b in text_blocks[i + 1 :]:
            vertical_overlap = min(a[3], b[3]) - max(a[1], b[1])
            horizontally_disjoint = a[2] < b[0] or b[2] < a[0]
            if vertical_overlap > 20 and horizontally_disjoint:
                return True
    return False


def _caption_for(rect, blocks: list[tuple]) -> str:
    """The caption text for an image at ``rect``, or "".

    Prefers a caption *below* the image, which is the dominant convention
    for figures, then falls back to above (where tables usually put theirs).
    Only text starting with a recognised caption word counts — an arbitrary
    nearby paragraph is not a label, and inventing one would be worse than
    admitting there is none.
    """
    below: list[tuple[float, str]] = []
    above: list[tuple[float, str]] = []
    for block in blocks:
        if len(block) < 7 or block[6] != 0:
            continue
        text = (block[4] or "").strip()
        if not text or not _CAPTION_RE.match(text):
            continue
        gap_below = block[1] - rect.y1
        gap_above = rect.y0 - block[3]
        if 0 <= gap_below <= CAPTION_SEARCH_PT:
            below.append((gap_below, text))
        elif 0 <= gap_above <= CAPTION_SEARCH_PT:
            above.append((gap_above, text))
    for candidates in (below, above):
        if candidates:
            return min(candidates)[1].splitlines()[0].strip()
    return ""


def _label_from_caption(caption: str) -> str:
    match = _CAPTION_RE.match(caption)
    if not match:
        return ""
    kind = match.group("kind").lower()
    kind = "Figure" if kind.startswith("fig") else kind.capitalize()
    return f"{kind} {match.group('number')}"


def extract_pdf_figures(pdf_path: Path, assets_dir: Path) -> list[FigureEntry]:
    """Extract every plausible figure from ``pdf_path`` into ``assets_dir``
    and return the index.

    Never raises: a document whose figures cannot be extracted (a missing
    ``docs`` extra, a damaged file, an unwritable folder) yields an empty
    index and a log line. The caller's job is to say "no figures could be
    read", never to fail the turn.
    """
    try:
        import pymupdf
    except ImportError:
        logger.info("figure extraction needs the 'docs' extra (pymupdf); skipping %s", pdf_path)
        return []

    entries: list[FigureEntry] = []
    seen_xrefs: set[int] = set()
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(pdf_path) as doc:
            for page_index, page in enumerate(doc):
                blocks = page.get_text("blocks")
                multi_column = _looks_multi_column(blocks)
                for image in page.get_images(full=True):
                    xref = image[0]
                    # One logo repeated on fourteen pages is one image, and
                    # the first page it appears on is the useful one.
                    if xref in seen_xrefs:
                        continue
                    rects = page.get_image_rects(xref)
                    if not rects:
                        continue
                    rect = max(rects, key=lambda r: r.width * r.height)
                    if min(rect.width, rect.height) < MIN_FIGURE_EDGE_PT:
                        continue
                    longest, shortest = max(rect.width, rect.height), min(rect.width, rect.height)
                    if shortest <= 0 or longest / shortest > MAX_FIGURE_ASPECT:
                        continue
                    seen_xrefs.add(xref)

                    extracted = doc.extract_image(xref)
                    filename = f"fig-{len(entries) + 1:02d}.{extracted.get('ext', 'png')}"
                    (assets_dir / filename).write_bytes(extracted["image"])

                    caption = _caption_for(rect, blocks)
                    label = _label_from_caption(caption)
                    if label:
                        confidence = "low" if multi_column else "high"
                    else:
                        label = f"image {len(entries) + 1} (page {page_index + 1})"
                        confidence = "none"
                    entries.append(
                        FigureEntry(
                            label=label,
                            caption=caption,
                            file=filename,
                            page=page_index + 1,
                            confidence=confidence,
                        )
                    )
    except Exception as exc:  # noqa: BLE001 - a figure index must never fail a turn
        logger.warning("could not extract figures from %s: %s", pdf_path, exc)
        return entries
    return entries


@dataclass
class FigureIndex:
    """A document's figures plus how they were found.

    ``backend`` and ``note`` exist so the model can be told *why* the
    labels are as good or as poor as they are — "extracted with OCR" and
    "OCR unavailable (no API key), used the built-in extractor" lead to
    very different levels of trust in a figure number, and hiding that
    difference would be the same mistake as hiding a low confidence.
    """

    source: str
    figures: list[FigureEntry]
    backend: str = "builtin"
    note: str = ""


def write_index(
    assets_dir: Path,
    source_name: str,
    entries: list[FigureEntry],
    *,
    backend: str = "builtin",
    note: str = "",
) -> Path:
    """Cache the index beside the extracted images. Written even when empty,
    so "we looked and found nothing" is distinguishable from "we have not
    looked yet" and a fruitless extraction is not repeated on every ask."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    path = assets_dir / INDEX_FILENAME
    path.write_text(
        json.dumps(
            {
                "source": source_name,
                "backend": backend,
                "note": note,
                "figures": [asdict(e) for e in entries],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def read_index(assets_dir: Path) -> FigureIndex | None:
    """The cached index, or ``None`` when this document has not been
    examined yet. A corrupt cache reads as "not examined" so the next ask
    rebuilds it rather than failing."""
    path = assets_dir / INDEX_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return FigureIndex(
            source=data.get("source", ""),
            figures=[FigureEntry(**entry) for entry in data.get("figures", [])],
            backend=data.get("backend", "builtin"),
            note=data.get("note", ""),
        )
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("ignoring unreadable figure index %s: %s", path, exc)
        return None


def describe_index(source_name: str, index: FigureIndex) -> str:
    """What the model reads. Says how sure the labels are, because a label
    presented as fact and a label presented as a guess lead to very
    different follow-up questions."""
    entries = index.figures
    if not entries:
        head = f"No figures could be extracted from {source_name}."
        return f"{head} {index.note}".strip() if index.note else head
    lines = [f"{len(entries)} figure(s) in {source_name}:"]
    for entry in entries:
        detail = f"  - {entry.label} (page {entry.page})"
        if entry.caption:
            detail += f" — {entry.caption}"
        lines.append(detail)
    if any(e.confidence == "low" for e in entries):
        lines.append(
            "Some labels are uncertain: this document has a multi-column layout, where a "
            "caption cannot reliably be matched to the image above it. Check the caption "
            "text against what you see before relying on a figure number."
        )
    if any(e.confidence == "none" for e in entries):
        lines.append(
            "Entries labelled 'image N' had no caption found near them; their numbering is "
            "positional, not the document's own."
        )
    if index.note:
        lines.append(index.note)
    lines.append("Call get_document_figure with a label to view one.")
    return "\n".join(lines)


__all__ = [
    "INDEX_FILENAME",
    "FigureEntry",
    "FigureIndex",
    "describe_index",
    "extract_pdf_figures",
    "read_index",
    "write_index",
]
