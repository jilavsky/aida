"""Context building: system prompt + skills files + conversation history.

Phase 2 uses the *direct context* strategy (BeamlineAdvisor's approach,
PLAN.md §2 row 3 / §4): skills are plain Markdown files loaded straight into
the system message. RAG (indexed retrieval for larger corpora) is Phase 8 —
nothing here talks to an index.
"""

from __future__ import annotations

import json
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


def estimate_message_tokens(message: Message) -> int:
    """``estimate_tokens`` for one whole ``Message`` — B7: the plain
    ``estimate_tokens(message.content)`` ``trim_history`` used to budget on
    ignored ``tool_calls`` entirely, so a tool-heavy session (a big
    ``arguments`` payload on every assistant turn, ``content`` often
    empty/short) skewed the estimate low — the trim budget looked like it
    had headroom it didn't actually have. Adds the JSON size of every tool
    call's ``name``+``arguments`` on top of the content estimate; a message
    with no tool calls costs exactly what ``estimate_tokens(message.content)``
    already did, so this is purely additive, never a regression for plain
    text turns."""
    total = estimate_tokens(message.content)
    for call in message.tool_calls:
        payload = json.dumps({"name": call.name, "arguments": call.arguments}, default=str)
        total += estimate_tokens(payload)
    return total


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
    scratch_dir: str | None = None,
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

    ``scratch_dir`` (bug report: "Agents seem to be saving temporary files
    ... in random places") is called out in its own paragraph rather than
    folded into ``global_allowed_folders`` — it's always allowed already
    (every MCP server is launched with it as its own working directory, see
    ``aida.mcp.server``), so mentioning it here is purely to give the model
    a stable place to *choose* to put scratch work, not to grant access.
    """
    if not source_folders and not target_folder and not global_allowed_folders and not scratch_dir:
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
    if scratch_dir:
        lines.append("")
        lines.append(
            f"Scratch folder — put temporary/working files here (throwaway scripts, downloads, "
            f"intermediate output), not in a repo or the source/target folders above: {scratch_dir}\n"
            "It is not backed up and may be cleared out periodically — don't rely on it for anything "
            "the user needs kept."
        )

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


#: What a tool call with no recorded result is backfilled with by
#: ``repair_tool_call_pairing``. Distinct from
#: ``aida.core.agent.CANCELLED_TOOL_RESULT`` on purpose: that one is written
#: at the moment a call is cancelled and knows *why*; this one is a repair
#: applied to history that arrived already broken (a crash or force-quit
#: mid-turn, an older conversation recorded before the cancel path answered
#: its own calls).
MISSING_TOOL_RESULT = "(no result recorded — the session ended before this tool call finished)"


def split_into_turns(messages: list[Message]) -> list[list[Message]]:
    """Group non-system messages into whole turns.

    A turn starts at a ``role="user"`` message and runs up to (not
    including) the next one, so an assistant message and every ``role=
    "tool"`` result it triggered stay together. Any messages before the
    first user message (a resumed history that starts mid-turn, say) form
    one leading group of their own rather than being dropped.
    """
    turns: list[list[Message]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns


def repair_tool_call_pairing(
    messages: list[Message], *, placeholder: str = MISSING_TOOL_RESULT
) -> list[Message]:
    """Return ``messages`` with assistant tool calls and tool results
    guaranteed to line up, so a provider can't reject the whole history.

    Both providers require a strict pairing: every ``tool_call`` on an
    assistant message must be answered by a ``role="tool"`` message, and
    every tool message must answer a call that was actually announced.
    History can violate that without anything being wrong *now* — a session
    killed (crash, force-quit, Stop pressed by an older build) after the
    assistant message was persisted but before its tool results were, or a
    trim that cut between the two. One such gap wedges the conversation
    permanently: every later turn fails, not just the interrupted one.

    Repairs, in order of appearance: a missing tool result is backfilled
    with ``placeholder``; results are emitted in the order their calls were
    announced; a tool message answering no announced call is dropped.
    The input list is not modified.
    """
    repaired: list[Message] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "tool":
            index += 1  # orphan: no assistant tool call announced it
            continue

        repaired.append(message)
        if not (message.role == "assistant" and message.tool_calls):
            index += 1
            continue

        call_ids = [tc.id for tc in message.tool_calls]
        answers: dict[str, Message] = {}
        scan = index + 1
        while scan < len(messages) and messages[scan].role == "tool":
            answer = messages[scan]
            if answer.tool_call_id in call_ids and answer.tool_call_id not in answers:
                answers[answer.tool_call_id] = answer
            scan += 1

        for call in message.tool_calls:
            repaired.append(
                answers.get(call.id)
                or Message(
                    role="tool", content=placeholder, tool_call_id=call.id, name=call.name
                )
            )
        index = scan
    return repaired


def trim_history(
    messages: list[Message], max_tokens: int, *, min_recent_turns: int = 4
) -> tuple[list[Message], bool]:
    """Drop oldest whole turns until under ``max_tokens`` (estimated).

    Always keeps every ``role="system"`` message and at least
    ``min_recent_turns`` of the most recent turns, even if that means
    staying over budget — trimming should never make the very next turn
    unanswerable. Returns ``(trimmed_messages, was_trimmed)``.

    Whole *turns* (a user message plus the assistant/tool messages it
    produced — see ``split_into_turns``), never individual messages: cutting
    between an assistant message and the tool results answering its calls
    produces exactly the orphaned-``tool_use`` history both providers reject
    outright, so a message-at-a-time trim would break the conversation it
    was meant to keep alive.
    """
    system_messages = [m for m in messages if m.role == "system"]
    turns = split_into_turns(messages)

    def total_tokens(msgs: list[Message]) -> int:
        return sum(estimate_message_tokens(m) for m in msgs)

    system_tokens = total_tokens(system_messages)
    turn_tokens = [total_tokens(turn) for turn in turns]

    dropped = 0
    while (
        system_tokens + sum(turn_tokens[dropped:]) > max_tokens
        and len(turns) - dropped > min_recent_turns
    ):
        dropped += 1

    if not dropped:
        return list(messages), False
    kept = [m for turn in turns[dropped:] for m in turn]
    return system_messages + kept, True


__all__ = [
    "MISSING_TOOL_RESULT",
    "SkillInfo",
    "build_system_message",
    "build_workspace_context_block",
    "estimate_message_tokens",
    "estimate_tokens",
    "list_skills",
    "load_skill_texts",
    "repair_tool_call_pairing",
    "skill_exists",
    "skill_path",
    "split_into_turns",
    "trim_history",
]
