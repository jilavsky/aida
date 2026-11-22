"""Context building: system prompt + skills files + conversation history.

Phase 2 uses the *direct context* strategy (BeamlineAdvisor's approach,
PLAN.md §2 row 3 / §4): skills are plain Markdown files loaded straight into
the system message. RAG (indexed retrieval for larger corpora) is Phase 8 —
nothing here talks to an index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path

from aida.config.logging_setup import get_logger
from aida.providers.base import Message, ToolCall, ToolSchema

logger = get_logger("context")

# Rough, deliberately simple token estimate (no tokenizer dependency in
# Phase 2): ~4 characters per token is a reasonable average for English/code
# and is only used for a soft trim budget, not billing.
_CHARS_PER_TOKEN_ESTIMATE = 4

# PLAN.md §1.3 / planning/completed/context_management.md §2b: the 4-chars-per-token
# average is fair for English prose, but JSON and dense numeric data (tool
# call arguments, tool results — the bulk of a pyIrena session) tokenize
# closer to ~3 chars/token, so the plain estimator systematically
# *undercounts* exactly the content that matters most for a long analysis
# conversation. Used wherever the content being estimated is one of those,
# never for ordinary assistant/user prose.
DENSE_CHARS_PER_TOKEN = 3

# planning/completed/context_management.md §3.3: a vision image's rough token cost —
# derived from Anthropic's own ~(width x height)/750 rule for a ~1024px
# image (AIDA already downscales tool-result/attached images to ~1024px in
# aida.providers.vision before they're sent), i.e. roughly
# (1024*1024)/750 ~= 1400, rounded up for headroom since not every image is
# exactly square. Deliberately one flat number rather than reading actual
# dimensions — good enough to decide "are we getting close to the limit",
# not a billing figure.
IMAGE_TOKEN_ESTIMATE = 1600

# planning/completed/context_management.md §3.2: covers estimator error (§2b) plus
# each provider's own per-request overhead around the raw message content.
# One knob, not five — every other constant below is either derived from
# measurement (IMAGE_TOKEN_ESTIMATE) or a provider default
# (DEFAULT_RESERVED_OUTPUT_TOKENS), not a second safety margin stacked on
# top of this one.
CONTEXT_SAFETY_FRACTION = 0.85

# aida.providers.anthropic_.DEFAULT_MAX_TOKENS — the default when a profile
# doesn't set its own max_tokens — a reasonable stand-in for an
# OpenAI-compatible endpoint too, where output is otherwise unbounded and
# *something* has to be reserved out of the window for the reply that's
# about to be generated.
DEFAULT_RESERVED_OUTPUT_TOKENS = 8192

# Below this, the computed budget is not "tight", it is a misconfiguration —
# a window too small for the tool set actually enabled (see
# estimate_tool_schema_tokens). Clamping to this floor rather than trimming
# down to (near-)nothing keeps the very next turn answerable; the warning
# logged when this fires is the signal to fix the real problem (a smaller
# tool group, or an explicit larger context_window).
MIN_HISTORY_BUDGET = 8000


def estimate_tokens(text: str) -> int:
    """Cheap token estimate for context-size management. Not exact — good
    enough to decide "are we getting close to the limit"."""
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def estimate_tokens_dense(text: str) -> int:
    """Same shape as ``estimate_tokens``, but calibrated for dense JSON/
    numeric content rather than English prose — see ``DENSE_CHARS_PER_TOKEN``.
    Used for tool-call arguments and ``role="tool"`` message content inside
    ``estimate_message_tokens``, and for tool schemas in
    ``estimate_tool_schema_tokens``."""
    return max(1, len(text) // DENSE_CHARS_PER_TOKEN)


def estimate_tool_schema_tokens(tools: list[ToolSchema]) -> int:
    """The token cost of the tool schemas sent on *every* request alongside
    the message list — previously invisible to the budget entirely (PLAN.md
    §1.3: pyirena-mcp's 68 tools measured at ~40.8 KB of JSON, ~10,200
    tokens, on every single turn). Recomputed fresh each call rather than
    cached: the enabled tool set can change mid-session (starting/stopping
    an MCP server from the MCP dialog), and this is just a ``json.dumps``
    plus a ``len`` — cheap enough that caching would be premature."""
    if not tools:
        return 0
    payload = json.dumps(
        [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools],
        default=str,
    )
    return estimate_tokens_dense(payload)


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
    text turns.

    PLAN.md §1.3 (§2b/§3.3): tool-call arguments and ``role="tool"`` result
    content are dense JSON/numeric data, not prose — estimated with
    ``estimate_tokens_dense`` rather than the plain estimator, which
    otherwise systematically undercounts exactly the bulk of a pyIrena
    session. Vision images (``message.images``, B1) are counted too, at a
    flat ``IMAGE_TOKEN_ESTIMATE`` each — previously not counted at all."""
    is_dense_content = message.role == "tool"
    total = (
        estimate_tokens_dense(message.content)
        if is_dense_content
        else estimate_tokens(message.content)
    )
    for call in message.tool_calls:
        payload = json.dumps({"name": call.name, "arguments": call.arguments}, default=str)
        total += estimate_tokens_dense(payload)
    total += IMAGE_TOKEN_ESTIMATE * len(message.images)
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
            texts.append(render_skill_text(name, path, skills_dir))
    return texts


