"""Shots for docs/reference/providers-and-secrets.md.

The Providers dialog (``ProfilesDialog``) and its Add/Edit form on the two
neutral profiles from ``standard_settings()``: ``local-ollama`` (no key) and
``claude-direct`` (``secret_ref: claude-direct``). The keychain is keyring's
fail backend, so no secret is ever looked up.
"""

from __future__ import annotations

from ._common import Context, draw_callouts, shot, widget_rect
from ._package import reset_docs_tmp, standard_settings


def _button(widget, text: str):
    from aida.ui.qt._qt import QPushButton

    return next(
        b
        for b in widget.findChildren(QPushButton)
        if b.isVisible() and b.text().rstrip("…") == text
    )


@shot("providers-and-secrets-01-providers-dialog")
def providers_dialog(ctx: Context):
    from aida.ui.qt._qt import QTabWidget
    from aida.ui.qt.profiles_dialog import ProfilesDialog

    reset_docs_tmp()
    settings = standard_settings()
    dialog = ProfilesDialog(settings, None)
    ctx.show(dialog, (760, 420))
    tabs = dialog.findChild(QTabWidget)
    profile_list = dialog._provider_list
    profile_list.setCurrentRow(0)
    ctx.settle()
    image = dialog.grab()
    marks = [
        (1, widget_rect(tabs.tabBar(), dialog), "r", (16, 0)),
        (2, widget_rect(profile_list, dialog), "r", (-16, 0)),
        (3, widget_rect(_button(dialog, "Add"), dialog), "r", (-16, 0)),
        (4, widget_rect(_button(dialog, "Test"), dialog), "r", (-16, 0)),
    ]
    return draw_callouts(image, marks)


@shot("providers-and-secrets-02-profile-form")
def profile_form(ctx: Context):
    from aida.ui.qt.profiles_dialog import ProviderProfileFormDialog

    reset_docs_tmp()
    settings = standard_settings()
    profile = settings.providers.profiles["claude-direct"]
    dialog = ProviderProfileFormDialog(profile=profile)
    ctx.show(dialog, (520, dialog.sizeHint().height()))
    return dialog.grab()
