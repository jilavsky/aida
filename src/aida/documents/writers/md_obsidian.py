"""Default document writer (PLAN.md Phase 6): "MD file in target folder;
images written to user-nameable sidecar folder... links relative; safe
filename collision handling." One ``.md`` file per document, images copied
into a sidecar folder next to it and linked with relative Markdown links —
a records/target folder stays fully portable (move it, zip it, open it in
Obsidian on another machine — links never break because nothing is
absolute).

``copy_images_to_sidecar`` is the one shared low-level mechanic between this
writer's ``write_markdown_document`` (a freeform "title + body + images"
document — what the ``write_markdown_report`` agent tool in
``aida.workspace.files`` uses) and ``aida.persistence.records``'s
conversation-transcript writer, which is why the task list calls the
transcript exporter "refactored onto ``md_obsidian.py`` (one writer)":
both funnel their image-copying through ``ArtifactStore.copy_to_target``
via this one function rather than each having its own copy of that logic,
even though the two callers' text rendering is different in shape (a
transcript is role-structured dialogue; a report is freeform prose the
agent wrote).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.workspace.safety import unique_destination


@dataclass
class ImageToEmbed:
    """One image to copy into the sidecar folder and link into the
    document, with the alt text to give it."""

    artifact: ImageArtifact
    alt_text: str = ""


def copy_images_to_sidecar(
    images: list[ImageArtifact], sidecar_dir: Path, artifact_store: ArtifactStore
) -> dict[str, Path]:
    """Copies each already-saved (``artifact.path`` set) image into
    ``sidecar_dir`` via ``ArtifactStore.copy_to_target``, creating it if
    needed. Returns ``{artifact_id: copied_path}``."""
    if not images:
        return {}
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    return {image.id: artifact_store.copy_to_target(image, sidecar_dir) for image in images}


def _is_relative(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def markdown_image_link(image_path: Path, *, relative_to: Path, alt_text: str = "") -> str:
    """A Markdown image link, relative to ``relative_to`` when the image is
    actually underneath it (the normal case — a sidecar folder next to the
    document) and absolute otherwise (defensive fallback, not expected in
    practice given this module always copies into a sidecar under the same
    target folder first)."""
    rel = image_path.relative_to(relative_to) if _is_relative(image_path, relative_to) else image_path
    return f"![{alt_text}]({rel.as_posix()})"


def write_markdown_document(
    *,
    target_dir: Path,
    filename_stem: str,
    title: str,
    body: str,
    artifact_store: ArtifactStore,
    images: list[ImageToEmbed] | None = None,
    sidecar_dirname: str = "figures",
) -> Path:
    """Writes ``# title`` + ``body`` (+ any ``images``, copied into
    ``target_dir/sidecar_dirname`` and linked with relative paths) to
    ``target_dir/filename_stem.md`` — collision-safe if that name is
    already taken (PLAN.md: "safe filename collision handling"). Returns
    the final path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    sidecar_dir = target_dir / sidecar_dirname
    images = images or []

    lines = [f"# {title}", ""]
    if body:
        lines.append(body)
        lines.append("")

    if images:
        copied = copy_images_to_sidecar([img.artifact for img in images], sidecar_dir, artifact_store)
        for img in images:
            copied_path = copied[img.artifact.id]
            lines.append(markdown_image_link(copied_path, relative_to=target_dir, alt_text=img.alt_text or img.artifact.id))
            lines.append("")

    destination = unique_destination(target_dir / f"{filename_stem}.md")
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


__all__ = ["ImageToEmbed", "copy_images_to_sidecar", "markdown_image_link", "write_markdown_document"]
