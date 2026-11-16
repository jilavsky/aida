"""Shots for docs/guides/organizing-conversations.md.

The real main window on the standard scratch configuration, with five seeded
conversations under two user labels (``usaxs-team``, ``saxs-student``) and
one without a label. The sidebar filter is set to "All users".
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


def _window(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    seed_conversation("conversation-short-3.json", when=today_at(10, 15, days_ago=40))
    seed_conversation("conversation-short-4.json", when=today_at(14, 20, days_ago=1))
    seed_conversation("conversation-short-2.json", when=today_at(17, 44, days_ago=1))
    seed_conversation("conversation-short-1.json", when=today_at(8, 30))
    conv_id = seed_conversation("conversation-unified-overlay.json", when=today_at(9, 3))
    window = open_main_window(ctx, settings, resume=conv_id)
    window.sidebar._user_filter.setCurrentText("All users")
    ctx.settle()
    return window


@shot("organizing-conversations-01-sidebar")
def sidebar(ctx: Context):
    window = _window(ctx)  # keep a reference: the sidebar is owned by the window
    bar = window.sidebar
    image = bar.grab()
    return draw_callouts(
        image,
        [
            (1, widget_rect(bar._user_filter, bar), "r", (-30, 0)),
            (2, widget_rect(bar._search_edit, bar), "r", (-14, 0)),
        ],
    )


@shot("organizing-conversations-02-manage-users")
def manage_users(ctx: Context):
    from aida.persistence.store import ConversationStore
    from aida.ui.qt.users_dialog import UsersDialog

    _window(ctx)
    store = ConversationStore()
    dialog = UsersDialog(store, active_user="usaxs-team")
    dialog._list.setCurrentRow(1)  # usaxs-team: enables Rename or Merge… and Clear Label…
    ctx.show(dialog, (520, 300))
    image = dialog.grab()
    store.close()
    return image


@shot("organizing-conversations-03-move-to-user")
def move_to_user(ctx: Context):
    """The sidebar's right-click menu and its Move to User submenu, side by side."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPainter, QPixmap

    window = _window(ctx)
    bar = window.sidebar
    bar.select_row(0)
    menu = bar._build_context_menu()
    menu.popup(QPoint(0, 0))
    ctx.settle()
    submenu = next(a.menu() for a in menu.actions() if a.menu() is not None)
    submenu.popup(QPoint(0, 0))
    ctx.settle()
    left, right = menu.grab(), submenu.grab()
    top = next(menu.actionGeometry(a).top() for a in menu.actions() if a.menu() is submenu)
    gap = 4
    canvas = QPixmap(left.width() + gap + right.width(), max(left.height(), top + right.height()))
    canvas.fill(QColor("white"))
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, left)
    painter.drawPixmap(left.width() + gap, top, right)
    painter.end()
    menu.close()
    submenu.close()
    return canvas
