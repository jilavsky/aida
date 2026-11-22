"""Tools for reaching a document's figures — the pull half of the design.

Nothing pushes images at the model. ``list_document_figures`` returns an
*index* (labels, captions, pages, and how sure those labels are), and
``get_document_figure`` returns one image by label. So a paper with twelve
figures costs a couple of hundred tokens to describe, and the agent spends
its four-image vision budget
(``aida.providers.vision.MAX_ATTACHED_IMAGES``) on the two it actually
needs — instead of that budget silently truncating a push of twelve
anonymous pictures.

Both tools read only inside the conversation's own attachments folder.
That is a plain containment check rather than a ``SafetyGuard`` call,
deliberately: these are files AIDA itself copied there, not paths the model
chose, so the workspace's allowed-roots question does not arise. The check
exists so a crafted ``document`` argument cannot walk out of that folder.

Figures are only available for documents a person **attached**. One the
agent opened with ``read_file`` was never copied (see
``aida.documents.attachments``), and the error says so plainly rather than
reporting an empty index, which would read as "this paper has no figures".
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aida.artifacts.base import ImageArtifact
from aida.config.logging_setup import get_logger
from aida.core.tools import NativeTool, ToolResult, ToolSchema, wrap_tool_errors
from aida.documents.attachments import assets_dir_for
from aida.documents.figures import (
    FigureIndex,
    describe_index,
    extract_docx_figures,
    extract_pdf_figures,
    read_index,
    write_index,
)
from aida.documents.ocr.common import apply_page_captions
from aida.documents.ocr.vlm import ConvertPages

logger = get_logger(__name__)

#: Same named-failure boundary the other document tools use: an expected
#: problem becomes an error ToolResult the model can read and act on,
#: rather than an exception the agent loop has to generalise about.
_tool = wrap_tool_errors(OSError, ValueError, asyncio.TimeoutError)

#: Extraction is CPU-bound and reads a whole PDF; a very large one should
#: not hold a turn open indefinitely.
EXTRACT_TIMEOUT_SECONDS = 120.0

#: Formats whose figures can be pulled out. A .docx is here because it is
#: a zip with the pictures already whole inside it — see
#: ``aida.documents.figures.extract_docx_figures``, which needs no
#: dependency beyond the standard library. Bug report: "I attached docx
#: file and agent stated ... the extraction tool couldn't get them from
#: this docx"; before this, every non-PDF returned an empty index, which
#: reads as "this document has no figures" rather than "nobody looked".
_EXTRACTABLE_SUFFIXES = {".pdf", ".docx"}


def _resolve_document(attachments_dir: Path, name: str) -> Path | None:
    """The attached file called ``name``, or ``None``.

    Resolved by comparing the *resolved* path against the resolved
    attachments folder, so neither ``../`` nor a symlink in the argument
    can reach a file outside it.
    """
    if not name or not attachments_dir.is_dir():
        return None
    # A bare filename only. Taking the basename of anything else would be
    # safe (it cannot escape) but sloppy: it would silently reinterpret
    # "../paper.pdf" as the attached "paper.pdf" and answer a question that
    # was not asked. Refusing is clearer, and the caller's message says
    # what is actually attached.
    if name != Path(name).name or name in (".", ".."):
        return None
    root = attachments_dir.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    return candidate


def _attached_document_names(attachments_dir: Path) -> list[str]:
    if not attachments_dir.is_dir():
        return []
    present = {p.name for p in attachments_dir.iterdir() if p.is_file()}
    return sorted(n for n in present if not (n.endswith(".md") and n[: -len(".md")] in present))


def _not_found_message(attachments_dir: Path, name: str) -> str:
    available = _attached_document_names(attachments_dir)
    if not available:
        return (
            f"No document called {name!r} is attached to this conversation. Figures are only "
            "available for documents the user attached here — a file read from disk with "
            "read_file is not copied into the conversation, so it has no extracted figures."
        )
    return f"No attached document called {name!r}. Attached: {', '.join(available)}."


async def _recaption_with_vision(
    document: Path, entries: list, convert_pages: ConvertPages
) -> tuple[list, str, bool]:
    """Re-caption the figures on pages where the built-in pairing is a guess.

    Only pages holding a figure whose confidence is not "high" are
    converted (a multi-column page, or no caption found), so a paper costs
    a few page conversions, not one per page. Returns the entries, a note
    for the index, and whether any page was re-captioned.
    """
    pages = sorted({entry.page for entry in entries if entry.confidence != "high"})
    if not pages:
        return entries, "", False
    try:
        result = await convert_pages(document, [page - 1 for page in pages])
    except Exception as exc:  # noqa: BLE001 - never fail a turn over an optional step
        logger.warning("vision re-captioning failed for %s: %s", document.name, exc)
        return entries, "", False
    if not result.pages:
        note = (
            "Figure labels on multi-column pages come from the built-in extractor and may be "
            f"less reliable. {result.unavailable}"
        )
        return entries, note.strip(), False
    markdown = {index + 1: text for index, text in result.pages.items()}
    entries, used = apply_page_captions(entries, markdown)
    if not used:
        return entries, "", False
    note = (
        f"Captions on page(s) {', '.join(map(str, used))} were matched in reading order by "
        f"{result.route_label}."
    )
    return entries, note, True


async def _figures_for(document: Path, convert_pages: ConvertPages | None = None) -> FigureIndex:
    """The document's figure index, extracting it on first use.

    Lazy on purpose: a paper nobody asks a figure question about costs
    nothing, and extraction never runs on the turn the document arrives on.
    The cached index is written even when empty, so a fruitless extraction
    is not repeated on every ask, and pages converted for it are cached by
    the converter.
    """
    assets = assets_dir_for(document)
    cached = read_index(assets)
    if cached is not None:
        return cached
    suffix = document.suffix.lower()
    if suffix not in _EXTRACTABLE_SUFFIXES:
        return FigureIndex(source=document.name, figures=[])

    backend, note = "builtin", ""
    if suffix == ".docx":
        # A .docx is a single text flow, so the built-in pairing of picture
        # to caption is the good case, not a fallback.
        entries, note = await asyncio.wait_for(
            asyncio.to_thread(extract_docx_figures, document, assets),
            timeout=EXTRACT_TIMEOUT_SECONDS,
        )
    else:
        entries = await asyncio.wait_for(
            asyncio.to_thread(extract_pdf_figures, document, assets),
            timeout=EXTRACT_TIMEOUT_SECONDS,
        )
        if convert_pages is not None and entries:
            entries, note, recaptioned = await _recaption_with_vision(
                document, entries, convert_pages
            )
            if recaptioned:
                backend = "vision"
    await asyncio.to_thread(write_index, assets, document.name, entries, backend=backend, note=note)
    return FigureIndex(source=document.name, figures=entries, backend=backend, note=note)


def default_figure_tools(
    attachments_dir_provider: Callable[[], Path], *, convert_pages: ConvertPages | None = None
) -> dict[str, NativeTool]:
    """Build the two figure tools around a *callable* rather than a path.

    The conversation's attachments folder is not known when the rest of the
    tool set is built — it depends on the conversation id, which the
    recorder assigns later — and it must be re-read rather than captured,
    since resuming or switching conversation changes it.
    """

    @_tool
    async def list_document_figures(arguments: dict[str, Any]) -> ToolResult:
        attachments_dir = attachments_dir_provider()
        name = arguments.get("document") or ""
        document = _resolve_document(attachments_dir, name)
        if document is None:
            return ToolResult(content=_not_found_message(attachments_dir, name), is_error=True)
        index = await _figures_for(document, convert_pages)
        return ToolResult(content=describe_index(document.name, index))

    @_tool
    async def get_document_figure(arguments: dict[str, Any]) -> ToolResult:
        attachments_dir = attachments_dir_provider()
        name = arguments.get("document") or ""
        label = (arguments.get("label") or "").strip()
        document = _resolve_document(attachments_dir, name)
        if document is None:
            return ToolResult(content=_not_found_message(attachments_dir, name), is_error=True)
        entries = (await _figures_for(document, convert_pages)).figures
        if not entries:
            return ToolResult(
                content=f"No figures could be extracted from {document.name}.", is_error=True
            )

        wanted = label.casefold()
        match = next((e for e in entries if e.label.casefold() == wanted), None)
        if match is None:
            # Substring as a fallback: a model asking for "Figure 2" when
            # the index says "Figure 2b" should get the figure, not a
            # lecture. Ambiguity is reported rather than guessed at.
            partial = [e for e in entries if wanted and wanted in e.label.casefold()]
            if len(partial) == 1:
                match = partial[0]
            elif len(partial) > 1:
                return ToolResult(
                    content=f"{label!r} matches several: {', '.join(e.label for e in partial)}.",
                    is_error=True,
                )
        if match is None:
            return ToolResult(
                content=f"No figure labelled {label!r} in {document.name}. "
                f"Available: {', '.join(e.label for e in entries)}.",
                is_error=True,
            )

        image_path = assets_dir_for(document) / match.file
        if not image_path.is_file():
            return ToolResult(
                content=f"{match.label} is in the index but its image file is missing.",
                is_error=True,
            )
        caption = f" — {match.caption}" if match.caption else ""
        uncertain = " (label uncertain: multi-column layout)" if match.confidence == "low" else ""
        return ToolResult(
            content=f"{match.label} from {document.name}, page {match.page}{caption}{uncertain}",
            artifacts=[
                ImageArtifact(
                    data=b"",
                    path=str(image_path),
                    mime_type=mimetypes.guess_type(str(image_path))[0] or "image/png",
                    filename=f"{document.stem}-{match.file}",
                )
            ],
        )

    tools = [
        NativeTool(
            schema=ToolSchema(
                name="list_document_figures",
                description=(
                    "List the figures in a document the user attached to this conversation, with "
                    "their captions, page numbers and how reliable each label is. Returns text "
                    "only — no images — so it is cheap to call whenever a document's text "
                    "mentions a figure you cannot see. Use get_document_figure to actually view "
                    "one. Only works for documents the user attached; a file you opened yourself "
                    "with read_file has no extracted figures."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "document": {
                            "type": "string",
                            "description": "Filename of the attached document, e.g. 'paper.pdf'.",
                        }
                    },
                    "required": ["document"],
                },
            ),
            func=list_document_figures,
        ),
        NativeTool(
            schema=ToolSchema(
                name="get_document_figure",
                description=(
                    "View one figure from an attached document, by the label list_document_figures "
                    "reported (e.g. 'Figure 1'). Returns the image itself. Request only the figures "
                    "you actually need — a few images per turn reach the model, so pulling every "
                    "figure in a paper crowds out the ones that matter."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "document": {
                            "type": "string",
                            "description": "Filename of the attached document.",
                        },
                        "label": {
                            "type": "string",
                            "description": "Figure label from list_document_figures, e.g. 'Figure 2'.",
                        },
                    },
                    "required": ["document", "label"],
                },
            ),
            func=get_document_figure,
        ),
    ]
    return {tool.schema.name: tool for tool in tools}


__all__ = ["default_figure_tools"]
