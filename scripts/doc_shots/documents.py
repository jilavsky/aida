"""Shots for docs/guides/documents.md (attached documents and figures).

No document is read: the attached "paper" is a placeholder file under
/tmp/aida-docs, shown as a chip in the input box before sending. The
Documents settings shot is the real Settings dialog on the scratch config.
"""

from __future__ import annotations

from pathlib import Path

from ._common import Context, draw_callouts, shot, widget_rect
from ._package import open_main_window, reset_docs_tmp, shown_folder, standard_settings


@shot("documents-01-attached")
def attached(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    paper = Path(shown_folder("papers")) / "silica-pdms-usaxs.pdf"
    paper.write_bytes(b"%PDF-1.4\n% placeholder for the documentation screenshot\n")
    w = open_main_window(ctx, settings)
    w.input_box.add_attachment(str(paper))
    w.input_box.set_text("What figures are in this paper?")
    ctx.settle(30)
    box = w.input_box
    image = box.grab()
    from aida.ui.qt.input_box import _AttachmentChip

    chip = w.input_box.findChildren(_AttachmentChip)[0]
    attach = next(
        b
        for b in w.input_box.findChildren(type(w.documentation_button))
        if b.text().startswith("Attach")
    )
    return draw_callouts(
        image,
        [
            (1, widget_rect(chip, box), "r", (14, 0)),
            (2, widget_rect(attach, box), "l", (-14, 0)),
        ],
    )


@shot("documents-02-settings-documents")
def settings_documents(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    from aida.ui.qt.window_state import apply_font_size

    apply_font_size(ctx.app, settings.app)
    from aida.ui.qt.settings_dialog import SettingsDialog

    dialog = SettingsDialog(settings.app, settings.providers.profiles)
    ctx.show(dialog, (640, 980))
    from aida.ui.qt._qt import QGroupBox

    group = next(g for g in dialog.findChildren(QGroupBox) if g.title() == "Documents")
    rect = widget_rect(group, dialog).adjusted(-4, -2, 4, 4)
    return dialog.grab(rect)
