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
) -> str:
    """Render the full Markdown transcript text (no file I/O — callers that
    also need to copy artifact files use ``write_transcript`` instead)."""
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
                    rel = f"{sidecar_dirname}/{conversation_id[:8]}/{Path(art.path).name}"
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
    # rows are) — copy_to_target needs the real Artifact dataclass, so
    # reconstruct just enough of one from the record for the file copy.
    for art in artifacts:
        if art.kind == "ImageArtifact" and art.path and Path(art.path).exists():
            placeholder = ImageArtifact(data=b"", mime_type=art.mime_type or "", path=art.path)
            artifact_store.copy_to_target(placeholder, target_dir)

    text = render_transcript(
        conversation_id=conversation_id,
        title=title,
        workspace_name=workspace_name,
        profile_name=profile_name,
        messages=messages,
        artifacts=artifacts,
        sidecar_dirname=sidecar_dirname,
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
