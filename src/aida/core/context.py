"""Context building: system prompt + skills files + conversation history.

Phase 2 uses the *direct context* strategy (BeamlineAdvisor's approach,
PLAN.md §2 row 3 / §4): skills are plain Markdown files loaded straight into
the system message. RAG (indexed retrieval for larger corpora) is Phase 8 —
nothing here talks to an index.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aida.providers.base import Message

# Rough, deliberately simple token estimate (no tokenizer dependency in
# Phase 2): ~4 characters per token is a reasonable average for English/code
# and is only used for a soft trim budget, not billing.
_CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Cheap token estimate for context-size management. Not exact — good
    enough to decide "are we getting close to the limit"."""
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def skill_path(skills_dir: Path, name: str) -> Path | None:
    """Resolve a skill name to its file, or ``None`` if it doesn't exist.

    A skill is a Markdown file ``<skills_dir>/<name>.md`` or a folder
    ``<skills_dir>/<name>/SKILL.md``.
    """
    candidate_file = skills_dir / f"{name}.md"
    if candidate_file.is_file():
        return candidate_file
    candidate_folder = skills_dir / name / "SKILL.md"
    if candidate_folder.is_file():
        return candidate_folder
    return None


def skill_exists(skills_dir: Path, name: str) -> bool:
    return skill_path(skills_dir, name) is not None


@dataclass(frozen=True)
class SkillInfo:
    """One discovered skill — name plus the file its content actually
    lives in (a bare ``<name>.md`` or a ``<name>/SKILL.md`` folder, same
    two shapes ``skill_path`` resolves)."""

    name: str
    path: Path


def list_skills(skills_dir: Path) -> list[SkillInfo]:
    """Enumerate every skill under ``skills_dir`` (Phase 7's skills
    browser: "list, preview, open in external editor, new-from-template").
    No enumeration helper existed before this — every other caller
    (``load_skill_texts``, ``aida.workspace.workspaces.validate_workspace``)
    only ever looks up skills *named* by config, it never lists what's
    actually on disk. Sorted by name for a stable UI listing; a name that
    has both a ``<name>.md`` file *and* a ``<name>/SKILL.md`` folder is
    listed once, preferring the bare file (matching ``skill_path``'s own
    file-before-folder precedence).
    """
    if not skills_dir.is_dir():
        return []
    found: dict[str, Path] = {}
    for entry in skills_dir.iterdir():
        if entry.is_file() and entry.suffix == ".md":
            found.setdefault(entry.stem, entry)
        elif entry.is_dir():
            candidate = entry / "SKILL.md"
            if candidate.is_file():
                found.setdefault(entry.name, candidate)
    return [SkillInfo(name=name, path=found[name]) for name in sorted(found)]


def load_skill_texts(skills_dir: Path, skill_names: list[str]) -> list[str]:
    """Load each named skill's Markdown content from ``skills_dir``.

    Missing skills are skipped (not a hard error) — a workspace can list a
    skill that hasn't been added yet without aida.chat crashing. Use
    ``skill_exists`` if a caller needs to warn about that instead of
    silently skipping (``aida.workspace.workspaces.validate_workspace`` does).
    """
    texts: list[str] = []
    for name in skill_names:
        path = skill_path(skills_dir, name)
        if path is not None:
            texts.append(f"# Skill: {name}\n\n{path.read_text(encoding='utf-8')}")
    return texts


def build_workspace_context_block(
    *,
    source_folders: list[str],
    target_folder: str | None,
    global_allowed_folders: list[str],
    sidecar_dirname: str,
    safety_mode: str,
) -> str | None:
    """A short, factual block telling the model exactly which folders this
    session gives it access to, and what its safety mode means for them.

    Bug report: "Agent seems to have no understanding of Source and Target
    folders." Before this existed, nothing in the system context ever
    mentioned a workspace's actual configured paths — the model could only
    learn them if the user typed them out, or by guessing a starting point
    for ``list_directory``/``find_files`` with nothing to guess from. This
    is not something a skills file can fix: a skill is static Markdown
    shared across every workspace that lists it, but these paths are
    per-workspace, user-configured data (a different data folder for every
    real session), so they have to be generated fresh at session-start time
    instead of hand-authored.

    Returns ``None`` when there is nothing to say (no source/target/global
    folders configured at all — e.g. a bare ``--profile`` chat with no
    workspace and nothing globally allowed).
    """
    if not source_folders and not target_folder and not global_allowed_folders:
        return None

    lines = ["# Workspace folders"]
    if source_folders:
        lines.append("")
        lines.append("Source folder(s) — read data from here (list_directory/find_files/search_text/read_file):")
        lines.extend(f"- {folder}" for folder in source_folders)
    if target_folder:
        lines.append("")
        lines.append(f"Target folder — write generated reports and files here: {target_folder}")
        lines.append(
            f"Images you embed in a report are copied into a `{sidecar_dirname}` sidecar folder next "
            "to it automatically — pass their artifact ids to write_markdown_report/write_docx_report, "
            "don't manage that folder yourself."
        )
    if global_allowed_folders:
        lines.append("")
        lines.append("Also allowed, in every workspace:")
        lines.extend(f"- {folder}" for folder in global_allowed_folders)

    lines.append("")
    if safety_mode == "relaxed":
        lines.append(
            "Safety mode: relaxed — you may read, write, move, and delete files inside the folders "
            "above without asking first. Anything outside them still requires confirmation."
        )
    else:
        lines.append(
            "Safety mode: confirm — the user will be asked to approve each write/delete inside the "
            "folders above, and anything outside them, before it happens."
        )
    return "\n".join(lines)


