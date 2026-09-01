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


def test_mark_historic_shows_a_neutral_marker_and_no_elapsed_time(qapp):
    """U6: a resumed tool row rebuilt from a persisted Message — is_error
    and duration were never recorded, so neither ✓/✗ nor an elapsed
    seconds figure can be shown."""
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={"tz": "utc"})
    row.mark_historic(result="the time is now")
    assert row.is_error is None
    text = row._summary_label.text()
    assert "•" in text
    assert "✓" not in text
    assert "✗" not in text
    assert "s)" not in text  # no elapsed-seconds suffix
    assert "the time is now" in row._detail_text.toPlainText()


def test_mark_historic_row_still_expands_to_show_details(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={"tz": "utc"})
    row.mark_historic(result="the time is now")
    row.toggle_expanded()
    assert row.is_expanded
    assert "the time is now" in row._detail_text.toPlainText()


def test_toggle_expanded_shows_and_hides_detail(qapp):
    row = ToolCallRow(call_id="c1", tool_name="get_time", arguments={"tz": "utc"})
    row.mark_finished(result="the time is now", is_error=False)
    # `isVisible()` is unreliable offscreen and `not X or True` could never
    # fail; assert the toggle state the widget actually drives instead.
    assert not row.is_expanded

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


# --- regression: long argument list must not force the window wider --------


def test_summary_label_wraps_instead_of_growing_the_window(qapp):
    """Bug report: plotting many datasets at once sent a `paths=[...]`
    argument long enough that the collapsed row (no word wrap) forced the
    whole app window to grow "to semi-infinite size". Word wrap shrinks a
    QLabel's minimum size hint down to its longest unbreakable token,
    rather than the full unwrapped line — this is what actually stops the
    width demand from propagating up to the main window."""
    row = ToolCallRow(
        call_id="c1",
        tool_name="pyirena-mcp__pyirena_plot_iq",
        arguments={"paths": [f"/data/sample_{i:03d}_reduced.h5" for i in range(40)]},
    )
    assert row._summary_label.wordWrap() is True
    # A word-wrapped label's minimum width is bounded by its longest single
    # token, not the full (here, ~1400-character) unwrapped line.
    assert row._summary_label.minimumSizeHint().width() < 1000


def test_summary_line_is_truncated_but_details_stay_full(qapp):
    huge_arguments = {"paths": [f"/data/sample_{i:03d}_reduced.h5" for i in range(200)]}
    row = ToolCallRow(call_id="c1", tool_name="pyirena_plot_iq", arguments=huge_arguments)

    assert len(row._summary_label.text()) < 500
    assert "…" in row._summary_label.text()

    row.mark_finished(result="ok", is_error=False)
    # The full, untruncated arguments are still available once expanded —
    # truncation is a display-only concern for the collapsed summary.
    assert "sample_199_reduced.h5" in row._detail_text.toPlainText()


def test_folder_display_target_label_wraps(qapp):
    from aida.ui.qt.selectors import FolderDisplay

    display = FolderDisplay()
    long_path = "/Users/ilavsky/Experiments/USAXS_data/2026/2026-06/06_24_Anovitz/Alteration/Alteration_usaxs/"
    display.set_folders(source_folders=[long_path], target_folder=long_path)

    assert display._target_label.wordWrap() is True
    row = display._source_rows_layout.itemAt(0).widget()
    label = row.findChild(type(display._target_label))
    assert label.wordWrap() is True
