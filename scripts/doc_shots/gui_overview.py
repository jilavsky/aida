"""Shots for docs/getting-started/gui-overview.md (the window tour).

The window is AIDA's real MainWindow on a MockProvider, resuming a
conversation seeded into the scratch database: a question about Unified
fits, two pyIrena tool calls (``pyirena_list_files``, ``pyirena_plot_iq``),
the returned plot and the answer. The plot is a tracked pyIrena docs image
of three ``validationData/unified/`` files with their Unified models.
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


def _window_with_conversation(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    seed_conversation("conversation-short-3.json", when=today_at(10, 15, days_ago=40))
    seed_conversation("conversation-short-2.json", when=today_at(17, 44, days_ago=1))
    seed_conversation("conversation-short-1.json", when=today_at(8, 30))
    conv_id = seed_conversation("conversation-unified-overlay.json", when=today_at(9, 3))
    return open_main_window(ctx, settings, resume=conv_id)


@shot("gui-overview-01-window")
def window(ctx: Context):
    w = _window_with_conversation(ctx)
    menu = w.menuBar()
    last_menu = menu.actionGeometry(menu.actions()[-1])
    image = w.grab()
    marks = [
        (1, widget_rect(menu, w), "l", (last_menu.right() + 16, 0)),
        (2, widget_rect(w.user_selector, w), "r", (-10, 0)),
        (3, widget_rect(w.workspace_selector, w), "r", (-10, 0)),
        (4, widget_rect(w.profile_selector, w), "r", (-10, 0)),
        (5, widget_rect(w.code_editor_button, w), "r", (16, 0)),
        (6, widget_rect(w.documentation_button, w), "l", (-14, 0)),
        (7, widget_rect(w.sidebar, w), "tr", (-14, 14)),
        (8, widget_rect(w.conversation_header, w), "r", (-14, 0)),
        (9, widget_rect(w.chat_panel, w), "tr", (-34, 90)),
        (10, widget_rect(w.input_box, w), "r", (-60, 0)),
        (11, widget_rect(w._splitter.widget(2), w), "tr", (-24, 14)),
        (12, widget_rect(w.statusBar(), w), "l", (190, 0)),
    ]
    return draw_callouts(image, marks)
