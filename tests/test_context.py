from __future__ import annotations

from pathlib import Path

from aida.core.context import (
    CONTEXT_SAFETY_FRACTION,
    DEFAULT_RESERVED_OUTPUT_TOKENS,
    IMAGE_TOKEN_ESTIMATE,
    MIN_HISTORY_BUDGET,
    MISSING_TOOL_RESULT,
    build_coding_context_block,
    build_identity_context_block,
    build_system_message,
    build_workspace_context_block,
    compaction_request_messages,
    compaction_summary_message,
    estimate_message_tokens,
    estimate_tokens,
    estimate_tokens_dense,
    estimate_tool_schema_tokens,
    history_budget,
    load_skill_texts,
    plan_trim,
    repair_tool_call_pairing,
    split_into_turns,
    trim_history,
)
from aida.providers.base import ImageRef, Message, ToolCall, ToolSchema


def test_estimate_tokens_roughly_four_chars_per_token():
    assert estimate_tokens("") == 1  # never zero
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10


def test_build_system_message_combines_prompt_and_skills():
    msg = build_system_message("Be concise.", ["skill one text", "skill two text"])
    assert msg.role == "system"
    assert "Be concise." in msg.content
    assert "skill one text" in msg.content
    assert "skill two text" in msg.content


def test_build_system_message_empty_when_nothing_given():
    msg = build_system_message(None, [])
    assert msg.content == ""


def test_build_system_message_extra_texts_sit_between_prompt_and_skills():
    msg = build_system_message("Be concise.", ["skill text"], extra_texts=["folder facts"])
    order = [msg.content.index(p) for p in ("Be concise.", "folder facts", "skill text")]
    assert order == sorted(order), "extra_texts must land between system_prompt and skill_texts"


# --- identity_text (B15 — "should we inject name Aida ... so agent can be
# easier addressed") ordering: ahead of even the workspace's own
# system_prompt, since a domain persona reads oddly before the model knows
# its own name. --------------------------------------------------------


def test_build_system_message_identity_text_comes_before_everything_else():
    msg = build_system_message(
        "Be concise.",
        ["skill text"],
        extra_texts=["folder facts"],
        identity_text="Your name is Aida.",
    )
    order = [msg.content.index(p) for p in ("Your name is Aida.", "Be concise.", "folder facts", "skill text")]
    assert order == sorted(order), "identity_text must come before system_prompt/extra_texts/skill_texts"


def test_build_system_message_identity_text_optional():
    msg = build_system_message("Be concise.", [])
    assert "Aida" not in msg.content


# --- build_identity_context_block (B15) -------------------------------


def test_identity_context_block_names_the_assistant():
    block = build_identity_context_block(assistant_name="Aida", user_context="")
    assert block is not None
    assert "Aida" in block


def test_identity_context_block_includes_user_context_when_set():
    block = build_identity_context_block(assistant_name="Aida", user_context="Jan, beamline scientist at APS.")
    assert block is not None
    assert "Jan, beamline scientist at APS." in block


def test_identity_context_block_none_when_both_blank():
    assert build_identity_context_block(assistant_name="", user_context="") is None


def test_identity_context_block_user_context_alone_still_produces_a_block():
    # Defensive case (AppConfig.assistant_name should never actually be
    # blank in practice, see from_dict) — user_context alone is still worth
    # saying something for.
    block = build_identity_context_block(assistant_name="", user_context="Jan, beamline scientist at APS.")
    assert block is not None
    assert "Jan, beamline scientist at APS." in block


# --- build_workspace_context_block (regression: "agent seems to have no
# understanding of Source and Target folders") -----------------------------


def test_workspace_context_block_none_when_nothing_configured():
    assert (
        build_workspace_context_block(
            source_folders=[], target_folder=None, global_allowed_folders=[], sidecar_dirname="figures",
            safety_mode="confirm",
        )
        is None
    )


def test_workspace_context_block_lists_source_and_target_folders():
    block = build_workspace_context_block(
        source_folders=["/data/USAXS_2026"],
        target_folder="/Users/me/out",
        global_allowed_folders=[],
        sidecar_dirname="figures",
        safety_mode="relaxed",
    )
    assert block is not None
    assert "/data/USAXS_2026" in block
    assert "/Users/me/out" in block
    assert "figures" in block
    assert "relaxed" in block.lower()


