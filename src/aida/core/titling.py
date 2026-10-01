"""Naming a conversation from what is actually in it.

A conversation's title used to be the first line of the first thing the
user typed, truncated (``aida.persistence.recorder._derive_title``) — which
is "can you look at the file I just put in the target folder and" far more
often than it is a name. That placeholder is still what appears the instant
a conversation starts, because it costs nothing and is better than a blank;
this module is what replaces it once there is a reply to read.

Built on exactly the machinery context compaction already uses
(``aida.core.context.compaction_request_messages`` ->
``ChatSession._compact_context``): a plain ``role="user"`` request, sent
through the *active* provider with no tools, and a caller that treats every
possible failure as "no title this time" rather than an error. No new
provider API, no second "utility" profile to configure, and nothing here
can fail a turn.

Re-titling is the same call with the current title fed back in. The model
is asked to answer with ``KEEP`` when that title still fits, so an ongoing
conversation only gets renamed when its subject has genuinely moved on —
no embeddings, no similarity threshold, and the judgement is made by the
model that just read the conversation.
"""

from __future__ import annotations

import re

from aida.providers.base import Message

#: Same ceiling as ``aida.persistence.recorder._TITLE_MAX_CHARS``, so a
#: generated title and the placeholder it replaces occupy the same space in
#: the sidebar and the chat header. Kept as its own constant rather than
#: imported: core must not depend on persistence (PLAN.md §3 layering), and
#: the two are a UI width decision that happens to agree, not one fact.
TITLE_MAX_CHARS = 60

#: What the model answers instead of a title when the existing one still
#: describes the conversation. Compared case-insensitively after cleaning,
#: and never itself accepted as a title.
KEEP_SENTINEL = "KEEP"

#: How much of the conversation the titling request carries. Generous
#: enough to cover a real exchange, small enough that this stays a cheap
#: call on a local model — it runs once per conversation plus once every
#: few turns after that.
DEFAULT_EXCERPT_CHARS = 4000

#: Per-message truncation inside that excerpt, so one pasted log or one
#: very long reply cannot crowd out every other message.
_PER_MESSAGE_CHARS = 600

#: How many of the most recent messages are shown, on top of the opening
#: user message (which is always included — it is usually the clearest
#: statement of what the whole conversation is for).
_RECENT_MESSAGES = 8

_NEW_TITLE_PROMPT = (
    "Below is an excerpt from a conversation between a scientist and an AI assistant. "
    "Give the conversation a short title, for a list of past conversations.\n\n"
    "Rules:\n"
    "- A noun phrase of at most 8 words. Not a sentence, no final period.\n"
    "- Name the specific subject: the sample, instrument, file, technique or task. "
    "Keep names, labels and identifiers exactly as they are written "
    "(for example AgBehenate, Unified fit, S12_0042.h5, 9-ID) — never generalize "
    "them away or translate them into plainer words.\n"
    "- Do not start with 'Conversation about', 'Discussion of', 'Help with' or similar.\n"
    "- Do not use quotation marks, markdown, or a 'Title:' prefix.\n"
    "- Answer with the title and nothing else."
)

_RETITLE_PROMPT_TEMPLATE = (
    "Below is an excerpt from a conversation between a scientist and an AI assistant. "
    "Its current title is:\n\n"
    "{current_title}\n\n"
    "Decide whether that title still describes what the conversation is about.\n\n"
    "- If it does, answer with exactly {sentinel} and nothing else. Prefer this: a title "
    "that is still broadly right should not be churned for a slightly better wording.\n"
    "- Only if the subject has clearly moved on to something else, answer with a new title "
    "instead.\n\n"
    "A new title must follow these rules:\n"
    "- A noun phrase of at most 8 words. Not a sentence, no final period.\n"
    "- Name the specific subject: the sample, instrument, file, technique or task. "
    "Keep names, labels and identifiers exactly as they are written "
    "(for example AgBehenate, Unified fit, S12_0042.h5, 9-ID) — never generalize "
    "them away or translate them into plainer words.\n"
    "- Do not start with 'Conversation about', 'Discussion of', 'Help with' or similar.\n"
    "- Do not use quotation marks, markdown, or a 'Title:' prefix.\n"
    "- Answer with the title and nothing else."
)

#: Leading noise a model adds despite being told not to.
_LEADING_LABEL_RE = re.compile(r"^(?:title|new title)\s*[:\-—]\s*", re.IGNORECASE)

