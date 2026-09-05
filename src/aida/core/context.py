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

from aida.config.logging_setup import get_logger
from aida.providers.base import Message, ToolSchema

logger = get_logger("context")

# Rough, deliberately simple token estimate (no tokenizer dependency in
# Phase 2): ~4 characters per token is a reasonable average for English/code
# and is only used for a soft trim budget, not billing.
_CHARS_PER_TOKEN_ESTIMATE = 4

# PLAN.md §1.3 / planning/context_management.md §2b: the 4-chars-per-token
# average is fair for English prose, but JSON and dense numeric data (tool
# call arguments, tool results — the bulk of a pyIrena session) tokenize
# closer to ~3 chars/token, so the plain estimator systematically
# *undercounts* exactly the content that matters most for a long analysis
# conversation. Used wherever the content being estimated is one of those,
# never for ordinary assistant/user prose.
DENSE_CHARS_PER_TOKEN = 3

# planning/context_management.md §3.3: a vision image's rough token cost —
# derived from Anthropic's own ~(width x height)/750 rule for a ~1024px
# image (AIDA already downscales tool-result/attached images to ~1024px in
# aida.providers.vision before they're sent), i.e. roughly
# (1024*1024)/750 ~= 1400, rounded up for headroom since not every image is
# exactly square. Deliberately one flat number rather than reading actual
# dimensions — good enough to decide "are we getting close to the limit",
# not a billing figure.
IMAGE_TOKEN_ESTIMATE = 1600

# planning/context_management.md §3.2: covers estimator error (§2b) plus
# each provider's own per-request overhead around the raw message content.
# One knob, not five — every other constant below is either derived from
# measurement (IMAGE_TOKEN_ESTIMATE) or a provider default
# (DEFAULT_RESERVED_OUTPUT_TOKENS), not a second safety margin stacked on
# top of this one.
CONTEXT_SAFETY_FRACTION = 0.85

# Anthropic's own default max_tokens when a profile doesn't set one — a
# reasonable stand-in for an OpenAI-compatible endpoint too, where output is
# otherwise unbounded and *something* has to be reserved out of the window
# for the reply that's about to be generated.
DEFAULT_RESERVED_OUTPUT_TOKENS = 4096

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

    lines = ["# Workspace folders"]
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


@dataclass(frozen=True)
class TrimPlan:
    """What ``plan_trim`` decided: which whole turns to drop to fit
    ``max_tokens``, without committing to *how* they get removed. Plain
    discard (``trim_history``, still exactly what it always was) and
    summarize-then-replace (``aida.core.session.ChatSession._trim_context``,
    PLAN.md §1.3 compaction) both start from the same plan, so the two
    policies can never disagree about *which* turns are old enough to go —
    only about what replaces them."""

    system_messages: list[Message]
    kept_turn_messages: list[Message]
    dropped_turns: list[list[Message]]
    was_trimmed: bool

    @property
    def kept_messages(self) -> list[Message]:
        """``system_messages + kept_turn_messages`` — the plain-discard
        result ``trim_history`` returns."""
        return self.system_messages + self.kept_turn_messages


def plan_trim(messages: list[Message], max_tokens: int, *, min_recent_turns: int = 4) -> TrimPlan:
    """Decide which oldest whole turns would need to go to fit
    ``max_tokens`` (estimated), without dropping them — see ``TrimPlan``.

    Always keeps every ``role="system"`` message and at least
    ``min_recent_turns`` of the most recent turns, even if that means
    staying over budget — trimming should never make the very next turn
    unanswerable.

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
        all_turn_messages = [m for turn in turns for m in turn]
        return TrimPlan(system_messages, all_turn_messages, [], False)
    kept_turn_messages = [m for turn in turns[dropped:] for m in turn]
    return TrimPlan(system_messages, kept_turn_messages, turns[:dropped], True)


def trim_history(
    messages: list[Message], max_tokens: int, *, min_recent_turns: int = 4
) -> tuple[list[Message], bool]:
    """Drop oldest whole turns until under ``max_tokens`` (estimated).
    Returns ``(trimmed_messages, was_trimmed)``. A thin wrapper over
    ``plan_trim`` — see its docstring and ``TrimPlan`` for the shared
    decision both this and compaction build on."""
    plan = plan_trim(messages, max_tokens, min_recent_turns=min_recent_turns)
    return plan.kept_messages, plan.was_trimmed


def history_budget(
    *,
    context_window: int,
    reserved_output_tokens: int,
    tool_schema_tokens: int,
    safety_fraction: float = CONTEXT_SAFETY_FRACTION,
) -> int:
    """How many tokens of *history* (messages) can be sent, given a model's
    total ``context_window`` — planning/context_management.md §3.2.

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


def _render_turns_for_summary(turns: list[list[Message]]) -> str:
    """Flatten dropped turns into plain text for the compaction request —
    the summarizer reads a transcript, not AIDA's internal ``Message``/
    ``ToolCall`` shapes."""
    lines: list[str] = []
    for turn in turns:
        for message in turn:
            if message.role == "user":
                lines.append(f"User: {message.content}")
            elif message.role == "assistant":
                if message.content:
                    lines.append(f"Assistant: {message.content}")
                for call in message.tool_calls:
                    payload = json.dumps(call.arguments, default=str)
                    lines.append(f"Assistant called tool {call.name}({payload})")
            elif message.role == "tool":
                lines.append(f"Tool result ({message.name}): {message.content}")
    return "\n".join(lines)


def compaction_request_messages(turns: list[list[Message]]) -> list[Message]:
    """Build the (single-message) request that asks the active provider to
    summarize ``turns`` (whole turns about to be dropped, oldest first —
    see ``plan_trim``) — a bare ``role="user"`` request with
    ``COMPACTION_PROMPT`` plus the rendered transcript, no tools, no system
    prompt. Sent through the ordinary ``provider.complete()``, so no new
    provider API is needed."""
    transcript = _render_turns_for_summary(turns)
    return [Message(role="user", content=f"{COMPACTION_PROMPT}\n\n---\n\n{transcript}")]


def compaction_summary_message(summary_text: str) -> Message:
    """Wrap a model-produced summary as the ``role="user"`` message that
    replaces the turns it summarizes. User-role (not a synthetic assistant
    message) is the safe choice across both API dialects — an assistant
    message with no matching tool-call history risks confusing tool-call
    pairing on the next request."""
    return Message(
        role="user", content=f"# Summary of earlier conversation (compacted)\n\n{summary_text}"
    )


__all__ = [
    "COMPACTION_PROMPT",
    "CONTEXT_SAFETY_FRACTION",
    "DEFAULT_RESERVED_OUTPUT_TOKENS",
    "DENSE_CHARS_PER_TOKEN",
    "IMAGE_TOKEN_ESTIMATE",
    "MIN_HISTORY_BUDGET",
    "MISSING_TOOL_RESULT",
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
