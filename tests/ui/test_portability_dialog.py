"""Tests for aida.ui.qt.portability_dialog.

The GUI half of `aida config export` / `aida config import`. What is worth
asserting here is the defaults — the ones that decide whether a bundle a
user emails is safe, and whether an import can quietly clobber their setup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.portability import CONFLICT_POLICIES
from aida.ui.qt._qt import Qt
from aida.ui.qt.portability_dialog import (
    BundleReportDialog,
    ExportSetupDialog,
    ImportSetupDialog,
)


def test_export_defaults_are_the_safe_ones(qapp, tmp_path: Path):
    """Personal context and private workspace notes must be opt-in: the
    default bundle is the one that is safe to send to a colleague."""
    dialog = ExportSetupDialog(default_dir=tmp_path)
    try:
        assert dialog.include_personal() is False
        assert dialog.destination() == tmp_path / "aida-setup.zip"
    finally:
        dialog.deleteLater()


def test_export_supplies_a_zip_suffix_for_a_bare_name(qapp, tmp_path: Path):
    dialog = ExportSetupDialog(default_dir=tmp_path)
    try:
        dialog._path_edit.setText(str(tmp_path / "beamline-setup"))
        assert dialog.destination() == tmp_path / "beamline-setup.zip"
        # An explicit suffix the user chose is left alone.
        dialog._path_edit.setText(str(tmp_path / "beamline-setup.bundle"))
        assert dialog.destination() == tmp_path / "beamline-setup.bundle"
    finally:
        dialog.deleteLater()


def test_import_defaults_to_skipping_and_not_touching_app_settings(qapp):
    dialog = ImportSetupDialog()
    try:
        assert dialog.conflict() == "skip"
        assert dialog.apply_app_settings() is False
    finally:
        dialog.deleteLater()


def test_import_offers_every_conflict_policy(qapp):
    """The combo's data values are handed straight to ``import_bundle``, so
    a policy renamed on one side and not the other would raise at run
    time rather than at import time."""
    dialog = ImportSetupDialog()
    try:
        offered = [
            dialog._conflict_combo.itemData(i) for i in range(dialog._conflict_combo.count())
        ]
        assert offered == list(CONFLICT_POLICIES)
        assert all(dialog._conflict_combo.itemText(i) for i in range(len(offered)))
    finally:
        dialog.deleteLater()


def test_import_prefills_a_given_source(qapp, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    dialog = ImportSetupDialog(source=bundle)
    try:
        assert dialog.source() == bundle
    finally:
        dialog.deleteLater()


def test_report_dialog_shows_the_text_verbatim(qapp):
    """The report is something the user acts on away from AIDA — installing
    a conda env, running `aida config secret set` lines — so it has to be
    readable and copyable, not summarized."""
    text = "Imported /tmp/setup.zip\n\nSECRETS TO SET:\n  aida config secret set argo"
    dialog = BundleReportDialog("Setup Imported", text)
    try:
        assert dialog._view.toPlainText() == text
        assert dialog._view.isReadOnly()
        assert dialog.windowTitle() == "Setup Imported"
    finally:
        dialog.deleteLater()


# --- Tier 2: the contents picker and the paths table ----------------------


@pytest.fixture
def linked_bundle(aida_home, tmp_path: Path) -> Path:
    """A bundle whose workspace references a profile, a skill and an MCP
    group, plus a second workspace that shares none of them — so checking
    one thing has a visible, bounded effect."""
    from aida.config.settings import (
        McpConfig,
        McpServerConfig,
        ProviderProfile,
        ProvidersConfig,
        WorkspaceConfig,
        WorkspacesConfig,
        save_mcp_config,
        save_providers_config,
        save_workspaces_config,
    )
    from aida.portability import export_bundle

    save_providers_config(
        ProvidersConfig(
            profiles={
                "argo": ProviderProfile(name="argo", model="sonnet"),
                "spare": ProviderProfile(name="spare", model="gemma"),
            }
        ),
        aida_home,
    )
    save_mcp_config(
        McpConfig(
            servers={
                "pyirena-mcp": McpServerConfig(
                    name="pyirena-mcp", command="npx", groups=["analysis"]
                ),
                "ghost": McpServerConfig(
                    name="ghost", command="/opt/miniconda3/envs/no-such-env/bin/ghost-mcp"
                ),
            }
        ),
        aida_home,
    )
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "analysis": WorkspaceConfig(
                    name="analysis", profile="argo", mcp_group="analysis", skills=["saxs"]
                ),
                "spare-ws": WorkspaceConfig(name="spare-ws", profile="spare"),
            }
        ),
        aida_home,
    )
    (aida_home / "skills").mkdir(exist_ok=True)
    (aida_home / "skills" / "saxs.md").write_text("# saxs\n", encoding="utf-8")
    bundle = tmp_path / "linked.zip"
    export_bundle(bundle, base_dir=aida_home)
    return bundle


def _loaded(bundle: Path) -> ImportSetupDialog:
    dialog = ImportSetupDialog(source=bundle)
    assert dialog._contents is not None, "bundle failed to load"
    return dialog


def _check_only(dialog: ImportSetupDialog, key: tuple[str, str]) -> None:
    from aida.ui.qt._qt import Qt
    from aida.ui.qt.portability_dialog import _KEY_ROLE

    dialog._set_all(False)
    for child in dialog._child_items():
        if child.data(0, _KEY_ROLE) == key:
            child.setCheckState(0, Qt.CheckState.Checked)
            return
    raise AssertionError(f"{key} not in the tree")


def test_a_bad_bundle_path_disables_import_without_a_modal(qapp, tmp_path: Path):
    """The user is still typing; a message box per keystroke would be
    intolerable, so the status line carries the error instead."""
    dialog = ImportSetupDialog(source=tmp_path / "nope.zip")
    try:
        assert dialog._contents is None
        assert not dialog._ok_button.isEnabled()
        assert not dialog._tabs.isVisibleTo(dialog)
        assert "nope.zip" in dialog._status_label.text()
    finally:
        dialog.deleteLater()


def test_loading_a_bundle_lists_everything_checked(qapp, linked_bundle: Path):
    dialog = _loaded(linked_bundle)
    try:
        # isVisibleTo, not isVisible: a child of a dialog that was never
        # shown reports isVisible() False no matter what it was set to.
        assert dialog._tabs.isVisibleTo(dialog)
        assert dialog._ok_button.isEnabled()
        assert all(child.checkState(0) == Qt.CheckState.Checked for child in dialog._child_items())
        # Everything checked means "not a partial pick" — see selection()'s
        # docstring on why that is None rather than the full key set.
        assert dialog.selection() is None
    finally:
        dialog.deleteLater()


def test_checking_a_workspace_checks_what_it_needs(qapp, linked_bundle: Path):
    """The whole reason the picker is not just a list of checkboxes:
    importing a workspace without its profile and servers produces something
    that looks configured and is not."""
    dialog = _loaded(linked_bundle)
    try:
        _check_only(dialog, ("workspace", "analysis"))
        selection = dialog.selection()
        assert selection == {
            ("workspace", "analysis"),
            ("profile", "argo"),
            ("skill", "saxs"),
            ("MCP server", "pyirena-mcp"),
        }
        # ...and it says so, rather than silently ticking boxes.
        assert "profile: argo" in dialog._closure_label.text()
    finally:
        dialog.deleteLater()


def test_select_none_then_all_returns_to_the_default(qapp, linked_bundle: Path):
    dialog = _loaded(linked_bundle)
    try:
        dialog._set_all(False)
        assert dialog.selection() == set()
        dialog._set_all(True)
        assert dialog.selection() is None
    finally:
        dialog.deleteLater()


def test_paths_tab_appears_only_when_something_is_wrong(qapp, linked_bundle: Path):
    """A dialog that always shows an empty problem table teaches people to
    ignore it."""
    dialog = _loaded(linked_bundle)
    try:
        tabs = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
        assert any(t.startswith("Paths") for t in tabs)  # 'ghost' cannot resolve
        assert dialog._paths_table.rowCount() == 1

        # Deselect the broken server and the tab goes away: paths belonging
        # to items you did not pick are not your problem.
        _check_only(dialog, ("profile", "argo"))
        tabs = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
        assert not any(t.startswith("Paths") for t in tabs)
    finally:
        dialog.deleteLater()


def test_typing_a_replacement_becomes_a_path_override(qapp, linked_bundle: Path, tmp_path: Path):
    from aida.ui.qt._qt import QTableWidgetItem
    from aida.ui.qt.portability_dialog import _COL_BUNDLE_VALUE, _COL_REPLACEMENT

    dialog = _loaded(linked_bundle)
    try:
        assert dialog._paths_table.rowCount() == 1
        bundle_value = dialog._paths_table.item(0, _COL_BUNDLE_VALUE).text()
        assert "no-such-env" in bundle_value

        replacement = tmp_path / "ghost-mcp"
        replacement.write_text("", encoding="utf-8")
        dialog._paths_table.setItem(0, _COL_REPLACEMENT, QTableWidgetItem(str(replacement)))

        assert dialog.path_overrides() == {bundle_value: str(replacement)}
    finally:
        dialog.deleteLater()


def test_an_empty_replacement_is_not_an_override(qapp, linked_bundle: Path):
    """Leaving a row blank means "import it unchanged and fix it later",
    not "map it to the empty string"."""
    from aida.ui.qt._qt import QTableWidgetItem
    from aida.ui.qt.portability_dialog import _COL_REPLACEMENT

    dialog = _loaded(linked_bundle)
    try:
        dialog._paths_table.setItem(0, _COL_REPLACEMENT, QTableWidgetItem("   "))
        assert dialog.path_overrides() == {}
    finally:
        dialog.deleteLater()