#: Surrounding quotes/backticks/markdown emphasis, stripped in a loop so
#: ``**"Rg fits"**`` unwraps completely.
_WRAPPERS = ('"', "'", "`", "“", "”", "‘", "’", "**", "*", "__", "_")


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_messages_for_title(
    messages: list[Message], *, max_chars: int = DEFAULT_EXCERPT_CHARS
) -> str:
    """Flatten a conversation into the excerpt the titling request carries.

    Only ``user`` and ``assistant`` *text* is kept. Tool calls and tool
    results are skipped deliberately: they are by far the bulkiest part of
    a pyIrena/EPICS session and almost never what the conversation is
    *about* — a title drawn from them would name the plumbing ("read_file
    and plot_data") instead of the science.

    The opening user message is always included (it usually states the
    whole purpose), followed by the most recent few messages, so a long
    conversation is titled from both where it started and where it is now.
    """
    usable = [
        message
        for message in messages
        if message.role in ("user", "assistant") and (message.content or "").strip()
    ]
    if not usable:
        return ""
    recent = usable[-_RECENT_MESSAGES:]
    # Identity, not equality: two separate turns can legitimately carry the
    # same text, and only the actual first object should be skipped here.
    selected = recent if any(m is usable[0] for m in recent) else [usable[0], *recent]

    lines: list[str] = []
    for message in selected:
        speaker = "User" if message.role == "user" else "Assistant"
        lines.append(f"{speaker}: {_truncate(message.content, _PER_MESSAGE_CHARS)}")
    excerpt = "\n\n".join(lines)
    if len(excerpt) > max_chars:
        # Keep the *end*: the most recent exchange is what a re-title is
        # supposed to react to.
        excerpt = "…\n\n" + excerpt[-max_chars:]
    return excerpt


def title_request_messages(
    messages: list[Message],
    *,
    current_title: str | None = None,
    max_chars: int = DEFAULT_EXCERPT_CHARS,
) -> list[Message]:
    """The single-message request handed to ``provider.complete()``.

    Same shape as ``aida.core.context.compaction_request_messages``: one
    bare ``role="user"`` message, no system prompt, no tools — so it works
    identically on every provider AIDA talks to, including a small local
    model that has no system-prompt handling worth relying on.

    With ``current_title`` set, this becomes a *re-title* request and the
    model is allowed to answer ``KEEP``; without it, a title is always
    expected.
    """
    excerpt = render_messages_for_title(messages, max_chars=max_chars)
    if current_title:
        instruction = _RETITLE_PROMPT_TEMPLATE.format(
            current_title=current_title, sentinel=KEEP_SENTINEL
        )
    else:
        instruction = _NEW_TITLE_PROMPT
    return [Message(role="user", content=f"{instruction}\n\n---\n\n{excerpt}")]


def _strip_wrappers(text: str) -> str:
    changed = True
    while changed and text:
        changed = False
        for wrapper in _WRAPPERS:
            # `>=`, so an answer that is *only* a pair of wrappers ('""')
            # strips down to nothing and is rejected, rather than being
            # kept verbatim as a title. The guard is still what stops a
            # lone `"` matching both startswith and endswith on itself.
            if (
                len(text) >= 2 * len(wrapper)
                and text.startswith(wrapper)
                and text.endswith(wrapper)
            ):
                text = text[len(wrapper) : -len(wrapper)].strip()
                changed = True
    return text


def clean_title(raw: str, *, max_chars: int = TITLE_MAX_CHARS) -> str | None:
    """Turn a model's answer into something fit for a sidebar row.

    Returns ``None`` for anything that is not a usable title — an empty
    reply, the ``KEEP`` sentinel, or a reply that cleans down to nothing.
    Callers treat ``None`` as "leave the existing title alone", which is
    also the correct outcome for ``KEEP``, so neither case needs special
    handling upstream.

    Models ignore formatting instructions often enough that this does the
    tidying rather than trusting them: first non-empty line only (a chatty
    model adds a second paragraph explaining its choice), surrounding
    quotes/backticks/emphasis removed, a ``Title:`` prefix dropped, and no
    trailing period.
    """
    if not raw:
        return None
    first_line = next((line for line in raw.strip().splitlines() if line.strip()), "")
    title = _LEADING_LABEL_RE.sub("", first_line.strip()).strip()
    title = _strip_wrappers(title)
    title = title.rstrip(" .").strip()
    # Re-stripped after the trailing period: '"Rg fits."' leaves a quote
    # behind that the first pass could not see past.
    title = _strip_wrappers(title)
    if not title:
        return None
    if title.strip().upper() == KEEP_SENTINEL:
        return None
    return _truncate(title, max_chars)


__all__ = [
    "DEFAULT_EXCERPT_CHARS",
    "KEEP_SENTINEL",
    "TITLE_MAX_CHARS",
    "clean_title",
    "render_messages_for_title",
    "title_request_messages",
]