#: Placeholder a skill may use for the absolute path of its own folder, so
#: it can name its scripts portably (``{skill_dir}/scripts/fit.py``) on any
#: machine, user account or OS. Replaced literally, not with str.format, so
#: other braces in a skill (code samples, JSON) are left alone.
SKILL_DIR_PLACEHOLDER = "{skill_dir}"


def _skill_extra_files(folder: Path) -> list[str]:
    """Relative paths of the files a folder skill carries besides
    ``SKILL.md`` (scripts, templates, reference notes), skipping caches."""
    names: list[str] = []
    for item in sorted(folder.rglob("*")):
        if not item.is_file() or item.name == "SKILL.md":
            continue
        relative = item.relative_to(folder)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        names.append(relative.as_posix())
    return names


def render_skill_text(name: str, path: Path, skills_dir: Path) -> str:
    """One skill as it goes into the system context.

    For a folder skill (``<name>/SKILL.md``), ``{skill_dir}`` becomes the
    folder's absolute path, and when the folder holds more than
    ``SKILL.md`` a short note lists those files and says how to use them —
    so a skill that ships code works without the user configuring
    anything. For a single-file skill ``{skill_dir}`` is the skills folder.
    """
    text = path.read_text(encoding="utf-8")
    is_folder_skill = path.name == "SKILL.md" and path.parent != skills_dir
    folder = path.parent if is_folder_skill else skills_dir
    text = text.replace(SKILL_DIR_PLACEHOLDER, str(folder))
    header = f"# Skill: {name}\n\n"
    if is_folder_skill:
        extra = _skill_extra_files(folder)
        if extra:
            shown = extra[:MAX_LISTED_SKILL_FILES]
            listing = "\n".join(f"- `{item}`" for item in shown)
            if len(extra) > len(shown):
                listing += f"\n- … and {len(extra) - len(shown)} more"
            header += (
                f"This skill's files are in `{folder}`:\n{listing}\n\n"
                "You can read them, and run its Python scripts with `run_python_script` "
                "(they run with the workspace target folder as the working directory). "
                "The folder is read-only: do not write outputs there.\n\n"
            )
    return header + text


#: A folder skill with many files lists only the first ones in its note.
MAX_LISTED_SKILL_FILES = 30


