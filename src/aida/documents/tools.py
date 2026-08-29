"""Agent-facing document-writing tools (PLAN.md Phase 6): "Agent-facing
tools: ``write_markdown_report(title, body, images=[artifact refs])`` and
plain ``write_file``" (the latter already lives in ``aida.workspace.files``
— this module is specifically the *formatted-document* writers). Both tools
here go through ``SafetyGuard.authorize_write`` on the target path first,
same as every mutating tool in ``aida.workspace.files``.

Resolving ``image_artifact_ids`` -> real files: rather than inventing a new
shared "artifact registry" object threaded through every tool, this reuses
``ArtifactStore.list_metadata()`` — the same ``ArtifactStore`` instance
``start_session`` already hands to ``aida.mcp.manager`` (which saves every
image an MCP tool call produces there) is handed here too, so a
``write_markdown_report`` call later in the same turn/conversation can look
up an id the model quotes back (from an earlier ``ImageArtifactCreated``
event) against metadata that's already been recorded. An id that isn't
found (stale, mistyped, or from a FileArtifact rather than an image) is
skipped rather than failing the whole report — a partial report beats a
lost one over one bad reference.
"""

from __future__ import annotations

import re
from typing import Any

from aida.artifacts.base import FileArtifact, ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.core.tools import NativeTool, ToolResult, wrap_tool_errors
from aida.documents.writers.docx_writer import DocxSection, write_docx_document
from aida.documents.writers.md_obsidian import ImageToEmbed, write_markdown_document
from aida.providers.base import ToolSchema
from aida.workspace.safety import ConfirmationDenied, SafetyGuard

_tool = wrap_tool_errors(ConfirmationDenied, OSError, ValueError)

#: Mirrors ``md_obsidian._IMAGE_PLACEHOLDER_RE`` (PLAN.md §1.5: let the model
#: place an image within the body instead of always after it) — the DOCX
#: writer has no single "body string" to substitute into (``DocxSection``s
#: are already an ordered list), so here the placeholder splits ``body``
#: into the paragraph/image sections directly, in the order the model wrote
#: them.
_IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{image:([^{}]+)\}\}")


def _docx_sections_for_body(body: str, images: list[ImageArtifact]) -> list[DocxSection]:
    """Splits ``body`` on ``{{image:ARTIFACT_ID}}`` placeholders into
    ordered paragraph/image ``DocxSection``s, matching each placeholder to
    one of ``images`` by id. A placeholder referencing an id not in
    ``images`` is left as literal text — visible rather than silently
    dropped. Any image in ``images`` no placeholder referenced is appended
    at the end, in list order, exactly as when no placeholders are used."""
    by_id = {image.id: image for image in images}
    referenced: set[str] = set()
    sections: list[DocxSection] = []

    if body:
        parts = _IMAGE_PLACEHOLDER_RE.split(body)
        for index, part in enumerate(parts):
            if index % 2 == 0:
                if part.strip():
                    sections.append(DocxSection(kind="paragraph", text=part))
                continue
            artifact_id = part.strip()
            image = by_id.get(artifact_id)
            if image is None:
                sections.append(DocxSection(kind="paragraph", text="{{image:" + artifact_id + "}}"))
                continue
            sections.append(DocxSection(kind="image", image=image))
            referenced.add(artifact_id)

    for image in images:
        if image.id not in referenced:
            sections.append(DocxSection(kind="image", image=image))
    return sections


def _resolve_images(artifact_store: ArtifactStore, image_artifact_ids: list[str]) -> list[ImageArtifact]:
    known = {m.id: m for m in artifact_store.list_metadata() if m.kind == "ImageArtifact" and m.path}
    resolved = []
    for artifact_id in image_artifact_ids:
        meta = known.get(artifact_id)
        if meta is None:
            continue
        resolved.append(ImageArtifact(data=b"", id=meta.id, path=meta.path, mime_type=meta.mime_type or "image/png"))
    return resolved


def default_document_tools(
    guard: SafetyGuard, artifact_store: ArtifactStore, *, sidecar_dirname: str = "figures"
) -> dict[str, NativeTool]:
    """Builds ``write_markdown_report`` (the default writer) and
    ``write_docx_report`` (for Office needs), each closing over ``guard``,
    ``artifact_store``, and ``sidecar_dirname`` — same closure-at-build-time
    pattern as ``aida.workspace.files.default_file_tools``."""

    @_tool
    async def write_markdown_report(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        title = arguments["title"]
        body = arguments.get("body", "")
        image_ids = arguments.get("image_artifact_ids") or []

        candidate = await guard.authorize_write(path)
        images = [ImageToEmbed(artifact=img) for img in _resolve_images(artifact_store, image_ids)]

        final_path = write_markdown_document(
            target_dir=candidate.parent,
            filename_stem=candidate.stem,
            title=title,
            body=body,
            artifact_store=artifact_store,
            images=images,
            sidecar_dirname=sidecar_dirname,
        )
        artifact = FileArtifact(path=str(final_path), mime_type="text/markdown")
        return ToolResult(content=f"Wrote Markdown report to {final_path}", artifacts=[artifact])

    @_tool
    async def write_docx_report(arguments: dict[str, Any]) -> ToolResult:
        path = arguments["path"]
        title = arguments["title"]
        body = arguments.get("body", "")
        image_ids = arguments.get("image_artifact_ids") or []

        candidate = await guard.authorize_write(path)
        sections = _docx_sections_for_body(body, _resolve_images(artifact_store, image_ids))

        final_path = write_docx_document(
            target_dir=candidate.parent, filename_stem=candidate.stem, title=title, sections=sections
        )
        artifact = FileArtifact(
            path=str(final_path), mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        return ToolResult(content=f"Wrote DOCX report to {final_path}", artifacts=[artifact])

    tools = [
        NativeTool(
            schema=ToolSchema(
                name="write_markdown_report",
                description=(
                    "Write a Markdown report (the default output format) with a title, body text, and "
                    "optionally embedded images. Images are referenced by the artifact id from an earlier "
                    "tool result's image (e.g. an ImageArtifactCreated event) — pass their ids in "
                    "image_artifact_ids and they'll be copied into a sidecar folder next to the report and "
                    "linked in. By default every image is appended after the body, in the order given; to "
                    "place one at a specific point instead, put a {{image:ARTIFACT_ID}} placeholder in body "
                    "where it should appear — any image_artifact_ids not referenced by a placeholder still "
                    "appear after the body, so placeholders are optional and only change images you use them "
                    "for."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Destination .md file path."},
                        "title": {"type": "string"},
                        "body": {"type": "string", "description": "Report body text (Markdown)."},
                        "image_artifact_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Artifact ids of images (from earlier tool results) to embed.",
                        },
                    },
                    "required": ["path", "title"],
                },
            ),
            func=write_markdown_report,
        ),
        NativeTool(
            schema=ToolSchema(
                name="write_docx_report",
                description=(
                    "Write a basic Word (.docx) report with a title, body paragraph, and optionally embedded "
                    "images — for Office-centric workflows. See write_markdown_report for the default output "
                    "format. Images are appended after the body by default; to place one at a specific point "
                    "in the body instead, put a {{image:ARTIFACT_ID}} placeholder where it should appear — "
                    "any image_artifact_ids not referenced by a placeholder still appear after the body."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Destination .docx file path."},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "image_artifact_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["path", "title"],
                },
            ),
            func=write_docx_report,
        ),
    ]
    return {tool.schema.name: tool for tool in tools}


__all__ = ["default_document_tools"]
