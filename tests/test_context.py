from __future__ import annotations

from pathlib import Path

from aida.core.context import build_system_message, estimate_tokens, load_skill_texts, trim_history
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