def build_coding_context_block(
    *, python_interpreter: str | None, command_allowlist: list[str], scripting_enabled: bool
) -> str | None:
    """Tells the model which interpreter ``run_python_script`` actually
    uses and which shell commands ``run_command`` doesn't need confirmation
    for (Phase 9).

    Bug report: with nothing telling it otherwise, the model resorted to a
    raw ``python3 -c "..."`` probe through ``run_command`` just to discover
    which interpreter/packages were available — an ad hoc command that was
    never on the allowlist, so it needed confirmation for something
    ``run_python_script`` (not allowlist-gated at all) would have answered
    directly. Same "generate fresh per session, not a static skill" reasoning
    as ``build_workspace_context_block`` — the interpreter path and
    allowlist are per-workspace, user-configured data.

    Returns ``None`` when scripting is disabled for this workspace —
    ``run_python_script``/``run_command`` aren't even registered then, so
    there's nothing useful to say.
    """
    if not scripting_enabled:
        return None

    lines = ["# Python execution"]
    lines.append("")
    lines.append(
        f"run_python_script runs scripts with: {python_interpreter or 'the interpreter AIDA itself is running under'}. "
        "Prefer it (write a small script, even a one-liner saved to a temp path) over run_command for anything "
        "Python-related, including checking what's importable — it isn't gated by the command allowlist below, "
        "only by the workspace's normal folder-safety rules."
    )
    lines.append("")
    if command_allowlist:
        lines.append("run_command only runs these without asking for confirmation first:")
        lines.extend(f"- {pattern}" for pattern in command_allowlist)
    else:
        lines.append(
            "run_command has no allowlisted commands configured for this workspace — every run_command "
            "call asks for confirmation first."
        )
    return "\n".join(lines)


def build_system_message(
    system_prompt: str | None, skill_texts: list[str], *, extra_texts: list[str] | None = None
) -> Message:
    """Combine an optional system prompt, dynamically-generated context
    blocks, and any loaded skill texts into a single system ``Message``.

    ``extra_texts`` sits between ``system_prompt`` and ``skill_texts`` —
    generated fresh at session-start time rather than loaded from a file
    (``aida.cli.chat.start_session`` uses it for
    ``build_workspace_context_block``'s output and for each connected MCP
    server's own ``instructions`` from its initialize handshake, via
    ``aida.mcp.manager.McpManager.server_instructions``). Foundational
    operational facts belong before domain skill guidance, so this
    ordering is deliberate.
    """
    parts = [
        p
        for p in ([system_prompt] if system_prompt else []) + (extra_texts or []) + skill_texts
        if p
    ]
    return Message(role="system", content="\n\n---\n\n".join(parts))


def trim_history(
    messages: list[Message], max_tokens: int, *, min_recent_turns: int = 4
) -> tuple[list[Message], bool]:
    """Drop oldest non-system messages until under ``max_tokens`` (estimated).

    Always keeps every ``role="system"`` message and at least
    ``min_recent_turns`` of the most recent non-system messages, even if that
    means staying over budget — trimming should never make the very next
    turn unanswerable. Returns ``(trimmed_messages, was_trimmed)``.
    """
    system_messages = [m for m in messages if m.role == "system"]
    other_messages = [m for m in messages if m.role != "system"]

    def total_tokens(msgs: list[Message]) -> int:
        return sum(estimate_tokens(m.content) for m in msgs)

    was_trimmed = False
    while (
        total_tokens(system_messages) + total_tokens(other_messages) > max_tokens
        and len(other_messages) > min_recent_turns
    ):
        other_messages.pop(0)
        was_trimmed = True

    return system_messages + other_messages, was_trimmed


__all__ = [
    "SkillInfo",
    "build_system_message",
    "build_workspace_context_block",
    "estimate_tokens",
    "list_skills",
    "load_skill_texts",
    "skill_exists",
    "skill_path",
    "trim_history",
]
