"""Shots for docs/guides/moving-and-sharing.md (setup bundles).

The bundle is exported from the scratch setup (``standard_settings`` plus a
"beamline" workspace under ``${HOME}/Experiments`` and an MCP server in a
conda environment ``bait`` that no machine has), then opened in the real
Import Setup dialog as if on a second machine: HOME and AIDA_HOME switch to
fresh folders, so the Experiments folder and the bait server do not resolve
and the Paths tab appears.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._common import Context, draw_callouts, shot, widget_rect
from ._package import reset_docs_tmp, shown_folder, standard_settings


def _bundle() -> Path:
    from aida.config.settings import (
        McpServerConfig,
        WorkspaceConfig,
        load_settings,
        save_mcp_config,
        save_workspaces_config,
    )
    from aida.portability import export_bundle

    reset_docs_tmp()
    os.environ["HOME"] = shown_folder("first-machine-home")
    experiments = Path(shown_folder("first-machine-home", "Experiments", "2026"))
    standard_settings(with_pyirena=False)
    settings = load_settings()
    settings.mcp.servers["pyirena"] = McpServerConfig(
        name="pyirena", command="pyirena-mcp", groups=["pyirena-analysis"]
    )
    settings.mcp.servers["bait-mcp"] = McpServerConfig(
        name="bait-mcp", command="/opt/miniconda3/envs/bait/bin/bait-mcp", groups=["beamline"]
    )
    save_mcp_config(settings.mcp)
    settings.workspaces.workspaces["saxs-analysis"].mcp_group = "pyirena-analysis"
    settings.workspaces.workspaces["beamline"] = WorkspaceConfig(
        name="beamline",
        profile="local-ollama",
        source_folders=[str(experiments)],
        mcp_group="beamline",
    )
    save_workspaces_config(settings.workspaces)
    bundle = Path(shown_folder("transfer")) / "aida-setup.zip"
    export_bundle(bundle)
    return bundle


def _second_machine(ctx: Context) -> None:
    os.environ["HOME"] = shown_folder("second-machine-home")
    os.environ["AIDA_HOME"] = str(ctx.scratch / "second-machine-aida")


@shot("moving-and-sharing-01-export")
def export(ctx: Context):
    reset_docs_tmp()
    standard_settings()
    from aida.ui.qt.portability_dialog import ExportSetupDialog

    dialog = ExportSetupDialog(default_dir=Path(shown_folder("transfer")))
    ctx.show(dialog, (620, dialog.sizeHint().height()))
    return dialog.grab()


@shot("moving-and-sharing-02-import-contents")
def import_contents(ctx: Context):
    bundle = _bundle()
    _second_machine(ctx)
    from aida.ui.qt.portability_dialog import _KEY_ROLE, ImportSetupDialog

    dialog = ImportSetupDialog(source=bundle)
    ctx.show(dialog, (760, 640))
    dialog._set_all(False)
    from aida.ui.qt._qt import Qt

    for child in dialog._child_items():
        if child.data(0, _KEY_ROLE) == ("workspace", "saxs-analysis"):
            child.setCheckState(0, Qt.CheckState.Checked)
    dialog._tree.expandAll()
    ctx.settle()
    image = dialog.grab()
    return draw_callouts(
        image,
        [
            (1, widget_rect(dialog._tree, dialog), "tr", (-24, 14)),
            (2, widget_rect(dialog._closure_label, dialog), "l", (-14, 0)),
        ],
    )


@shot("moving-and-sharing-03-import-paths")
def import_paths(ctx: Context):
    bundle = _bundle()
    _second_machine(ctx)
    from aida.ui.qt.portability_dialog import ImportSetupDialog

    dialog = ImportSetupDialog(source=bundle)
    ctx.show(dialog, (860, 560))
    dialog._tabs.setCurrentWidget(dialog._paths_tab)
    dialog._paths_table.resizeColumnsToContents()
    dialog._paths_table.horizontalHeader().setStretchLastSection(True)
    ctx.settle()
    return dialog.grab()
