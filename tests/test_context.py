from __future__ import annotations

from pathlib import Path

from aida.core.context import (
    build_coding_context_block,
    build_system_message,
    build_workspace_context_block,
    estimate_tokens,
    load_skill_texts,
    trim_history,
)
from aida.providers.base import Message


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
