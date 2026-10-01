"""Tests for aida.ui.qt.conversation_header — the conversation's name at
the top of the chat column."""

from __future__ import annotations

from aida.ui.qt.conversation_header import PLACEHOLDER_TITLE, ConversationHeader


def test_a_fresh_header_shows_the_placeholder_not_a_blank_strip(qapp):
    header = ConversationHeader()
    assert header.title_text() == PLACEHOLDER_TITLE


def test_set_conversation_shows_the_title_and_the_subtitle(qapp):
    header = ConversationHeader()
    header.set_conversation("Unified fit of S12_0042.h5", "usaxs-analysis · argo-claude")
    assert header.title_text() == "Unified fit of S12_0042.h5"
    assert header.subtitle_text() == "usaxs-analysis · argo-claude"


def test_a_missing_or_blank_title_falls_back_to_the_placeholder(qapp):
    header = ConversationHeader()
    for title in (None, "", "   "):
        header.set_conversation(title, "usaxs-analysis")
        assert header.title_text() == PLACEHOLDER_TITLE, repr(title)


def test_an_empty_subtitle_hides_that_line_rather_than_leaving_a_gap(qapp):
    header = ConversationHeader()
    header.set_conversation("Some chat", "")
    assert header._subtitle_label.isVisible() is False

    header.set_conversation("Some chat", "usaxs-analysis")
    header.show()
    qapp.processEvents()
    assert header._subtitle_label.isVisible() is True
    header.close()


def test_the_pencil_button_asks_for_a_rename(qapp):
    header = ConversationHeader()
    asked = []
    header.rename_requested.connect(lambda: asked.append(True))
    header._rename_button.click()
    assert asked == [True]


class _FakeDoubleClick:
    """Stands in for the ``QMouseEvent`` Qt would deliver.

    Constructing a real one means either the deprecated positional
    ``QMouseEvent`` overload or importing ``QtTest`` (which would have to
    be added to the production ``_qt`` shim for the sake of one test).
    The override under test only calls ``accept()``, so this is the whole
    surface it touches.
    """

    def __init__(self) -> None:
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True


def test_double_clicking_the_strip_asks_for_a_rename(qapp):
    """The gesture people try on a title before they go looking for a
    button."""
    header = ConversationHeader()
    asked = []
    header.rename_requested.connect(lambda: asked.append(True))

    event = _FakeDoubleClick()
    header.mouseDoubleClickEvent(event)

    assert asked == [True]
    # Accepted, so the double click does not also fall through to
    # whatever is underneath.
    assert event.accepted is True


def test_a_long_title_elides_instead_of_widening_the_chat_column(qapp):
    """The chat column sits in a user-draggable splitter: the header must
    never become the reason it cannot shrink."""
    header = ConversationHeader()
    long_title = "quantitative USAXS background subtraction of the aged AgBehenate standard"
    header.set_conversation(long_title, "usaxs-analysis · argo-claude · Jan")
    header.resize(180, 50)
    header.show()
    qapp.processEvents()

    try:
        assert header.minimumSizeHint().width() <= 180
        # What is *painted* is shortened; what the title really is stays
        # reachable through the accessor and the tooltip.
        assert header._title_label.text() != long_title
        assert header._title_label.text().endswith("…")
        assert header.title_text() == long_title
        assert header._title_label.toolTip() == long_title
    finally:
        header.close()
