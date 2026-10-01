"""Tests for aida.core.titling — building the request that names a
conversation, and making the model's answer fit in a sidebar row."""

from __future__ import annotations

from aida.core.titling import (
    KEEP_SENTINEL,
    TITLE_MAX_CHARS,
    clean_title,
    render_messages_for_title,
    title_request_messages,
)
from aida.providers.base import Message, ToolCall


def _exchange(*pairs: tuple[str, str]) -> list[Message]:
    messages = []
    for role, content in pairs:
        messages.append(Message(role=role, content=content))
    return messages


# --- clean_title -----------------------------------------------------------


def test_clean_title_passes_a_well_behaved_answer_through():
    assert clean_title("Background subtraction for AgBehenate") == (
        "Background subtraction for AgBehenate"
    )


def test_clean_title_strips_the_wrappers_models_add_despite_being_told_not_to():
    for raw in (
        '"Unified fit of PS latex"',
        "'Unified fit of PS latex'",
        "`Unified fit of PS latex`",
        "**Unified fit of PS latex**",
        "Title: Unified fit of PS latex",
        "title: **Unified fit of PS latex**",
        "Unified fit of PS latex.",
        '"Unified fit of PS latex."',
    ):
        assert clean_title(raw) == "Unified fit of PS latex", raw


def test_clean_title_keeps_only_the_first_line():
    """A chatty model explains its choice underneath the answer."""
    reply = "Rg fits for the aged sample\n\nI chose this because the conversation is about…"
    assert clean_title(reply) == "Rg fits for the aged sample"


def test_clean_title_skips_leading_blank_lines():
    assert clean_title("\n\n  Motor scan setup  \n") == "Motor scan setup"


def test_clean_title_truncates_to_the_sidebar_width():
    title = clean_title("x" * 200)
    assert title is not None
    assert len(title) == TITLE_MAX_CHARS
    assert title.endswith("…")


def test_clean_title_rejects_nothing_usable():
    for raw in ("", "   ", "\n", '""', "."):
        assert clean_title(raw) is None, repr(raw)


def test_clean_title_rejects_the_keep_sentinel_in_any_casing_or_wrapping():
    for raw in (KEEP_SENTINEL, "keep", "  Keep  ", '"KEEP"', "KEEP."):
        assert clean_title(raw) is None, raw


def test_clean_title_does_not_mangle_identifiers_the_prompt_asks_it_to_preserve():
    """Filenames carry dots and underscores; the trailing-period and
    emphasis stripping must not eat into them."""
    assert clean_title("Unified fit of S12_0042.h5") == "Unified fit of S12_0042.h5"


# --- render_messages_for_title ---------------------------------------------


def test_render_includes_user_and_assistant_text():
    rendered = render_messages_for_title(
        _exchange(("user", "fit a Unified level"), ("assistant", "load the file first"))
    )
    assert "User: fit a Unified level" in rendered
    assert "Assistant: load the file first" in rendered


def test_render_skips_the_system_message_and_tool_traffic():
    """A title drawn from tool results would name the plumbing, not the
    science — see the function's docstring."""
    messages = [
        Message(role="system", content="You are a workspace assistant."),
        Message(role="user", content="fit a Unified level"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.h5"})],
        ),
        Message(role="tool", content="{'rows': 4096}", tool_call_id="c1", name="read_file"),
        Message(role="assistant", content="three levels fit well"),
    ]
    rendered = render_messages_for_title(messages)
    assert "workspace assistant" not in rendered
    assert "read_file" not in rendered
    assert "4096" not in rendered
    assert "three levels fit well" in rendered


def test_render_always_keeps_the_opening_message_however_long_the_conversation():
    """The first message usually states what the whole conversation is
    for, so it survives the recent-messages window."""
    messages = _exchange(("user", "analyze the AgBehenate calibration"))
    for index in range(30):
        messages.extend(_exchange(("user", f"q{index}"), ("assistant", f"a{index}")))
    rendered = render_messages_for_title(messages)
    assert "AgBehenate calibration" in rendered
    assert "a29" in rendered  # and the most recent exchange too


def test_render_truncates_one_enormous_message_rather_than_dropping_the_others():
    messages = _exchange(("user", "x" * 50_000), ("assistant", "the sample is annealed"))
    rendered = render_messages_for_title(messages)
    assert "the sample is annealed" in rendered
    assert len(rendered) < 10_000


def test_render_of_nothing_sayable_is_empty():
    assert render_messages_for_title([]) == ""
    assert render_messages_for_title(_exchange(("user", "   "))) == ""


# --- title_request_messages ------------------------------------------------


def test_request_is_one_plain_user_message_with_no_system_prompt():
    """Same shape as compaction_request_messages, for the same reason: it
    has to work identically on every provider AIDA talks to."""
    request = title_request_messages(_exchange(("user", "fit a Unified level")))
    assert len(request) == 1
    assert request[0].role == "user"
    assert request[0].tool_calls == []


def test_a_first_title_request_does_not_mention_keep():
    request = title_request_messages(_exchange(("user", "fit a Unified level")))
    assert KEEP_SENTINEL not in request[0].content


def test_a_retitle_request_carries_the_current_title_and_offers_keep():
    request = title_request_messages(
        _exchange(("user", "fit a Unified level")),
        current_title="Unified fit of PS latex",
    )
    assert "Unified fit of PS latex" in request[0].content
    assert KEEP_SENTINEL in request[0].content


def test_request_respects_the_excerpt_cap():
    messages = _exchange(("user", "x" * 5_000), ("assistant", "y" * 5_000))
    request = title_request_messages(messages, max_chars=500)
    # The instruction itself is fixed-size; only the excerpt is capped.
    assert len(request[0].content) < 3_000
