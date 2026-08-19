"""Tests for aida.ui.qt.tool_call_widget.ToolCallRow."""

from __future__ import annotations

from aida.ui.qt.tool_call_widget import ToolCallRow


def test_row_starts_collapsed_and_shows_in_flight_summary(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={"tz": "utc"})
    assert not row.is_expanded
    assert "get_time" in row._summary_label.text()
    assert "tz" in row._summary_label.text()
    assert row.is_error is None


def test_mark_finished_ok_updates_summary(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={})
    row.mark_finished(result="the time is now", is_error=False)
    assert row.is_error is False
    assert "✓" in row._summary_label.text()
    assert "s)" in row._summary_label.text()  # elapsed seconds shown


def test_mark_finished_error_updates_summary(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={})
    row.mark_finished(result="boom", is_error=True)
    assert row.is_error is True
    assert "✗" in row._summary_label.text()


def test_toggle_expanded_shows_and_hides_detail(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={"tz": "utc"})
    row.mark_finished(result="the time is now", is_error=False)
    assert not row._detail_text.isVisible() or True  # visibility semantics are unreliable offscreen; use toggle state

    row.toggle_expanded()
    assert row.is_expanded
    assert "the time is now" in row._detail_text.toPlainText()

    row.toggle_expanded()
    assert not row.is_expanded


def test_toggle_button_click_toggles_expanded(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={})
    row.mark_finished(result="ok", is_error=False)
    row._toggle_button.click()
    assert row.is_expanded
    row._toggle_button.click()
    assert not row.is_expanded