#: Heading the workspace-folders context block starts with. Used both to
#: build the block and to find it again inside a live session's
#: ``extra_context_texts`` when the user changes the workspace's folders
#: mid-conversation (``ChatSession.update_workspace_folders``), so the block
#: is replaced rather than duplicated.
WORKSPACE_FOLDERS_HEADING = "# Workspace folders"


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

    That paragraph also tells the model to prefer building an *absolute*
    path from ``scratch_dir`` for MCP tool file arguments (a separate bug
    report: the model lost track of a relative-path screenshot it couldn't
    find afterward) — but a server that manages its own independent output
    sandbox (Playwright MCP configured with its own ``--output-dir``, say)
    rejects that absolute path outright, since its own allowed roots don't
    include AIDA's scratch folder at all. The paragraph's last sentence
    covers that: fall back to a bare filename or the folder the tool's own
    error named, rather than retrying the same rejected path.
    """
    if not source_folders and not target_folder and not global_allowed_folders and not scratch_dir:
        return None

    lines = [WORKSPACE_FOLDERS_HEADING]
    if source_folders:
        lines.append("")
        lines.append(
            "Source folder(s) — read data from here (list_directory/find_files/search_text/read_file):"
        )
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
            "the user needs kept.\n"
            "Every MCP tool server also starts with this folder as its working directory. If an MCP "
            "tool takes a relative filename/path argument (e.g. a browser tool's screenshot or "
            f"download path) and you don't pass an absolute one, that's where it lands — e.g. a "
            f"relative filename of 'shot.png' resolves to {scratch_dir}/shot.png. Prefer passing the "
            "tool an absolute path built from the folder above in the first place; otherwise expect "
            "its output there instead of guessing afterward or searching the filesystem for it.\n"
            "Exception: some MCP servers (browser/screenshot automation tools especially) enforce "
            "their own separate output directory as a security sandbox, independent of this scratch "
            "folder — if a tool call fails with an error naming *different* allowed folders (e.g. "
            '"File access denied ... outside allowed roots"), that server manages its own location: '
            "retry with a bare filename and no directory (most such tools resolve it against their own "
            "configured output dir), or use the folder the error itself named. Don't keep retrying with "
            "an absolute path built from the scratch folder above once a tool has already told you it "
            "doesn't accept that."
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


def build_identity_context_block(*, assistant_name: str, user_context: str) -> str | None:
    """Global (not per-workspace) identity/user framing — B15 user request:
    nothing in the system context ever told the model its own name or
    anything about the person it's talking to; the user would otherwise
    have to retype that into every workspace's own ``system_prompt``.

    ``assistant_name`` is expected to always be set (``AppConfig.
    assistant_name`` defaults to ``"Aida"`` and falls back to that default
    for a blank value — see ``AppConfig.from_dict``), so this only returns
    ``None`` if a caller passes both fields empty. ``user_context`` is
    opt-in and empty by default — a fresh install says nothing about the
    user until they fill in "Personal context" in Settings.
    """
    name = (assistant_name or "").strip()
    context = (user_context or "").strip()
    if not name and not context:
        return None
    lines = []
    if name:
        lines.append(f"Your name is {name}. The user may address you by that name.")
    if context:
        lines.append(context)
    return "\n".join(lines)


def build_system_message(
    system_prompt: str | None,
    skill_texts: list[str],
    *,
    extra_texts: list[str] | None = None,
    identity_text: str | None = None,
) -> Message:
    """Combine an optional system prompt, dynamically-generated context
    blocks, and any loaded skill texts into a single system ``Message``.

    ``identity_text`` (B15 — ``build_identity_context_block``'s output)
    comes first, ahead of even ``system_prompt``: a workspace's own persona
    ("You are a synchrotron data-reduction assistant...") reads oddly
    opening a conversation before the model even knows its own name, so the
    global "who am I / who am I talking to" framing is foundational to
    everything after it, including the workspace's own prompt.

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
        for p in (
            ([identity_text] if identity_text else [])
            + ([system_prompt] if system_prompt else [])
            + (extra_texts or [])
            + skill_texts
        )
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
                or Message(role="tool", content=placeholder, tool_call_id=call.id, name=call.name)
            )
        index = scan
    return repaired


#: Heading of the message that replaces summarized turns. Also how a
#: summary is recognized again (``is_compaction_summary``): it lives only in
#: memory, never in the database, so no persisted flag is needed.
COMPACTION_SUMMARY_HEADING = "# Summary of earlier conversation (compacted)"


def is_compaction_summary(message: Message) -> bool:
    """Whether ``message`` is a compaction summary (see
    ``compaction_summary_message``)."""
    return message.role == "user" and message.content.startswith(COMPACTION_SUMMARY_HEADING)


def summary_body(message: Message) -> str:
    """The summary text of a compaction summary message, without its heading."""
    return message.content.removeprefix(COMPACTION_SUMMARY_HEADING).strip()


