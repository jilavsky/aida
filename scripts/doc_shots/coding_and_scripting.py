"""Shots for docs/guides/coding-and-scripting.md.

01: a seeded assistant reply with a fenced code block, showing its
"</> Open in Editor" button. 02: the real Code Editor dialog after **Run**
of that script (a real subprocess with AIDA's own interpreter) on
placeholder .h5 files under /tmp/aida-docs. 03: the Scripting group of the
workspace form. No model involved.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._common import Context, draw_callouts, shot, widget_rect
from ._package import (
    open_main_window,
    reset_docs_tmp,
    seed_conversation,
    shown_folder,
    standard_settings,
    today_at,
)

SCRIPT = '''"""List the HDF5 files in the source folder, newest first."""
from pathlib import Path

folder = Path("/tmp/aida-docs/data/usaxs-2026-11")
files = sorted(folder.glob("*.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
for f in files:
    print(f"{f.name:28s} {f.stat().st_size / 1024:6.1f} KB")
'''

#: Placeholder data files: name -> size in bytes. Written with fixed
#: modification times so the listing order is the same on every run.
FILES = {"S1_silica.h5": 412_000, "S2_silica.h5": 409_600, "S3_silica.h5": 415_744}


def _placeholder_data() -> None:
    folder = Path(shown_folder("data", "usaxs-2026-11"))
    for i, (name, size) in enumerate(FILES.items()):
        path = folder / name
        path.write_bytes(b"\0" * size)
        stamp = 1_794_000_000 + 600 * i
        os.utime(path, (stamp, stamp))


def _bubble_with_code(window):
    from aida.ui.qt.chat_panel import MessageBubble

    for i in range(window.chat_panel.widget_count):
        widget = window.chat_panel.widget_at(i)
        if isinstance(widget, MessageBubble) and widget._open_in_editor_button.isVisible():
            return widget
    raise LookupError("no bubble with an Open in Editor button")


@shot("coding-and-scripting-01-open-in-editor")
def open_in_editor(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    conv_id = seed_conversation("conversation-script.json", when=today_at(9, 40))
    w = open_main_window(ctx, settings, resume=conv_id)
    bubble = _bubble_with_code(w)
    image = bubble.grab()
    button = bubble._open_in_editor_button
    return draw_callouts(image, [(1, widget_rect(button, bubble), "c", (0, 24))])


@shot("coding-and-scripting-02-code-editor")
def code_editor(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    _placeholder_data()
    w = open_main_window(ctx, settings)
    saved = Path(shown_folder("results", "usaxs-2026-11", "saved_scripts")) / "list_h5.py"
    saved.write_text(SCRIPT, encoding="utf-8")
    from aida.ui.qt.code_editor_dialog import CodeEditorDialog

    workspace = settings.workspaces.workspaces["saxs-analysis"]
    dialog = CodeEditorDialog(
        initial_path=str(saved),
        saved_scripts_dir=str(saved.parent),
        script_timeout_seconds=workspace.script_timeout_seconds,
        bridge=w.bridge,
    )
    ctx.show(dialog, (760, 560))
    dialog._run_button.click()
    ctx.wait_until(lambda: dialog.output_text().startswith("exit code"), timeout=60)
    image = dialog.grab()
    marks = [
        (1, widget_rect(dialog._editor, dialog), "tr", (-24, 14)),
        (2, widget_rect(dialog._run_button, dialog), "r", (-14, 0)),
        (3, widget_rect(dialog._output_view, dialog), "tr", (-24, 14)),
    ]
    return draw_callouts(image, marks)


@shot("coding-and-scripting-03-scripting-group")
def scripting_group(ctx: Context):
    reset_docs_tmp()
    settings = standard_settings()
    from aida.config.paths import skills_dir
    from aida.ui.qt._qt import QGroupBox
    from aida.ui.qt.workspace_management_dialog import WorkspaceFormDialog

    dialog = WorkspaceFormDialog(
        settings=settings,
        skills_dir=skills_dir(),
        workspace=settings.workspaces.workspaces["saxs-analysis"],
    )
    ctx.show(dialog, (720, 1100))
    group = next(g for g in dialog.findChildren(QGroupBox) if g.title() == "Scripting")
    group.window().resize(720, 1100)
    ctx.settle()
    root = group.window()
    rect = widget_rect(group, root).adjusted(-4, -4, 4, 4)
    return root.grab(rect)
