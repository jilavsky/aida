"""Shots for docs/guides/workspaces.md.

The scratch configuration from ``_package.standard_settings``: workspaces
``saxs-analysis`` (local-ollama, pyIrena group, /tmp/aida-docs folders) and
``notebook`` (claude-direct); the edit form also has the knowledge base
``usaxs-notes`` from ``knowledge_bases.add_knowledge_base``.
"""

from __future__ import annotations

from ._common import Context, shot
from ._package import reset_docs_tmp, standard_settings


def _settings():
    reset_docs_tmp()
    return standard_settings()


@shot("workspaces-01-management")
def management(ctx: Context):
    from aida.config.paths import skills_dir
    from aida.ui.qt.workspace_management_dialog import WorkspaceManagementDialog

    dialog = WorkspaceManagementDialog(_settings(), skills_dir())
    dialog._workspace_list.setCurrentRow(
        next(
            i
            for i in range(dialog._workspace_list.count())
            if dialog._workspace_list.item(i).text().startswith("saxs-analysis")
        )
    )
    ctx.show(dialog, (720, 460))
    return dialog.grab()


@shot("workspaces-02-edit-form")
def edit_form(ctx: Context):
    from aida.config.paths import skills_dir
    from aida.ui.qt.workspace_management_dialog import WorkspaceFormDialog

    from .knowledge_bases import add_knowledge_base

    settings = add_knowledge_base()
    dialog = WorkspaceFormDialog(
        settings=settings,
        skills_dir=skills_dir(),
        workspace=settings.workspaces.workspaces["saxs-analysis"],
    )
    ctx.show(dialog, (640, 900))
    return dialog.grab()