@dataclass(frozen=True)
class TrimPlan:
    """What ``plan_trim`` decided: which whole turns to drop to fit
    ``max_tokens``, without committing to *how* they get removed. Plain
    discard (``trim_history``) and summarize-then-replace
    (``aida.core.session.ChatSession._trim_context``) both start from the
    same plan, so they agree about *which* turns go and differ only in what
    replaces them.

    ``previous_summary`` is the compaction summary already at the front of
    the history, if any. It is never one of the ``dropped_turns``: a summary
    is only ever folded into a new one together with real turns, never
    re-summarized on its own."""

    system_messages: list[Message]
    kept_turn_messages: list[Message]
    dropped_turns: list[list[Message]]
    was_trimmed: bool
    previous_summary: Message | None = None

    @property
    def kept_messages(self) -> list[Message]:
        """The plain-discard result: system messages, the previous summary
        (still accurate for what it covers) and the kept turns."""
        summary = [self.previous_summary] if self.previous_summary is not None else []
        return [*self.system_messages, *summary, *self.kept_turn_messages]


def plan_trim(
    messages: list[Message],
    max_tokens: int,
    *,
    min_recent_turns: int = 4,
    fallback_min_recent_turns: int | None = None,
    keep_summary: bool = True,
) -> TrimPlan:
    """Decide which oldest whole turns would need to go to fit
    ``max_tokens`` (estimated), without dropping them — see ``TrimPlan``.

    Always keeps every ``role="system"`` message and at least
    ``min_recent_turns`` of the most recent turns. If that still leaves the
    history over budget and ``fallback_min_recent_turns`` is given, dropping
    continues down to that smaller floor: a long autonomous run can sit
    almost entirely inside the last few turns, and the normal floor would
    then protect everything. Neither floor is ever crossed, even if that
    means staying over budget, so the very next turn stays answerable.

    With ``keep_summary`` (the default) a compaction summary at the front of
    the history is held aside as ``TrimPlan.previous_summary``: it counts
    toward the budget but is never dropped on its own. ``keep_summary=False``
    treats it as an ordinary turn (``clear_context`` drops everything).

    Whole *turns* (a user message plus the assistant/tool messages it
    produced — see ``split_into_turns``), never individual messages: cutting
    between an assistant message and the tool results answering its calls
    produces exactly the orphaned-``tool_use`` history both providers reject
    outright.
    """
    system_messages = [m for m in messages if m.role == "system"]
    turns = split_into_turns(messages)

    previous_summary: Message | None = None
    if keep_summary and turns and is_compaction_summary(turns[0][0]):
        previous_summary = turns[0][0]
        turns = [turns[0][1:], *turns[1:]] if len(turns[0]) > 1 else turns[1:]

    def total_tokens(msgs: list[Message]) -> int:
        return sum(estimate_message_tokens(m) for m in msgs)

    fixed_tokens = total_tokens(system_messages)
    if previous_summary is not None:
        fixed_tokens += estimate_message_tokens(previous_summary)
    turn_tokens = [total_tokens(turn) for turn in turns]

    def over_budget(dropped: int) -> bool:
        return fixed_tokens + sum(turn_tokens[dropped:]) > max_tokens

    dropped = 0
    while over_budget(dropped) and len(turns) - dropped > min_recent_turns:
        dropped += 1
    if fallback_min_recent_turns is not None:
        while over_budget(dropped) and len(turns) - dropped > fallback_min_recent_turns:
            dropped += 1

    kept_turn_messages = [m for turn in turns[dropped:] for m in turn]
    return TrimPlan(
        system_messages,
        kept_turn_messages,
        turns[:dropped],
        bool(dropped),
        previous_summary,
    )


def trim_history(
    messages: list[Message], max_tokens: int, *, min_recent_turns: int = 4
) -> tuple[list[Message], bool]:
    """Drop oldest whole turns until under ``max_tokens`` (estimated).
    Returns ``(trimmed_messages, was_trimmed)``. A thin wrapper over
    ``plan_trim`` — see its docstring and ``TrimPlan`` for the shared
    decision both this and compaction build on."""
    plan = plan_trim(messages, max_tokens, min_recent_turns=min_recent_turns)
    return plan.kept_messages, plan.was_trimmed


