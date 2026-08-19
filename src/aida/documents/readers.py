"""Structured document reading, dispatched by file extension (PLAN.md Phase
6): "Dispatcher by extension/MIME; structured extraction, not blind
flattening." Each format gets format-appropriate extraction into AIDA's
typed ``Artifact`` types (PLAN.md hard rule 3) rather than one giant
"read bytes, hope it's text" fallback:

- Plain text / code / Markdown -> ``TextArtifact``
- CSV -> ``TableArtifact`` (stdlib ``csv``)
- JSON -> ``JsonArtifact`` (structure preserved, not re-flattened to text)
- PDF (extra ``docs``, ``pymupdf``) -> ``TextArtifact``, one page per
  ``--- page N ---`` section, capped at ``max_pdf_pages``
- DOCX (extra ``docs``, ``python-docx``) -> ``TextArtifact`` of paragraph
  text (tables rendered inline as pipe rows)
- XLSX (extra ``docs``, ``openpyxl``) -> one ``TableArtifact`` per sheet,
  capped at ``max_sheets`` sheets / ``max_rows_per_sheet`` rows each
- PPTX (extra ``docs``, ``python-pptx``) -> ``TextArtifact``, one slide per
  ``--- slide N ---`` section
- Images -> ``ImageArtifact`` referencing the file's own path (no bytes
  loaded into memory — the GUI's ``InlineImageWidget`` reads straight off
  disk via the path, same as a live ``ImageArtifactCreated`` event)

**Known v1 limitation, called out explicitly rather than silently**: an
``ImageArtifact`` read this way is *not* sent to the model as vision input.
``aida.providers.base.Message.content`` is plain ``str`` throughout this
codebase's provider layer — there is no multipart/vision message shape
anywhere yet, so "images... passed to vision-capable models" (PLAN.md) is
only partially true here: the image displays in the GUI and the model is
told it exists (via ``describe_for_model``), but its pixels never reach the
model's context. Real vision support needs a provider-layer change (a
multipart ``Message.content``) that's out of scope for this phase.

HDF5 is deliberately **not** implemented here — that's pyIrena MCP's job
(Phase 3 already covers science-data formats via MCP), not a general
document reader.

Every reader applies a size/token guard (PLAN.md: "long docs summarized-by-
section or chunk-selected rather than context-bombed") — callers that want
the *whole* document regardless of size should read the file directly
rather than going through this dispatcher.
"""

from __future__ import annotations

import csv
import json
import mimetypes
from collections.abc import Callable
from pathlib import Path

from aida.artifacts.base import Artifact, ImageArtifact, JsonArtifact, TableArtifact, TextArtifact

DEFAULT_MAX_CHARS = 20_000
DEFAULT_MAX_PDF_PAGES = 50
DEFAULT_MAX_SHEETS = 5
DEFAULT_MAX_ROWS_PER_SHEET = 200
DEFAULT_MAX_CSV_ROWS = 500

_TRUNCATION_NOTE = "\n... [truncated]"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".rs", ".go", ".rb", ".sh", ".bash", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".xml", ".html", ".htm", ".css", ".sql",
}


class UnsupportedDocumentFormatError(Exception):
    """Raised for a file extension no reader (and no plain-text fallback)
    recognizes — surfaces as a clear tool error rather than either crashing
    or silently mis-decoding binary data as text."""


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE


def _read_text_file(path: Path, *, max_chars: int) -> list[Artifact]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [TextArtifact(text=_truncate(text, max_chars))]


def _read_csv_file(path: Path, *, max_rows: int = DEFAULT_MAX_CSV_ROWS, **_ignored) -> list[Artifact]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return [TableArtifact(columns=[], rows=[])]
    columns, data_rows = rows[0], rows[1:]
    truncated = data_rows[:max_rows]
    if len(data_rows) > max_rows:
        truncated.append([f"... [{len(data_rows) - max_rows} more rows truncated]"] + [""] * (len(columns) - 1))
    return [TableArtifact(columns=columns, rows=truncated)]


def _read_json_file(path: Path, *, max_chars: int, **_ignored) -> list[Artifact]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        # Not actually valid JSON despite the extension -- fall back to text
        # rather than raising, since the content still exists and is useful.
        return [TextArtifact(text=_truncate(text, max_chars))]
    return [JsonArtifact(data=data)]


def _read_image_file(path: Path, **_ignored) -> list[Artifact]:
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return [ImageArtifact(data=b"", mime_type=mime_type, path=str(path), filename=path.name)]


