"""Human-readable Markdown transcript export.

The first cut of AIDA's Obsidian-style output writer (PLAN.md §2 row 7,
matured with full folder conventions in Phase 6): one ``.md`` file per
conversation under the records dir (``~/Documents/Aida/`` by default), with
every image/file artifact the conversation produced copied into a
per-conversation sidecar folder alongside it and linked with a relative
Markdown link — open the ``.md`` file in Obsidian (or any Markdown viewer)
and the images just work, no absolute paths, no missing-file links after
the conversation record folder is moved or zipped up.
"""

from __future__ import annotations

import re
from pathlib import Path

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.documents.writers.md_obsidian import copy_images_to_sidecar
from aida.persistence.store import ArtifactRecord
from aida.providers.base import Message

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 60) -> str:
    """Filesystem/Obsidian-safe slug: lowercase, hyphens, no punctuation."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "untitled"


def sidecar_dir(records_dir: Path, sidecar_dirname: str, conversation_id: str) -> Path:
    """Where this conversation's copied artifact files live — one
    subfolder per conversation so deleting it (aida.persistence.cleanup)
    can never touch another conversation's images."""
    return records_dir / sidecar_dirname / conversation_id[:8]


def record_file_path(records_dir: Path, conversation_id: str, title: str | None) -> Path:
    slug = slugify(title) if title else conversation_id[:8]
    return records_dir / f"{slug}-{conversation_id[:8]}.md"


def _role_heading(role: str) -> str:
    return {"user": "User", "assistant": "Assistant", "tool": "Tool result", "system": "System"}.get(
        role, role.title()
    )


def render_transcript(
    *,
    conversation_id: str,
    title: str | None,
    workspace_name: str | None,
    profile_name: str | None,
    messages: list[Message],
    artifacts: list[ArtifactRecord],
    sidecar_dirname: str,
    sidecar_filenames: dict[str, str] | None = None,
) -> str:
    """Render the full Markdown transcript text (no file I/O — callers that
    also need to copy artifact files use ``write_transcript`` instead).

    ``sidecar_filenames`` maps artifact id -> the filename that artifact
    actually ended up with inside the sidecar folder. It matters because
    ``ArtifactStore.copy_to_target`` renames on a genuine collision (two
    different images sharing a basename), so the source path's name is not
    always the copy's name; ``write_transcript`` passes the real mapping.
    Omitting it falls back to the source basename, which is correct
    whenever no collision occurred.
    """
    artifacts_by_call: dict[str, list[ArtifactRecord]] = {}
    for art in artifacts:
        if art.call_id:
            artifacts_by_call.setdefault(art.call_id, []).append(art)

    lines = [f"# {title or f'Conversation {conversation_id[:8]}'}", ""]
    lines.append(f"- **workspace:** {workspace_name or '(none)'}")
    lines.append(f"- **profile:** {profile_name or '(none)'}")
    lines.append(f"- **conversation id:** `{conversation_id}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    for message in messages:
        if message.role == "system":
            continue  # the system prompt/skills text is config, not dialogue
        if message.role == "assistant" and not message.content and message.tool_calls:
            # A tool-calling turn with no user-visible text of its own —
            # skip the empty heading; the tool result section carries it.
            continue

        lines.append(f"## {_role_heading(message.role)}")
        lines.append("")
        if message.content:
            lines.append(message.content)
            lines.append("")

        if message.role == "tool" and message.tool_call_id:
            for art in artifacts_by_call.get(message.tool_call_id, []):
                if art.kind == "ImageArtifact" and art.path:
                    name = (sidecar_filenames or {}).get(art.id) or Path(art.path).name
                    rel = f"{sidecar_dirname}/{conversation_id[:8]}/{name}"
                    lines.append(f"![{art.id}]({rel})")
                    lines.append("")

    return "\n".join(lines)


def write_transcript(
    *,
    path: Path,
    records_dir: Path,
    artifact_store: ArtifactStore,
    conversation_id: str,
    title: str | None,
    workspace_name: str | None,
    profile_name: str | None,
    messages: list[Message],
    artifacts: list[ArtifactRecord],
    sidecar_dirname: str = "figures",
) -> Path:
    """Copy every image/file artifact into this conversation's sidecar
    folder, render the transcript referencing those copies, and write it to
    ``path``. Returns ``path``. Overwrites on every call (Phase 4: "exported
    on close/update") — safe to call repeatedly as a conversation grows.

    ``path`` is the caller's decision, not recomputed here, deliberately:
    the caller (``aida.persistence.recorder.ConversationRecorder``) picks
    the path once (from the conversation's title at the time of its first
    write) and reuses it on every subsequent export, even if the title
    later changes — recomputing a title-derived path on every call would
    silently orphan the previous file instead of updating it.
    """
    records_dir.mkdir(parents=True, exist_ok=True)
    target_dir = sidecar_dir(records_dir, sidecar_dirname, conversation_id)

    # In-memory Artifact objects aren't available here (only DB metadata
    # rows are) — reconstruct just enough of one from each record for the
    # file copy. Phase 6: this now goes through the same
    # copy_images_to_sidecar primitive aida.documents.writers.md_obsidian's
    # write_markdown_document uses for freeform reports — "one writer" for
    # the image-copying mechanics, even though a transcript's own text
    # rendering (below, render_transcript) stays its own thing.
    placeholders = [
        ImageArtifact(data=b"", id=art.id, mime_type=art.mime_type or "", path=art.path)
        for art in artifacts
        if art.kind == "ImageArtifact" and art.path and Path(art.path).exists()
    ]
    # Keyed by the *record's* artifact id (hence id=art.id above — a fresh
    # placeholder would otherwise get a brand-new random id and the link
    # lookup in render_transcript would never match), so the links below
    # point at the filename each image actually got in the sidecar folder.
    copied = copy_images_to_sidecar(placeholders, target_dir, artifact_store)
    sidecar_filenames = {artifact_id: path.name for artifact_id, path in copied.items()}

    text = render_transcript(
        conversation_id=conversation_id,
        title=title,
        workspace_name=workspace_name,
        profile_name=profile_name,
        messages=messages,
        artifacts=artifacts,
        sidecar_dirname=sidecar_dirname,
        sidecar_filenames=sidecar_filenames,
    )
    path.write_text(text, encoding="utf-8")
    return path


__all__ = [
    "record_file_path",
    "render_transcript",
    "sidecar_dir",
    "slugify",
    "write_transcript",
]
