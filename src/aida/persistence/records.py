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

from aida.artifacts.base import FileArtifact, ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.documents.writers.md_obsidian import copy_artifacts_to_sidecar
from aida.persistence.store import ArtifactRecord
from aida.providers.base import Message

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: The values ``AppConfig.transcript_tool_results`` may take. An
#: unrecognized value (a hand-edited config.yaml, or an older AIDA's
#: config read by mistake) falls back to "full" here rather than raising or
#: silently blanking a tool result — same "never let a bad on-disk value
#: erase content" rule ``aida.ui.qt.tool_call_group``'s display modes
#: follow for the live view.
TOOL_RESULT_MODES = ("off", "summary", "full")
DEFAULT_TOOL_RESULT_MODE = "full"

#: How much of a tool result's first line survives in "summary" mode.
_SUMMARY_MAX_CHARS = 200


def _tool_result_text(content: str, mode: str) -> str | None:
    """The text to show for a tool-result message's ``content`` under
    ``mode`` — ``None`` means show nothing at all (distinct from an empty
    string, which would still print a blank line)."""
    if mode not in TOOL_RESULT_MODES:
        mode = DEFAULT_TOOL_RESULT_MODE
    if mode == "off" or not content:
        return None
    if mode == "full":
        return content
    first_line = next((line for line in content.splitlines() if line.strip()), "").strip()
    if not first_line:
        return None
    if len(first_line) > _SUMMARY_MAX_CHARS:
        first_line = first_line[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return first_line


def slugify(text: str, max_len: int = 60) -> str:
    """Filesystem/Obsidian-safe slug: lowercase, hyphens, no punctuation."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "untitled"


def sidecar_dir(records_dir: Path, sidecar_dirname: str, conversation_id: str) -> Path:
    """Where this conversation's copied artifact files live — one
    subfolder per conversation so deleting it (aida.persistence.cleanup)
    can never touch another conversation's images."""
    return records_dir / sidecar_dirname / conversation_id[:8]


#: Folder name under the records dir holding the *originals* the user
#: attached to a conversation, plus what AIDA derived from them. A peer of
#: the sidecar folder rather than a child of it: a sidecar holds images the
#: conversation *produced*, this holds what was fed *into* it.
ATTACHMENTS_DIRNAME = "attachments"


def attachments_dir(records_dir: Path, conversation_id: str) -> Path:
    """Where one conversation's attached documents and their derived files
    live — one subfolder per conversation, mirroring ``sidecar_dir``, so
    deleting it can never touch another conversation's.

    Callers must not recompute this at delete time: the effective
    ``records_dir`` can change (Settings has a Records folder field), which
    would leave the real folder orphaned and undeletable. The resolved path
    is recorded on the conversation row — see
    ``ConversationStore.set_attachments_path`` — and deletion reads it back.
    """
    return records_dir / ATTACHMENTS_DIRNAME / conversation_id[:8]


def record_file_path(records_dir: Path, conversation_id: str, title: str | None) -> Path:
    slug = slugify(title) if title else conversation_id[:8]
    return records_dir / f"{slug}-{conversation_id[:8]}.md"


def _role_heading(role: str) -> str:
    return {
        "user": "User",
        "assistant": "Assistant",
        "tool": "Tool result",
        "system": "System",
    }.get(role, role.title())


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
    attachment_names: list[str] | None = None,
    tool_result_mode: str = DEFAULT_TOOL_RESULT_MODE,
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

    ``tool_result_mode`` (see ``TOOL_RESULT_MODES``) controls how much of a
    tool-result message's own text survives — "off" and "summary" exist so a
    long instrument-control session reads as a clean record instead of a dump
    of every raw tool payload the agent needed but nobody re-reads. It never
    hides the *images/files* a tool produced — only their message's own text.
    """
    artifacts_by_call: dict[str, list[ArtifactRecord]] = {}
    for art in artifacts:
        if art.call_id:
            artifacts_by_call.setdefault(art.call_id, []).append(art)

    lines = [f"# {title or f'Conversation {conversation_id[:8]}'}", ""]
    lines.append(f"- **workspace:** {workspace_name or '(none)'}")
    lines.append(f"- **profile:** {profile_name or '(none)'}")
    lines.append(f"- **conversation id:** `{conversation_id}`")
    # Linked relatively, like the sidecar images, so the transcript still
    # resolves after the records folder is moved or zipped up. Listed at
    # all because a transcript that discusses a paper without pointing at
    # it is an incomplete record of the conversation.
    for name in attachment_names or []:
        rel = f"{ATTACHMENTS_DIRNAME}/{conversation_id[:8]}/{name}"
        lines.append(f"- **attachment:** [{name}]({rel})")
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

        body_lines: list[str] = []
        text = message.content
        if message.role == "tool":
            text = _tool_result_text(message.content, tool_result_mode)
        if text:
            body_lines.append(text)
            body_lines.append("")

        artifact_lines: list[str] = []
        if message.role == "tool" and message.tool_call_id:
            for art in artifacts_by_call.get(message.tool_call_id, []):
                if not art.path:
                    continue
                name = (sidecar_filenames or {}).get(art.id) or Path(art.path).name
                rel = f"{sidecar_dirname}/{conversation_id[:8]}/{name}"
                if art.kind == "ImageArtifact":
                    artifact_lines.append(f"![{art.id}]({rel})")
                    artifact_lines.append("")
                elif art.kind == "FileArtifact":
                    artifact_lines.append(f"[{name}]({rel})")
                    artifact_lines.append("")

        if message.role == "tool" and not body_lines and not artifact_lines:
            # Nothing survived tool_result_mode and the tool produced no
            # linkable artifact either — skip the heading entirely rather
            # than leaving an empty "## Tool result" with nothing under it.
            continue

        lines.append(f"## {_role_heading(message.role)}")
        lines.append("")
        lines.extend(body_lines)
        lines.extend(artifact_lines)

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
    attachments_path: Path | None = None,
    tool_result_mode: str = DEFAULT_TOOL_RESULT_MODE,
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

    ``tool_result_mode`` is forwarded to ``render_transcript`` verbatim —
    see its docstring.
    """
    records_dir.mkdir(parents=True, exist_ok=True)
    target_dir = sidecar_dir(records_dir, sidecar_dirname, conversation_id)

    # In-memory Artifact objects aren't available here (only DB metadata
    # rows are) — reconstruct just enough of one from each record for the
    # file copy. Phase 6: this now goes through the same
    # copy_artifacts_to_sidecar primitive aida.documents.writers.md_obsidian's
    # write_markdown_document uses for freeform reports — "one writer" for
    # the image/file-copying mechanics, even though a transcript's own text
    # rendering (below, render_transcript) stays its own thing.
    image_placeholders: list[ImageArtifact | FileArtifact] = [
        ImageArtifact(data=b"", id=art.id, mime_type=art.mime_type or "", path=art.path)
        for art in artifacts
        if art.kind == "ImageArtifact" and art.path and Path(art.path).exists()
    ]
    # Non-image artifacts the agent produced (saved scripts, generated
    # reports, data files) — linked the same way images already are, so
    # "what did the agent write" is answerable from the transcript alone
    # instead of only from whatever tool-result text happens to mention it.
    file_placeholders: list[ImageArtifact | FileArtifact] = [
        FileArtifact(id=art.id, path=art.path, mime_type=art.mime_type)
        for art in artifacts
        if art.kind == "FileArtifact" and art.path and Path(art.path).exists()
    ]
    # Keyed by the *record's* artifact id (hence id=art.id above — a fresh
    # placeholder would otherwise get a brand-new random id and the link
    # lookup in render_transcript would never match), so the links below
    # point at the filename each artifact actually got in the sidecar folder.
    copied = copy_artifacts_to_sidecar(
        image_placeholders + file_placeholders, target_dir, artifact_store
    )
    sidecar_filenames = {artifact_id: path.name for artifact_id, path in copied.items()}

    # Read off disk rather than tracked separately: the folder is the
    # record. A file the user dropped in there by hand is listed too, and
    # one they deleted stops being listed, with no bookkeeping to drift.
    attachment_names: list[str] = []
    if attachments_path is not None and attachments_path.is_dir():
        present = {entry.name for entry in attachments_path.iterdir() if entry.is_file()}
        # List the documents themselves, not what was derived from them:
        # `paper.pdf.md` holds the extracted text of `paper.pdf` and is not
        # a second attachment. Recognised by the companion actually being
        # there, rather than by trusting a naming convention on its own.
        attachment_names = sorted(
            name
            for name in present
            if not (name.endswith(".md") and name[: -len(".md")] in present)
        )

    text = render_transcript(
        conversation_id=conversation_id,
        title=title,
        workspace_name=workspace_name,
        profile_name=profile_name,
        messages=messages,
        artifacts=artifacts,
        sidecar_dirname=sidecar_dirname,
        sidecar_filenames=sidecar_filenames,
        attachment_names=attachment_names,
        tool_result_mode=tool_result_mode,
    )
    path.write_text(text, encoding="utf-8")
    return path


__all__ = [
    "ATTACHMENTS_DIRNAME",
    "DEFAULT_TOOL_RESULT_MODE",
    "TOOL_RESULT_MODES",
    "attachments_dir",
    "record_file_path",
    "render_transcript",
    "sidecar_dir",
    "slugify",
    "write_transcript",
]
