"""Shots for docs/reference/mcp-servers.md.

The MCP Servers dialog and its Edit Server form, on an ``mcp.json`` with two
neutral entries: a stdio ``pyirena`` server whose command is the usual
conda-env path (``/opt/conda/envs/pyirena/bin/pyirena-mcp``; it does not
exist and is never started) and the page's remote http example. Nothing is
launched: the dialog has no live session (``bridge=None``).
"""

from __future__ import annotations

from ._common import Context, draw_callouts, shot, widget_rect
from ._package import reset_docs_tmp, standard_settings


def _settings():
    from aida.config.paths import skills_dir
    from aida.config.settings import (
        McpConfig,
        McpServerConfig,
        load_settings,
        save_mcp_config,
    )

    reset_docs_tmp()
    standard_settings(with_pyirena=False)
    save_mcp_config(
        McpConfig(
            servers={
                "pyirena": McpServerConfig(
                    name="pyirena",
                    command="/opt/conda/envs/pyirena/bin/pyirena-mcp",
                    env={"PYIRENA_MAX_ARRAY_POINTS": "500"},
                    groups=["pyirena-analysis"],
                    skills=["saxs-basics", "pyirena-usage"],
                ),
                "usaxscontrol-instrument": McpServerConfig(
                    name="usaxscontrol-instrument",
                    type="http",
                    url="https://usaxscontrol.example:8765/mcp",
                    headers={"Authorization": "Bearer keyring:usaxscontrol_token"},
                    groups=["instrument-status"],
                ),
            }
        )
    )
    return load_settings(), skills_dir()


def _button(widget, text: str):
    from aida.ui.qt._qt import QPushButton

    return next(b for b in widget.findChildren(QPushButton) if b.text() == text)


@shot("mcp-servers-01-dialog")
def dialog(ctx: Context):
    from aida.ui.qt.mcp_management_dialog import McpManagementDialog

    settings, skills = _settings()
    dlg = McpManagementDialog(settings, None, skills)
    ctx.show(dlg, (900, 560))
    dlg._server_list.setCurrentRow(0)
    ctx.settle()
    image = dlg.grab()
    marks = [
        (1, widget_rect(dlg._server_list, dlg), "r", (-16, 0)),
        (2, widget_rect(_button(dlg, "Add Server…"), dlg), "r", (-16, 0)),
        (3, widget_rect(_button(dlg, "Start"), dlg), "r", (-16, 0)),
        (4, widget_rect(_button(dlg, "Test Connection"), dlg), "r", (-16, 0)),
        (5, widget_rect(_button(dlg, "Import mcp.json…"), dlg), "r", (-16, 0)),
        (6, widget_rect(dlg._tabs.tabBar(), dlg), "r", (16, 0)),
        (7, widget_rect(_button(dlg, "Edit…"), dlg), "r", (-16, 0)),
    ]
    return draw_callouts(image, marks)


@shot("mcp-servers-02-server-form")
def server_form(ctx: Context):
    from aida.ui.qt.mcp_management_dialog import ServerFormDialog

    settings, skills = _settings()
    form = ServerFormDialog(
        mcp_config=settings.mcp, server=settings.mcp.servers["pyirena"], skills_dir=skills
    )
    ctx.show(form, (620, 640))
    return form.grab()
