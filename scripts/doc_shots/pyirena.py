"""Shots for docs/guides/pyirena.md.

01: the transcript of the seeded pyIrena conversation (the same one as the
window tour): a question, the collapsed tool calls, the I(Q) plot returned
inline and the answer. The plot is a tracked pyIrena docs image; no model
or pyIrena server runs.
"""

from __future__ import annotations

from ._common import Context, draw_callouts, shot, widget_rect
from ._package import (
    open_main_window,
    reset_docs_tmp,
    seed_conversation,
    standard_settings,
    today_at,
)


@shot("pyirena-01-inline-plot")
def inline_plot(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    conv_id = seed_conversation("conversation-unified-overlay.json", when=today_at(9, 3))
    w = open_main_window(ctx, settings, resume=conv_id, size=(1400, 1080))
    from aida.ui.qt.artifact_widgets import InlineImageWidget
    from aida.ui.qt.tool_call_group import ToolCallGroup

    panel = w.chat_panel
    image_widget = next(
        panel.widget_at(i)
        for i in range(panel.widget_count)
        if isinstance(panel.widget_at(i), InlineImageWidget)
    )
    group = next(
        panel.widget_at(i)
        for i in range(panel.widget_count)
        if isinstance(panel.widget_at(i), ToolCallGroup)
    )
    image = panel.grab()
    return draw_callouts(
        image,
        [
            (1, widget_rect(group, panel), "l", (180, 0)),
            (2, widget_rect(image_widget, panel), "l", (500, 0)),
        ],
    )