# --- Stage A: shrinking old tool results in place ---------------------------

#: How many of the most recent tool rounds (an assistant message with tool
#: calls plus its results) ``shrink_old_tool_results`` never touches: the
#: model is usually still working with them.
KEEP_RECENT_TOOL_ROUNDS = 3

#: Tool results shorter than this are left alone: a stub would save little
#: and the model loses the actual answer.
MIN_SHRINK_CHARS = 1000

#: String arguments of old tool calls longer than this are shortened too
#: (``write_file`` content, a long script for ``run_python_script``).
MIN_SHRINK_ARGUMENT_CHARS = 2000

#: Once stage A has to run, it shrinks down to this fraction of the budget
#: rather than to just under it. Stopping right at the limit would shrink one
#: more result on every round trip, and each change to old history also
#: invalidates the provider's prompt cache from that point on.
SHRINK_TARGET_FRACTION = 0.8

SHRUNK_TOOL_RESULT_PREFIX = "[result removed to save context:"


def is_shrunk_tool_result(message: Message) -> bool:
    """Whether a ``role="tool"`` message has already been replaced by a stub."""
    return message.role == "tool" and message.content.startswith(SHRUNK_TOOL_RESULT_PREFIX)


def _call_preview(call: ToolCall | None, limit: int = 200) -> str:
    if call is None:
        return ""
    payload = json.dumps(call.arguments, default=str, ensure_ascii=False)
    if len(payload) > limit:
        payload = payload[: limit - 3] + "..."
    return payload


def _tool_result_stub(message: Message, call: ToolCall | None) -> str:
    name = message.name or (call.name if call else "tool")
    return (
        f"{SHRUNK_TOOL_RESULT_PREFIX} {name}({_call_preview(call)}) returned "
        f"{len(message.content):,} chars — call the tool again if you need it]"
    )


def _shrink_arguments(call: ToolCall) -> ToolCall | None:
    """``call`` with every long string argument replaced by a length note, or
    ``None`` when nothing in it is long enough to bother."""
    changed = False
    arguments = dict(call.arguments)
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) >= MIN_SHRINK_ARGUMENT_CHARS:
            arguments[key] = f"[{len(value):,} chars removed to save context]"
            changed = True
    return replace(call, arguments=arguments) if changed else None


def shrink_old_tool_results(
    messages: list[Message],
    target_tokens: int,
    *,
    keep_recent_rounds: int = KEEP_RECENT_TOOL_ROUNDS,
) -> tuple[list[Message], int]:
    """Replace the content of old, large tool results with short stubs until
    the history is estimated at or below ``target_tokens``.

    Works inside a turn, which dropping whole turns cannot: one autonomous
    turn can hold hundreds of tool calls. Oldest first. The last
    ``keep_recent_rounds`` tool rounds are never touched. A stub keeps
    ``tool_call_id`` and ``name``, so call/result pairing still holds, says
    which call produced it and how big the result was, and drops any images.
    Long string arguments of old assistant tool calls are shortened the same
    way. Messages already stubbed are skipped, so repeated calls are cheap.

    Returns ``(new_messages, number_shrunk)``. The list has the same length
    and order; changed messages are new objects, so the caller's originals
    (and anything already persisted from them) are untouched. A
    ``target_tokens`` of 0 shrinks everything eligible.
    """
    total = sum(estimate_message_tokens(m) for m in messages)
    if total <= target_tokens:
        return messages, 0

    round_starts = [i for i, m in enumerate(messages) if m.role == "assistant" and m.tool_calls]
    if len(round_starts) <= keep_recent_rounds:
        return messages, 0
    protected_from = round_starts[-keep_recent_rounds] if keep_recent_rounds else len(messages)

    calls_by_id = {call.id: call for m in messages[:protected_from] for call in m.tool_calls}
    result = list(messages)
    shrunk = 0
    for index in range(protected_from):
        if total <= target_tokens:
            break
        message = result[index]
        replacement: Message | None = None
        if message.role == "tool" and not is_shrunk_tool_result(message):
            if len(message.content) >= MIN_SHRINK_CHARS or message.images:
                stub = _tool_result_stub(message, calls_by_id.get(message.tool_call_id or ""))
                replacement = replace(message, content=stub, images=[])
        elif message.role == "assistant" and message.tool_calls:
            new_calls = [_shrink_arguments(call) for call in message.tool_calls]
            if any(c is not None for c in new_calls):
                replacement = replace(
                    message,
                    tool_calls=[
                        new or old for new, old in zip(new_calls, message.tool_calls, strict=True)
                    ],
                )
        if replacement is None:
            continue
        total -= estimate_message_tokens(message) - estimate_message_tokens(replacement)
        result[index] = replacement
        shrunk += 1
    return result, shrunk