def test_workspace_context_block_confirm_mode_wording_differs_from_relaxed():
    relaxed = build_workspace_context_block(
        source_folders=["/x"], target_folder=None, global_allowed_folders=[], sidecar_dirname="figures",
        safety_mode="relaxed",
    )
    confirm = build_workspace_context_block(
        source_folders=["/x"], target_folder=None, global_allowed_folders=[], sidecar_dirname="figures",
        safety_mode="confirm",
    )
    assert "without asking" in relaxed
    assert "will be asked to approve" in confirm


def test_workspace_context_block_mentions_global_allowed_folders_even_with_no_workspace():
    block = build_workspace_context_block(
        source_folders=[], target_folder=None, global_allowed_folders=["/shared/reference"],
        sidecar_dirname="figures", safety_mode="confirm",
    )
    assert block is not None
    assert "/shared/reference" in block


# --- scratch_dir (bug report: "Agents seem to be saving temporary files
# ... in random places") ----------------------------------------------------


def test_workspace_context_block_none_stays_none_without_scratch_dir():
    assert (
        build_workspace_context_block(
            source_folders=[], target_folder=None, global_allowed_folders=[], sidecar_dirname="figures",
            safety_mode="confirm", scratch_dir=None,
        )
        is None
    )


def test_workspace_context_block_mentions_scratch_dir_even_with_no_workspace():
    block = build_workspace_context_block(
        source_folders=[], target_folder=None, global_allowed_folders=[], sidecar_dirname="figures",
        safety_mode="confirm", scratch_dir="/Users/me/.aida/tmp",
    )
    assert block is not None
    assert "/Users/me/.aida/tmp" in block
    assert "Scratch folder" in block


def test_workspace_context_block_scratch_dir_explains_mcp_relative_paths():
    # Bug report: the model successfully called an MCP tool (Playwright
    # screenshot) with a relative filename, then couldn't find the resulting
    # file — it guessed the wrong absolute path and resorted to `find`.
    # Every MCP server's cwd is the scratch dir (aida.mcp.server), so a
    # relative path a tool resolves on its own lands there too; the model
    # needs to be told that explicitly rather than discovering it via search.
    block = build_workspace_context_block(
        source_folders=[], target_folder=None, global_allowed_folders=[], sidecar_dirname="figures",
        safety_mode="confirm", scratch_dir="/Users/me/.aida/tmp",
    )
    assert block is not None
    assert "MCP tool" in block
    assert "working directory" in block
    # The example resolution shown to the model uses the real configured dir.
    assert "/Users/me/.aida/tmp/shot.png" in block


# --- build_coding_context_block (regression: the model resorted to a raw
# `python3 -c "..."` probe via run_command, needing confirmation, just to
# discover its interpreter/packages) ----------------------------------------


def test_coding_context_block_none_when_scripting_disabled():
    assert (
        build_coding_context_block(python_interpreter=None, command_allowlist=[], scripting_enabled=False) is None
    )


def test_coding_context_block_mentions_configured_interpreter():
    block = build_coding_context_block(
        python_interpreter="/opt/miniconda3/envs/aievaluator/bin/python",
        command_allowlist=[],
        scripting_enabled=True,
    )
    assert block is not None
    assert "/opt/miniconda3/envs/aievaluator/bin/python" in block


def test_coding_context_block_mentions_default_interpreter_when_unset():
    block = build_coding_context_block(python_interpreter=None, command_allowlist=[], scripting_enabled=True)
    assert "AIDA itself is running under" in block


def test_coding_context_block_lists_allowlisted_commands():
    block = build_coding_context_block(
        python_interpreter=None, command_allowlist=["git status", "git log *"], scripting_enabled=True
    )
    assert "git status" in block
    assert "git log *" in block


def test_coding_context_block_notes_empty_allowlist():
    block = build_coding_context_block(python_interpreter=None, command_allowlist=[], scripting_enabled=True)
    assert "no allowlisted commands" in block.lower()


def test_coding_context_block_recommends_run_python_script_over_run_command():
    block = build_coding_context_block(python_interpreter=None, command_allowlist=[], scripting_enabled=True)
    assert "run_python_script" in block
    assert "run_command" in block


