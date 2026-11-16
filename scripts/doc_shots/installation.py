"""Shots for docs/getting-started/installation.md.

The first-run onboarding dialog, on an empty scratch AIDA_HOME (no provider
profiles, no workspaces), as ``aida-gui`` shows it on a fresh install.
"""

from __future__ import annotations

from ._common import Context, shot
from ._package import reset_docs_tmp


@shot("installation-01-onboarding")
def onboarding(ctx: Context):
    from aida.config.paths import skills_dir
    from aida.config.settings import load_settings
    from aida.ui.qt.onboarding_dialog import OnboardingDialog

    reset_docs_tmp()
    dialog = OnboardingDialog(load_settings(), None, skills_dir())
    ctx.show(dialog, (620, dialog.sizeHint().height()))
    return dialog.grab()