# --- Stage D: capping one tool result relative to the budget ----------------

#: A single tool result may take this share of the history budget before the
#: rest is moved out to a file (``cap_tool_result``).
TOOL_RESULT_BUDGET_FRACTION = 0.05
#: Never cap below this, however small the window: smaller and the model has
#: to page through every ordinary result.
TOOL_RESULT_MIN_CHARS = 8_000
#: Never allow more than the interactive read limit in one result.
TOOL_RESULT_MAX_CHARS = 100_000


def tool_result_char_cap(history_budget_tokens: int) -> int:
    """How many characters one tool result may keep in the history, given the
    history budget in tokens. Tool output is dense, so the conversion uses
    ``DENSE_CHARS_PER_TOKEN``."""
    cap = int(history_budget_tokens * TOOL_RESULT_BUDGET_FRACTION * DENSE_CHARS_PER_TOKEN)
    return max(TOOL_RESULT_MIN_CHARS, min(TOOL_RESULT_MAX_CHARS, cap))


#: Built-in per-tool caps, in characters, by shell-style pattern on the tool
#: name (MCP tools are ``<server>__<tool>``). A web search returns a list of
#: hits whose start is what the model uses; ~28k characters per search
#: filled most of a long research session. A fetched or extracted page is
#: more useful longer, so it gets more. ``AppConfig.tool_result_char_caps``
#: adds to and overrides these.
DEFAULT_TOOL_RESULT_CAPS: dict[str, int] = {
    "*web_search*": 10_000,
    "*search_web*": 10_000,
    "*web_fetch*": 15_000,
    "*fetch_url*": 15_000,
    "*web_extract*": 15_000,
    "*tavily_extract*": 15_000,
}


def tool_specific_char_cap(tool_name: str, overrides: dict[str, int] | None = None) -> int | None:
    """The per-tool character cap for ``tool_name``, or ``None`` when no
    pattern matches. User ``overrides`` replace a built-in entry with the
    same pattern; when several patterns match, the smallest cap wins. Case
    is ignored. A cap below ``MIN_SHRINK_CHARS`` is raised to it."""
    caps = {**DEFAULT_TOOL_RESULT_CAPS, **(overrides or {})}
    name = tool_name.lower()
    matches = [cap for pattern, cap in caps.items() if fnmatchcase(name, pattern.lower())]
    if not matches:
        return None
    return max(MIN_SHRINK_CHARS, min(matches))


def cap_tool_result(content: str, cap: int, saved_path: Path | None) -> str:
    """Shorten ``content`` to about ``cap`` characters: its head and tail plus
    a note saying how much was left out and, when ``saved_path`` is given,
    where the full text was saved and how to read it in pieces. Content
    within the cap is returned unchanged."""
    if len(content) <= cap:
        return content
    head = content[: int(cap * 0.7)]
    tail = content[-int(cap * 0.2) :]
    omitted = len(content) - len(head) - len(tail)
    if saved_path is not None:
        where = (
            f"The full result was saved to {saved_path}. Read the omitted part with "
            f"read_file(path=..., offset={len(head)}, length=...) if you need it."
        )
    else:
        where = "The full result could not be saved."
    note = f"\n\n[... {omitted:,} of {len(content):,} chars omitted to save context. {where}]\n\n"
    return f"{head}{note}{tail}"


