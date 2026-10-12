"""Tests for aida.ui.qt.skills_panel."""

from __future__ import annotations

from aida.ui.qt.skills_panel import SkillData, SkillsPanel


def test_set_skills_ticks_the_active_ones(qapp):
    panel = SkillsPanel()
    panel.set_skills([SkillData("review"), SkillData("summarize")], ["summarize", "missing"])
    assert list(panel._checkboxes) == ["review", "summarize"]
    assert panel.checked_skills() == ["summarize"]


def test_set_skills_does_not_emit(qapp):
    panel = SkillsPanel()
    emitted: list[list[str]] = []
    panel.skills_changed.connect(emitted.append)
    panel.set_skills([SkillData("review")], ["review"])
    assert emitted == []


def test_ticking_a_skill_emits_the_checked_set(qapp):
    panel = SkillsPanel()
    panel.set_skills([SkillData("review"), SkillData("summarize")], ["summarize"])
    emitted: list[list[str]] = []
    panel.skills_changed.connect(emitted.append)

    panel._checkboxes["review"].setChecked(True)

    assert emitted == [["review", "summarize"]]


def test_tooltip_and_unsaved_marker(qapp):
    panel = SkillsPanel()
    panel.set_skills([SkillData("review", tooltip="Referee report")], [])
    assert panel._checkboxes["review"].toolTip() == "Referee report"
    panel.set_unsaved(True)
    assert panel._save_button.text() == "Save to Workspace *"
    panel.set_unsaved(False)
    assert panel._save_button.text() == "Save to Workspace"


def test_skills_are_laid_out_in_two_columns_top_to_bottom(qapp):
    panel = SkillsPanel()
    names = ["a", "b", "c", "d", "e"]
    panel.set_skills([SkillData(n) for n in names], [])
    positions = {}
    for n in names:
        row, column, _, _ = panel._grid.getItemPosition(panel._grid.indexOf(panel._checkboxes[n]))
        positions[n] = (row, column)
    assert positions == {"a": (0, 0), "b": (1, 0), "c": (2, 0), "d": (0, 1), "e": (1, 1)}
