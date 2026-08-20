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


def build_system_message(system_prompt: str | None, skill_texts: list[str]) -> Message:
    """Combine an optional system prompt with any loaded skill texts into a
    single system ``Message``."""
    parts = [p for p in ([system_prompt] if system_prompt else []) + skill_texts if p]
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
    "estimate_tokens",
    "list_skills",
    "load_skill_texts",
    "skill_exists",
    "skill_path",
    "trim_history",
]