def history_budget(
    *,
    context_window: int,
    reserved_output_tokens: int,
    tool_schema_tokens: int,
    safety_fraction: float = CONTEXT_SAFETY_FRACTION,
) -> int:
    """How many tokens of *history* (messages) can be sent, given a model's
    total ``context_window`` — planning/completed/context_management.md §3.2.

    ``usable = context_window * safety_fraction`` (covers estimator error
    plus each provider's own per-request overhead); the reply about to be
    generated (``reserved_output_tokens``) and the tool schemas sent on
    every request (``tool_schema_tokens``, see
    ``estimate_tool_schema_tokens``) both come out of that before history
    gets anything. If what's left is below ``MIN_HISTORY_BUDGET``, that is
    a misconfiguration (a window too small for the enabled tool set) rather
    than an honest tight budget — logged and clamped to the floor rather
    than trimming the next turn down to unanswerable."""
    usable = int(context_window * safety_fraction)
    budget = usable - reserved_output_tokens - tool_schema_tokens
    if budget < MIN_HISTORY_BUDGET:
        logger.warning(
            "context budget clamped to the %d-token floor: context_window=%d, "
            "reserved_output_tokens=%d, tool_schema_tokens=%d leaves only %d — "
            "consider a leaner MCP group or a larger context_window",
            MIN_HISTORY_BUDGET,
            context_window,
            reserved_output_tokens,
            tool_schema_tokens,
            budget,
        )
        return MIN_HISTORY_BUDGET
    return budget


#: The instruction given to the model for compaction (planning/
#: context_management.md §3.4) — facts, not prose, and explicitly asks it to
#: preserve exactly the things a pyIrena session needs to keep: filenames
#: and numeric results.
COMPACTION_PROMPT = (
    "The conversation turns below are about to be removed from your context to make room for "
    "the rest of this session. Summarize them into a compact set of facts for your own future "
    "reference — not a narrative retelling. Preserve exact filenames, folder paths, parameter "
    "values, and numeric results (fit parameters, Rg, chi-squared, and similar) verbatim; do not "
    "round or paraphrase a number. Cover: files and folders touched, parameters and results with "
    "their exact numbers, decisions made and why, and anything the user explicitly asked to "
    "remember. Write plain factual bullet points — no preamble, and do not restate this "
    "instruction."
)


#: Added to ``COMPACTION_PROMPT`` when an earlier summary exists, so the
#: model folds it in rather than summarizing only the new turns.
COMPACTION_MERGE_INSTRUCTION = (
    "A summary of the conversation before these turns is given first. Merge it with the "
    "turns that follow into one updated summary: keep every fact from it that is still "
    "relevant, and add what the new turns contain."
)

#: In the summary request, each tool result and each long tool-call argument
#: keeps only this many characters from its start and from its end. A
#: summary needs what was done and found, not whole file contents, and the
#: request has to fit in the model's window.
SUMMARY_TOOL_TEXT_EDGE_CHARS = 1500
#: User and assistant text is clipped too, but much more generously.
SUMMARY_MESSAGE_TEXT_EDGE_CHARS = 10_000


def _clip_middle(text: str, edge: int) -> str:
    if len(text) <= 2 * edge + 100:
        return text
    omitted = len(text) - 2 * edge
    return f"{text[:edge]}\n[... {omitted:,} chars omitted ...]\n{text[-edge:]}"


def _render_turns_for_summary(turns: list[list[Message]]) -> list[str]:
    """Flatten turns into plain-text transcript lines for the compaction
    request — the summarizer reads a transcript, not AIDA's internal
    ``Message``/``ToolCall`` shapes. Long tool output, long tool arguments and
    very long messages are clipped to their start and end."""
    lines: list[str] = []
    for turn in turns:
        for message in turn:
            if message.role == "user":
                text = _clip_middle(message.content, SUMMARY_MESSAGE_TEXT_EDGE_CHARS)
                lines.append(f"User: {text}")
            elif message.role == "assistant":
                if message.content:
                    text = _clip_middle(message.content, SUMMARY_MESSAGE_TEXT_EDGE_CHARS)
                    lines.append(f"Assistant: {text}")
                for call in message.tool_calls:
                    payload = _clip_middle(
                        json.dumps(call.arguments, default=str), SUMMARY_TOOL_TEXT_EDGE_CHARS
                    )
                    lines.append(f"Assistant called tool {call.name}({payload})")
            elif message.role == "tool":
                text = _clip_middle(message.content, SUMMARY_TOOL_TEXT_EDGE_CHARS)
                lines.append(f"Tool result ({message.name}): {text}")
    return lines