def test_load_skill_texts_flat_file(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "saxs-basics.md").write_text("SAXS basics content", encoding="utf-8")

    texts = load_skill_texts(skills_dir, ["saxs-basics"])
    assert len(texts) == 1
    assert "SAXS basics content" in texts[0]
    assert "saxs-basics" in texts[0]


def test_load_skill_texts_folder_with_skill_md(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    (skills_dir / "pyirena-usage").mkdir(parents=True)
    (skills_dir / "pyirena-usage" / "SKILL.md").write_text("pyIrena usage notes", encoding="utf-8")

    texts = load_skill_texts(skills_dir, ["pyirena-usage"])
    assert len(texts) == 1
    assert "pyIrena usage notes" in texts[0]


def test_load_skill_texts_missing_skill_skipped_not_error(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    texts = load_skill_texts(skills_dir, ["does-not-exist"])
    assert texts == []


def test_trim_history_keeps_system_and_recent_turns():
    system = Message(role="system", content="sys")
    old_turns = [Message(role="user", content="x" * 400) for _ in range(20)]
    recent = [Message(role="user", content="recent") for _ in range(4)]
    messages = [system, *old_turns, *recent]

    trimmed, was_trimmed = trim_history(messages, max_tokens=50, min_recent_turns=4)

    assert was_trimmed is True
    assert trimmed[0] is system
    # At least the minimum recent turns survive.
    assert trimmed[-4:] == recent


def test_trim_history_noop_when_under_budget():
    messages = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    trimmed, was_trimmed = trim_history(messages, max_tokens=10_000)
    assert was_trimmed is False
    assert trimmed == messages


# --- tool-call pairing repair + turn-safe trimming -------------------------
#
# Review finding: nothing ever called trim_history, so context grew without
# bound until a provider rejected a request for length mid-analysis — and
# wiring it in as it was written would have introduced the *other* finding's
# failure mode, since dropping the oldest messages one at a time can cut
# between an assistant message and the tool results answering its calls.


def _assistant_with_calls(*call_ids: str) -> Message:
    return Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id=cid, name="track", arguments={}) for cid in call_ids],
    )


def test_split_into_turns_keeps_tool_results_with_their_call():
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="q1"),
        _assistant_with_calls("c1"),
        Message(role="tool", content="r1", tool_call_id="c1", name="track"),
        Message(role="assistant", content="a1"),
        Message(role="user", content="q2"),
        Message(role="assistant", content="a2"),
    ]

    turns = split_into_turns(messages)

    assert [len(t) for t in turns] == [4, 2]
    assert [m.content for m in turns[0]] == ["q1", "", "r1", "a1"]


def test_trim_history_never_orphans_a_tool_result():
    system = Message(role="system", content="sys")
    messages = [system]
    for i in range(12):
        messages.extend(
            [
                Message(role="user", content=f"question {i} " + "x" * 400),
                _assistant_with_calls(f"c{i}"),
                Message(role="tool", content="result " + "y" * 400, tool_call_id=f"c{i}", name="track"),
                Message(role="assistant", content="answer " + "z" * 400),
            ]
        )

    trimmed, was_trimmed = trim_history(messages, max_tokens=600, min_recent_turns=2)

    assert was_trimmed is True
    announced = {tc.id for m in trimmed if m.role == "assistant" for tc in m.tool_calls}
    answered = {m.tool_call_id for m in trimmed if m.role == "tool"}
    assert announced == answered
    # Whole turns, so a kept turn always starts at its user message.
    assert trimmed[1].role == "user"


def test_trim_history_keeps_at_least_min_recent_turns():
    system = Message(role="system", content="sys")
    turns = [Message(role="user", content="x" * 4000) for _ in range(10)]
    trimmed, was_trimmed = trim_history([system, *turns], max_tokens=1, min_recent_turns=3)
    assert was_trimmed is True
    assert len(trimmed) == 1 + 3


