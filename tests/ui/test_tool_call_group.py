"""Tests for aida.ui.qt.tool_call_group.ToolCallGroup — the collapsible
run-of-tool-calls header added for the bug report that a turn with dozens
of calls pushes the rest of the conversation off-screen (see that
module's docstring).

Assertions go through ``header_text()``/``mode``/``is_expanded`` rather
than Qt's realized geometry: a group is built here without ever being
shown, so ``isVisible()`` is always False and only visibility relative to
the immediate parent (``isVisibleTo``) says anything.
"""

from __future__ import annotations

from aida.ui.qt.tool_call_group import (
    DEFAULT_TOOL_CALL_DISPLAY,
    TOOL_CALL_DISPLAY_MODES,
    ToolCallGroup,
)
from aida.ui.qt.tool_call_widget import ToolCallRow


def _finished_row(tool_name: str, *, is_error: bool) -> ToolCallRow:
    row = ToolCallRow(call_id=tool_name, tool_name=tool_name, arguments={})
    row.mark_finished(result="boom" if is_error else "ok", is_error=is_error)
    return row


def test_empty_group_reports_zero_calls(qapp):
    group = ToolCallGroup()
    assert group.row_count == 0
    assert group.header_text() == "0 tool calls"
    assert not group.is_expanded


def test_finished_rows_are_tallied_with_elapsed_total(qapp):
    group = ToolCallGroup()
    group.add_row(_finished_row("get_time", is_error=False))
    group.add_row(_finished_row("read_file", is_error=True))
    group.row_finished()

    text = group.header_text()
    assert text.startswith("2 tool calls · 1 ✓ 1 ✗ · ")
    assert text.endswith(" s")
    assert group.row_count == 2


def test_single_call_uses_the_singular_noun(qapp):
    group = ToolCallGroup()
    group.add_row(_finished_row("get_time", is_error=False))
    assert group.header_text().startswith("1 tool call · 1 ✓")


def test_an_unfinished_row_makes_the_header_report_the_running_tool(qapp):
    group = ToolCallGroup()
    group.add_row(_finished_row("get_time", is_error=False))
    group.add_row(ToolCallRow(call_id="c2", tool_name="pyirena_plot_iq", arguments={}))

    text = group.header_text()
    assert text.startswith("⏳ 2 tool calls · running pyirena_plot_iq")
    assert "✓" not in text  # tally waits until the run is done


def test_historic_rows_never_read_as_still_running(qapp):
    """Regression: a row rebuilt from persisted history has
    ``is_error is None`` exactly like an in-flight one, so without
    ``ToolCallRow.is_historic`` every resumed conversation's groups
    reported "⏳ … running …" forever."""
    group = ToolCallGroup()
    for name in ("get_time", "read_file"):
        row = ToolCallRow(call_id=name, tool_name=name, arguments={})
        row.mark_historic(result="whatever was persisted")
        group.add_row(row)

    text = group.header_text()
    assert "⏳" not in text
    # Neither ✓/✗ nor timing was persisted, so the count is all there is.
    assert text == "2 tool calls"


def test_hidden_mode_header_is_only_the_count(qapp):
    group = ToolCallGroup(mode="hidden")
    for name in ("a", "b", "c"):
        group.add_row(_finished_row(name, is_error=False))
    assert group.header_text() == "3 tool calls"


def test_hidden_mode_keeps_the_rows_so_inspection_stays_available(qapp):
    """The whole point of the modes: nothing is discarded, so flipping to
    "expanded" after something went wrong shows that turn in full."""
    group = ToolCallGroup(mode="hidden")
    group.add_row(_finished_row("read_file", is_error=True))
    assert group.row_count == 1

    group.set_mode("expanded")
    assert group.is_expanded
    assert group.rows[0].is_error is True
    assert "1 ✗" in group.header_text()


def test_mode_controls_expansion_and_body_visibility(qapp):
    group = ToolCallGroup()
    group.add_row(_finished_row("get_time", is_error=False))
    assert not group.is_expanded
    assert not group._body.isVisibleTo(group)

    group.set_mode("expanded")
    assert group.mode == "expanded"
    assert group.is_expanded
    assert group._body.isVisibleTo(group)

    group.set_mode("grouped")
    assert not group.is_expanded
    assert not group._body.isVisibleTo(group)


def test_a_mode_change_overrides_a_hand_toggled_group(qapp):
    group = ToolCallGroup()
    group.add_row(_finished_row("get_time", is_error=False))
    group._header.setChecked(True)
    group._on_header_clicked()
    assert group.is_expanded

    group.set_mode("grouped")
    assert not group.is_expanded


def test_an_unrecognized_mode_falls_back_to_the_default(qapp):
    group = ToolCallGroup(mode="wobble")
    assert group.mode == DEFAULT_TOOL_CALL_DISPLAY

    group.set_mode("grouped")
    group.set_mode("wobble")
    assert group.mode == DEFAULT_TOOL_CALL_DISPLAY
    assert DEFAULT_TOOL_CALL_DISPLAY in TOOL_CALL_DISPLAY_MODES


def test_a_very_long_tool_name_is_truncated_in_the_header(qapp):
    long_name = "x" * 200
    group = ToolCallGroup()
    group.add_row(ToolCallRow(call_id="c1", tool_name=long_name, arguments={}))

    text = group.header_text()
    assert long_name not in text
    assert "…" in text
    assert len(text) < 100


def test_the_config_default_matches_this_module(qapp):
    """``AppConfig.tool_call_display`` spells its default as a literal
    because config must stay importable with no Qt installed (see that
    field's comment). This is the check that keeps the two in step."""
    from aida.config.settings import AppConfig

    assert AppConfig().tool_call_display == DEFAULT_TOOL_CALL_DISPLAY
    assert AppConfig().tool_call_display in TOOL_CALL_DISPLAY_MODES