def _read_pdf_file(path: Path, *, max_chars: int, max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES, **_ignored) -> list[Artifact]:
    import pymupdf

    parts: list[str] = []
    with pymupdf.open(path) as doc:
        page_count = doc.page_count
        for index, page in enumerate(doc):
            if index >= max_pdf_pages:
                parts.append(f"--- [{page_count - max_pdf_pages} more pages truncated] ---")
                break
            parts.append(f"--- page {index + 1} ---\n{page.get_text()}")
    return [TextArtifact(text=_truncate("\n\n".join(parts), max_chars))]


def _read_docx_file(path: Path, *, max_chars: int, **_ignored) -> list[Artifact]:
    import docx

    document = docx.Document(str(path))
    parts: list[str] = [p.text for p in document.paragraphs]
    for table in document.tables:
        parts.append("")
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return [TextArtifact(text=_truncate("\n".join(parts), max_chars))]


def _read_xlsx_file(
    path: Path,
    *,
    max_sheets: int = DEFAULT_MAX_SHEETS,
    max_rows_per_sheet: int = DEFAULT_MAX_ROWS_PER_SHEET,
    **_ignored,
) -> list[Artifact]:
    import openpyxl

    workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        artifacts: list[Artifact] = []
        for sheet_name in workbook.sheetnames[:max_sheets]:
            sheet = workbook[sheet_name]
            all_rows = list(sheet.iter_rows(values_only=True))
            if not all_rows:
                artifacts.append(TableArtifact(columns=[sheet_name], rows=[]))
                continue
            header = [str(c) if c is not None else "" for c in all_rows[0]]
            data_rows = [[("" if c is None else c) for c in row] for row in all_rows[1 : max_rows_per_sheet + 1]]
            if len(all_rows) - 1 > max_rows_per_sheet:
                data_rows.append(
                    [f"... [{len(all_rows) - 1 - max_rows_per_sheet} more rows truncated]"] + [""] * (len(header) - 1)
                )
            artifacts.append(TableArtifact(columns=[f"{sheet_name}: {c}" if header else sheet_name for c in header] or [sheet_name], rows=data_rows))
        if len(workbook.sheetnames) > max_sheets:
            artifacts.append(
                TextArtifact(text=f"... [{len(workbook.sheetnames) - max_sheets} more sheet(s) truncated]")
            )
        return artifacts
    finally:
        workbook.close()


def _read_pptx_file(path: Path, *, max_chars: int, **_ignored) -> list[Artifact]:
    from pptx import Presentation

    presentation = Presentation(str(path))
    parts: list[str] = []
    for index, slide in enumerate(presentation.slides):
        texts = [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame and shape.text_frame.text]
        parts.append(f"--- slide {index + 1} ---\n" + "\n".join(texts))
    return [TextArtifact(text=_truncate("\n\n".join(parts), max_chars))]


_READERS: dict[str, Callable[..., list[Artifact]]] = {
    ".csv": _read_csv_file,
    ".json": _read_json_file,
    ".pdf": _read_pdf_file,
    ".docx": _read_docx_file,
    ".xlsx": _read_xlsx_file,
    ".pptx": _read_pptx_file,
}


def is_supported(path: str | Path) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in _READERS or suffix in _TEXT_SUFFIXES or suffix in _IMAGE_SUFFIXES or suffix == ""


def read_document(
    path: str | Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_sheets: int = DEFAULT_MAX_SHEETS,
    max_rows_per_sheet: int = DEFAULT_MAX_ROWS_PER_SHEET,
) -> list[Artifact]:
    """Reads ``path`` into one or more typed ``Artifact``s, dispatched by
    file extension. Returns a *list* deliberately (not one ``Artifact``):
    a multi-sheet spreadsheet reads as one ``TableArtifact`` per sheet.

    Raises ``UnsupportedDocumentFormatError`` for an extension with no
    reader and no plain-text fallback (binary formats like ``.zip``,
    ``.exe``, unrecognized proprietary formats, ...) rather than guessing.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in _IMAGE_SUFFIXES:
        return _read_image_file(p)
    if suffix in _READERS:
        return _READERS[suffix](
            p, max_chars=max_chars, max_pdf_pages=max_pdf_pages, max_sheets=max_sheets, max_rows_per_sheet=max_rows_per_sheet
        )
    if suffix in _TEXT_SUFFIXES or suffix == "":
        # No extension (README, Makefile, ...) is treated as plain text
        # too, same as any shell would.
        return _read_text_file(p, max_chars=max_chars)

    raise UnsupportedDocumentFormatError(
        f"No reader for {suffix!r} files ({p.name}). Supported: text/code/Markdown, "
        ".csv, .json, .pdf, .docx, .xlsx, .pptx, and common image formats."
    )


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_CSV_ROWS",
    "DEFAULT_MAX_PDF_PAGES",
    "DEFAULT_MAX_ROWS_PER_SHEET",
    "DEFAULT_MAX_SHEETS",
    "UnsupportedDocumentFormatError",
    "is_supported",
    "read_document",
]