def test_repair_backfills_a_missing_tool_result():
    messages = [
        Message(role="user", content="hi"),
        _assistant_with_calls("c1", "c2"),
        Message(role="tool", content="done", tool_call_id="c1", name="track"),
    ]

    repaired = repair_tool_call_pairing(messages)

    assert [m.role for m in repaired] == ["user", "assistant", "tool", "tool"]
    assert [m.tool_call_id for m in repaired if m.role == "tool"] == ["c1", "c2"]
    assert repaired[-1].content == MISSING_TOOL_RESULT
    assert messages[-1].tool_call_id == "c1"  # input untouched


def test_repair_drops_a_tool_result_answering_no_call():
    messages = [
        Message(role="user", content="hi"),
        Message(role="tool", content="stray", tool_call_id="ghost", name="track"),
        Message(role="assistant", content="hello"),
    ]

    repaired = repair_tool_call_pairing(messages)

    assert [m.role for m in repaired] == ["user", "assistant"]


def test_repair_reorders_results_to_match_the_announced_calls():
    messages = [
        _assistant_with_calls("c1", "c2"),
        Message(role="tool", content="second", tool_call_id="c2", name="track"),
        Message(role="tool", content="first", tool_call_id="c1", name="track"),
    ]

    repaired = repair_tool_call_pairing(messages)

    assert [m.tool_call_id for m in repaired if m.role == "tool"] == ["c1", "c2"]


def test_repair_leaves_a_healthy_history_alone():
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        _assistant_with_calls("c1"),
        Message(role="tool", content="done", tool_call_id="c1", name="track"),
        Message(role="assistant", content="all set"),
    ]

    assert repair_tool_call_pairing(messages) == messages


# --- PLAN.md §1.3 / planning/context_management.md: counting what is
# actually sent (tool schemas, dense tool/JSON content, images) -------------


def test_estimate_tokens_dense_uses_three_chars_per_token():
    assert estimate_tokens_dense("") == 1  # never zero
    assert estimate_tokens_dense("a" * 30) == 10


def test_estimate_tool_schema_tokens_empty_list_costs_nothing():
    assert estimate_tool_schema_tokens([]) == 0


def test_estimate_tool_schema_tokens_scales_with_schema_size():
    """Measured basis (context_management.md §2a): pyirena-mcp's 68 tools
    cost ~10,200 tokens, invisible to the budget before this existed. A
    small schema list should cost noticeably less than a large one, and a
    schema with a bigger `parameters` object should cost more than a
    trivial one."""
    small = [ToolSchema(name="get_time", description="Get the time.")]
    big = [
        ToolSchema(
            name=f"tool_{i}",
            description="A tool with a fairly detailed description of what it does.",
            parameters={
                "type": "object",
                "properties": {f"field_{j}": {"type": "string", "description": "x" * 40} for j in range(6)},
                "required": [],
            },
        )
        for i in range(20)
    ]
    assert estimate_tool_schema_tokens(big) > estimate_tool_schema_tokens(small) * 10


def test_estimate_message_tokens_tool_result_uses_dense_estimator():
    """A role="tool" message's content is dense JSON/numeric data (§2b), not
    prose — it must cost more than the plain (4-chars-per-token) estimate
    of the same text, since the dense estimator (3 chars/token) is what
    estimate_message_tokens now applies to it."""
    text = "x" * 300
    tool_message = Message(role="tool", content=text, tool_call_id="c1", name="track")
    assert estimate_message_tokens(tool_message) == estimate_tokens_dense(text)
    assert estimate_message_tokens(tool_message) > estimate_tokens(text)


def test_estimate_message_tokens_plain_user_message_unaffected():
    # Regular prose stays on the plain (4-chars-per-token) estimator — only
    # tool-role content and tool-call arguments moved to the dense one.
    text = "just a normal question about the data"
    assert estimate_message_tokens(Message(role="user", content=text)) == estimate_tokens(text)


def test_estimate_message_tokens_counts_images():
    """B1 images were never counted before this — a vision-heavy turn could
    look cheap to the trim budget while actually costing real tokens."""
    plain = Message(role="tool", content="a plot was generated", tool_call_id="c1", name="plot")
    with_one_image = Message(
        role="tool", content="a plot was generated", tool_call_id="c1", name="plot",
        images=[ImageRef(path="/tmp/plot.png")],
    )
    with_two_images = Message(
        role="tool", content="a plot was generated", tool_call_id="c1", name="plot",
        images=[ImageRef(path="/tmp/plot1.png"), ImageRef(path="/tmp/plot2.png")],
    )
    assert estimate_message_tokens(with_one_image) == estimate_message_tokens(plain) + IMAGE_TOKEN_ESTIMATE
    assert estimate_message_tokens(with_two_images) == estimate_message_tokens(plain) + 2 * IMAGE_TOKEN_ESTIMATE


