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
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

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

# --- docx ------------------------------------------------------------------
#
# A .docx is a zip: the body is ``word/document.xml``, the pictures are
# whole files under ``word/media/``, and ``word/_rels/document.xml.rels``
# maps the relationship id in the body to the file on disk. So extraction
# here is stdlib zipfile + ElementTree and needs no new dependency —
# ``python-docx`` (the ``docs`` extra) is not even imported, because it
# gives no better answer to "which paragraph is this picture in", which is
# the whole problem. Word has no page numbers without laying the document
# out, so ``page`` is 0 for these entries and the description says
# "in document order" instead of a page.

_DOCX_BODY = "word/document.xml"
_DOCX_RELS = "word/_rels/document.xml.rels"
_DOCX_MEDIA_PREFIX = "word/media/"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: English Metric Units per point — Word stores drawing sizes in EMU
#: (914400 per inch, 72 points per inch), so this converts a ``wp:extent``
#: into the same units MIN_FIGURE_EDGE_PT is written in.
EMU_PER_PT = 12700

#: Formats worth handing to a vision model. Word also stores EMF/WMF
#: (pasted vector art, chart fallbacks) and SVG companions next to a PNG
#: of the same picture; those are skipped rather than indexed as figures
#: nothing can display — and the index note says how many were skipped, so
#: "this document has one more figure than you listed" is answerable.
_DOCX_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


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


def _docx_media_by_rel_id(archive: zipfile.ZipFile) -> dict[str, str]:
    """``rId7 -> word/media/image3.png`` for every image relationship."""
    try:
        rels = ElementTree.fromstring(archive.read(_DOCX_RELS))
    except (KeyError, ElementTree.ParseError):
        return {}
    media: dict[str, str] = {}
    for rel in rels.findall(f"{{{_PKG_REL_NS}}}Relationship"):
        target = rel.get("Target") or ""
        rel_id = rel.get("Id") or ""
        if not rel_id or not target:
            continue
        # Targets are relative to word/ ("media/image1.png"), and an
        # external relationship (a linked, not embedded, picture) has no
        # file in the package at all.
        if (rel.get("TargetMode") or "").lower() == "external":
            continue
        name = f"word/{target.lstrip('/')}" if not target.startswith("word/") else target
        if name.startswith(_DOCX_MEDIA_PREFIX):
            media[rel_id] = name
    return media


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    """Visible text of one ``w:p``, runs joined in document order."""
    return "".join(node.text or "" for node in paragraph.iter(f"{{{_W_NS}}}t")).strip()


def _docx_paragraph_images(paragraph: ElementTree.Element) -> list[tuple[str, float, float]]:
    """``(relationship id, width_pt, height_pt)`` for each picture in a
    paragraph, in order. Size is 0 when the drawing carries no extent (a
    floating shape whose size lives elsewhere) — the caller treats an
    unknown size as "keep it" rather than guessing it away."""
    found: list[tuple[str, float, float]] = []
    for drawing in paragraph.iter(f"{{{_W_NS}}}drawing"):
        width = height = 0.0
        for extent in drawing.iter(f"{{{_WP_NS}}}extent"):
            try:
                width = int(extent.get("cx", 0)) / EMU_PER_PT
                height = int(extent.get("cy", 0)) / EMU_PER_PT
            except (TypeError, ValueError):
                width = height = 0.0
            break
        for blip in drawing.iter(f"{{{_A_NS}}}blip"):
            rel_id = blip.get(f"{{{_R_NS}}}embed")
            if rel_id:
                found.append((rel_id, width, height))
    return found


def _is_figure_sized(width_pt: float, height_pt: float) -> bool:
    """The same "is this a picture or a decoration" test the PDF path uses,
    applied to a Word drawing's declared size. Unknown size passes: a
    missing extent is not evidence of a logo."""
    if width_pt <= 0 or height_pt <= 0:
        return True
    if min(width_pt, height_pt) < MIN_FIGURE_EDGE_PT:
        return False
    longest, shortest = max(width_pt, height_pt), min(width_pt, height_pt)
    return longest / shortest <= MAX_FIGURE_ASPECT


