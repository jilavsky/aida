"""Tests for aida.ui.qt.collapsible.CollapsibleSection."""

from __future__ import annotations

from aida.ui.qt._qt import QGroupBox, QLabel
from aida.ui.qt.collapsible import CollapsibleSection


def test_header_click_collapses_and_expands(qapp):
    content = QLabel("body")
    section = CollapsibleSection("Quick Tasks", content)

    assert not section.is_collapsed
    assert content.isVisibleTo(section)

    section._header.click()
    assert section.is_collapsed
    assert not content.isVisibleTo(section)

    section._header.click()
    assert not section.is_collapsed
    assert content.isVisibleTo(section)


def test_toggling_reports_the_new_state(qapp):
    section = CollapsibleSection("Folders", QLabel("body"))
    toggles = []
    section.toggled.connect(lambda title, collapsed: toggles.append((title, collapsed)))

    section._header.click()
    section._header.click()

    assert toggles == [("Folders", True), ("Folders", False)]


def test_restoring_saved_state_does_not_emit(qapp):
    """set_collapsed() at construction restores what was persisted —
    emitting there would write every panel's state back on every launch."""
    section = CollapsibleSection("MCP Servers", QLabel("body"))
    toggles = []
    section.toggled.connect(lambda title, collapsed: toggles.append((title, collapsed)))

    section.set_collapsed(True)

    assert section.is_collapsed
    assert toggles == []


def test_set_collapsed_can_notify_when_asked(qapp):
    section = CollapsibleSection("MCP Servers", QLabel("body"))
    toggles = []
    section.toggled.connect(lambda title, collapsed: toggles.append((title, collapsed)))

    section.set_collapsed(True, notify=True)
    section.set_collapsed(True, notify=True)  # already collapsed: no-op

    assert toggles == [("MCP Servers", True)]


def test_a_wrapped_group_box_loses_its_own_title(qapp):
    """The section header already shows it; two stacked titles read as a
    bug."""
    box = QGroupBox("Quick Tasks")
    section = CollapsibleSection("Quick Tasks", box)

    assert box.title() == ""
    assert section.content is box
