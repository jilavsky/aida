"""Tests for aida.ui.qt.portability_dialog.

The GUI half of `aida config export` / `aida config import`. What is worth
asserting here is the defaults — the ones that decide whether a bundle a
user emails is safe, and whether an import can quietly clobber their setup.
"""

from __future__ import annotations

from pathlib import Path

from aida.portability import CONFLICT_POLICIES
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