def extract_docx_figures(docx_path: Path, assets_dir: Path) -> tuple[list[FigureEntry], str]:
    """Extract the pictures from a .docx into ``assets_dir``, with whatever
    captions the surrounding paragraphs give up. Returns the index and a
    note for the reader (empty when there is nothing to explain).

    Captions are more trustworthy here than in the PDF path: Word documents
    are a single flow, so "the paragraph after the picture" is genuinely
    the next thing a reader sees, not the neighbouring column. Only text
    that *starts* like a caption ("Figure 3.", "Table 1") counts, same as
    everywhere else in this module — an arbitrary following paragraph is
    not a label.

    Never raises, for the same reason ``extract_pdf_figures`` does not: a
    damaged file yields an empty index and a log line.
    """
    entries: list[FigureEntry] = []
    skipped_vector = 0
    seen_media: dict[str, int] = {}
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(docx_path) as archive:
            media_by_rel = _docx_media_by_rel_id(archive)
            if not media_by_rel:
                return [], ""
            body = ElementTree.fromstring(archive.read(_DOCX_BODY))
            paragraphs = list(body.iter(f"{{{_W_NS}}}p"))
            texts = [_docx_paragraph_text(p) for p in paragraphs]
            for position, paragraph in enumerate(paragraphs):
                for rel_id, width_pt, height_pt in _docx_paragraph_images(paragraph):
                    member = media_by_rel.get(rel_id)
                    if member is None:
                        continue
                    suffix = Path(member).suffix.lower()
                    if suffix not in _DOCX_IMAGE_SUFFIXES:
                        skipped_vector += 1
                        continue
                    if not _is_figure_sized(width_pt, height_pt):
                        continue
                    # The same picture used twice (a logo, a repeated
                    # diagram) is one figure, labelled where it first
                    # appears — the PDF path's seen_xrefs rule.
                    if member in seen_media:
                        continue

                    filename = f"fig-{len(entries) + 1:02d}{suffix}"
                    (assets_dir / filename).write_bytes(archive.read(member))
                    seen_media[member] = len(entries)

                    caption = _docx_caption_near(texts, position)
                    label = _label_from_caption(caption)
                    if label:
                        confidence = "high"
                    else:
                        label = f"image {len(entries) + 1}"
                        confidence = "none"
                    entries.append(
                        FigureEntry(
                            label=label,
                            caption=caption,
                            file=filename,
                            page=0,  # Word has no page numbers without layout
                            confidence=confidence,
                        )
                    )
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        logger.warning("could not extract figures from %s: %s", docx_path, exc)
        return entries, ""
    except Exception as exc:  # noqa: BLE001 - a figure index must never fail a turn
        logger.warning("could not extract figures from %s: %s", docx_path, exc)
        return entries, ""

    note = ""
    if skipped_vector:
        note = (
            f"{skipped_vector} embedded image(s) were skipped: they are vector or metafile "
            "formats (EMF/WMF/SVG — usually pasted charts or drawings) that cannot be shown "
            "as a picture. Ask the user for a raster export if one of them matters."
        )
    return entries, note


def _docx_caption_near(texts: list[str], position: int) -> str:
    """The caption for a picture in paragraph ``position``.

    Looked for in the picture's own paragraph first (Word's own "Insert
    Caption" often lands the text in the same paragraph as an inline
    image), then in the *first* non-empty paragraph after it — the figure
    convention — and finally the first non-empty one before it, which is
    where tables put theirs. Empty paragraphs are skipped, since a blank
    line between a picture and its caption is common formatting.

    Exactly one non-empty paragraph in each direction, deliberately.
    Looking two paragraphs out was tried and produced a confidently wrong
    label on a real document: an uncaptioned picture followed by a line of
    body text picked up the *next* figure's caption and reported it as
    high confidence. A missing caption costs a positional "image 2"; a
    stolen one makes "Figure 1 shows..." a lie.
    """
    own = texts[position] if 0 <= position < len(texts) else ""
    if _CAPTION_RE.match(own):
        return own.splitlines()[0].strip()

    def _first_non_empty(indices) -> str:
        for i in indices:
            text = texts[i]
            if not text:
                continue
            return text.splitlines()[0].strip() if _CAPTION_RE.match(text) else ""
        return ""

    return _first_non_empty(range(position + 1, len(texts))) or _first_non_empty(
        range(position - 1, -1, -1)
    )


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
        # page 0 means "this format has no page numbers" (a .docx is a
        # flow, not pages) — printing "page 0" would be a made-up fact.
        detail = f"  - {entry.label}" + (f" (page {entry.page})" if entry.page else "")
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
    "extract_docx_figures",
    "extract_pdf_figures",
    "read_index",
    "write_index",
]