def summary_transcript_chunks(turns: list[list[Message]], max_chars: int) -> list[str]:
    """The rendered transcript of ``turns``, split into pieces of at most
    about ``max_chars`` characters each, at line boundaries. Usually one
    piece. Each piece is summarized in turn, folding the previous result in
    (see ``ChatSession._compact_context``), so no single request outgrows
    the window. A single line longer than ``max_chars`` is clipped."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in _render_turns_for_summary(turns):
        if len(line) > max_chars:
            line = _clip_middle(line, max_chars // 2 - 100)
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def compaction_request_from_transcript(
    transcript: str, *, previous_summary: str | None = None
) -> list[Message]:
    """The single-message request that asks the active provider to summarize
    ``transcript``, merging ``previous_summary`` in when given. A bare
    ``role="user"`` request, no tools, no system prompt, sent through the
    ordinary ``provider.complete()``."""
    if previous_summary:
        return [
            Message(
                role="user",
                content=(
                    f"{COMPACTION_PROMPT} {COMPACTION_MERGE_INSTRUCTION}\n\n---\n\n"
                    f"Earlier summary:\n{previous_summary}\n\n---\n\n{transcript}"
                ),
            )
        ]
    return [Message(role="user", content=f"{COMPACTION_PROMPT}\n\n---\n\n{transcript}")]


def compaction_request_messages(
    turns: list[list[Message]], *, previous_summary: str | None = None
) -> list[Message]:
    """``compaction_request_from_transcript`` for whole ``turns`` (oldest
    first, as ``plan_trim`` returns them), rendered as one piece."""
    transcript = "\n".join(_render_turns_for_summary(turns))
    return compaction_request_from_transcript(transcript, previous_summary=previous_summary)


def compaction_summary_message(summary_text: str) -> Message:
    """Wrap a model-produced summary as the ``role="user"`` message that
    replaces the turns it summarizes. User-role (not a synthetic assistant
    message) is the safe choice across both API dialects — an assistant
    message with no matching tool-call history risks confusing tool-call
    pairing on the next request."""
    return Message(role="user", content=f"{COMPACTION_SUMMARY_HEADING}\n\n{summary_text}")


__all__ = [
    "COMPACTION_MERGE_INSTRUCTION",
    "COMPACTION_PROMPT",
    "COMPACTION_SUMMARY_HEADING",
    "KEEP_RECENT_TOOL_ROUNDS",
    "SHRINK_TARGET_FRACTION",
    "SHRUNK_TOOL_RESULT_PREFIX",
    "DEFAULT_TOOL_RESULT_CAPS",
    "TOOL_RESULT_BUDGET_FRACTION",
    "tool_specific_char_cap",
    "TOOL_RESULT_MAX_CHARS",
    "TOOL_RESULT_MIN_CHARS",
    "cap_tool_result",
    "compaction_request_from_transcript",
    "is_compaction_summary",
    "is_shrunk_tool_result",
    "shrink_old_tool_results",
    "summary_body",
    "summary_transcript_chunks",
    "tool_result_char_cap",
    "SKILL_DIR_PLACEHOLDER",
    "render_skill_text",
    "CONTEXT_SAFETY_FRACTION",
    "DEFAULT_RESERVED_OUTPUT_TOKENS",
    "DENSE_CHARS_PER_TOKEN",
    "IMAGE_TOKEN_ESTIMATE",
    "MIN_HISTORY_BUDGET",
    "MISSING_TOOL_RESULT",
    "WORKSPACE_FOLDERS_HEADING",
    "SkillInfo",
    "TrimPlan",
    "build_system_message",
    "build_workspace_context_block",
    "compaction_request_messages",
    "compaction_summary_message",
    "estimate_message_tokens",
    "estimate_tokens",
    "estimate_tokens_dense",
    "estimate_tool_schema_tokens",
    "history_budget",
    "list_skills",
    "load_skill_texts",
    "plan_trim",
    "repair_tool_call_pairing",
    "skill_exists",
    "skill_path",
    "split_into_turns",
    "trim_history",
]
