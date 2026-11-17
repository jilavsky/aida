"""Shots for docs/guides/skills.md.

The real main window on the standard scratch configuration, with every
bundled sample skill installed into the scratch skills folder. The pyIrena
stand-in server attaches ``saxs-basics`` and ``pyirena-usage``, so those two
are ticked; the others are available but off.
"""

from __future__ import annotations

from ._common import Context, shot
from ._package import open_main_window, reset_docs_tmp, standard_settings


@shot("skills-01-panel")
def panel(ctx: Context):
    from aida.config.paths import install_bundled_skills

    reset_docs_tmp()
    settings = standard_settings()
    install_bundled_skills()
    window = open_main_window(ctx, settings)
    section = window._sections["Skills"]
    return section.grab()