# --- history_budget (§3.2) --------------------------------------------------


def test_history_budget_basic_arithmetic():
    budget = history_budget(context_window=100_000, reserved_output_tokens=4096, tool_schema_tokens=10_000)
    expected = int(100_000 * CONTEXT_SAFETY_FRACTION) - 4096 - 10_000
    assert budget == expected


def test_history_budget_uses_the_default_safety_fraction():
    assert history_budget(context_window=200_000, reserved_output_tokens=0, tool_schema_tokens=0) == int(
        200_000 * CONTEXT_SAFETY_FRACTION
    )


def test_history_budget_clamps_to_the_floor_when_over_committed():
    """A 128k-class local model with pyirena-mcp's ~10k of tool schemas and
    a generous max_tokens reservation can compute to a negative or tiny
    budget — a misconfiguration, not an honest tight budget, so it clamps
    to MIN_HISTORY_BUDGET rather than leaving the next turn unanswerable."""
    budget = history_budget(context_window=20_000, reserved_output_tokens=8000, tool_schema_tokens=10_000)
    assert budget == MIN_HISTORY_BUDGET


def test_history_budget_respects_default_reserved_output_tokens_constant():
    assert DEFAULT_RESERVED_OUTPUT_TOKENS == 4096  # Anthropic's own default


# --- plan_trim / TrimPlan (§3.4 — the shared decision compaction and plain
# trimming both build on) ----------------------------------------------------


def test_plan_trim_reports_dropped_turns_as_whole_turns():
    old_turns = []
    for i in range(10):
        old_turns.append(Message(role="user", content=f"q{i} " + "x" * 400))
        old_turns.append(Message(role="assistant", content=f"a{i} " + "y" * 400))
    messages = old_turns + [Message(role="user", content="recent")]

    plan = plan_trim(messages, max_tokens=50, min_recent_turns=1)

    assert plan.was_trimmed is True
    assert len(plan.dropped_turns) > 0
    # Every dropped turn is a whole turn (starts with the user message).
    for turn in plan.dropped_turns:
        assert turn[0].role == "user"
    # trim_history's own plain-discard result matches kept_messages exactly.
    trimmed, was_trimmed = trim_history(messages, max_tokens=50, min_recent_turns=1)
    assert (trimmed, was_trimmed) == (plan.kept_messages, plan.was_trimmed)


def test_plan_trim_noop_reports_no_dropped_turns():
    messages = [Message(role="user", content="hi")]
    plan = plan_trim(messages, max_tokens=10_000)
    assert plan.was_trimmed is False
    assert plan.dropped_turns == []
    assert plan.kept_messages == messages


# --- compaction (§3.4) ------------------------------------------------------


def test_compaction_request_messages_is_one_user_message_with_the_transcript():
    turns = [[Message(role="user", content="please plot the data")]]
    request = compaction_request_messages(turns)
    assert len(request) == 1
    assert request[0].role == "user"
    assert "please plot the data" in request[0].content


def test_compaction_request_messages_includes_tool_calls_and_results():
    turns = [
        [
            Message(role="user", content="fit the Guinier region"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="fit_guinier", arguments={"file": "run_042.dat"})],
            ),
            Message(role="tool", content="Rg=32.4, I0=1050", tool_call_id="c1", name="fit_guinier"),
            Message(role="assistant", content="Rg is 32.4 Angstrom."),
        ]
    ]
    request = compaction_request_messages(turns)
    text = request[0].content
    assert "run_042.dat" in text
    assert "Rg=32.4, I0=1050" in text
    assert "Rg is 32.4 Angstrom." in text


def test_compaction_summary_message_is_a_labeled_user_message():
    message = compaction_summary_message("- fit run_042.dat: Rg=32.4")
    assert message.role == "user"
    assert "Summary of earlier conversation" in message.content
    assert "Rg=32.4" in message.content
