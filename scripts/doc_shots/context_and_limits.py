"""Shots for docs/reference/context-and-limits.md.

The right-hand end of the main window's status bar, with the seeded pyIrena
conversation from the window tour resumed on ``local-ollama``
(``context_window: 32768``), so the Context label reports a real fullness.
"""

from __future__ import annotations

from ._common import Context, draw_callouts, shot, widget_rect
from .gui_overview import _window_with_conversation

CROP_WIDTH = 560
PAD = 32


@shot("context-and-limits-01-status-bar")
def status_bar(ctx: Context):
    w = _window_with_conversation(ctx)
    bar = w.statusBar()
    full = bar.grab()
    dpr = full.devicePixelRatio()
    left = bar.width() - CROP_WIDTH
    crop = full.copy(int(left * dpr), 0, int(CROP_WIDTH * dpr), int(bar.height() * dpr))
    crop.setDevicePixelRatio(dpr)
    # 32 px of status-bar background on the right, for the second callout.
    from aida.ui.qt._qt import QPainter, QPixmap

    image = QPixmap(int((CROP_WIDTH + PAD) * dpr), crop.height())
    image.setDevicePixelRatio(dpr)
    image.fill(bar.palette().window().color())
    painter = QPainter(image)
    painter.drawPixmap(0, 0, crop)
    painter.end()

    def local(label):
        r = widget_rect(label, bar)
        r.translate(-left, 0)
        return r

    marks = [
        (1, local(w._usage_label), "l", (-14, 0)),
        (2, local(w._context_label), "r", (18, 0)),
    ]
    return draw_callouts(image, marks)
