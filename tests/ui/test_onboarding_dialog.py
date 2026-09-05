"""Tests for aida.ui.qt.onboarding_dialog.OnboardingDialog (U4)."""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import ProviderProfile, load_settings
from aida.ui.qt.onboarding_dialog import OnboardingDialog
from aida.workspace.workspaces import WorkspaceConfig


def test_workspace_button_disabled_with_no_profiles(qapp, aida_home: Path, records_home: Path):
    settings = load_settings()  # no profiles configured
    dialog = OnboardingDialog(settings, None, aida_home / "skills")
    assert not dialog._workspace_button.isEnabled()
    assert "No provider profiles configured yet" in dialog._status_label.text()


def test_workspace_button_enabled_once_a_profile_exists(qapp, aida_home: Path, records_home: Path):
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="m"
    )
    dialog = OnboardingDialog(settings, None, aida_home / "skills")
    assert dialog._workspace_button.isEnabled()
    assert "1 provider profile(s) configured" in dialog._status_label.text()


def test_checks_label_reports_pass_count(qapp, aida_home: Path, records_home: Path):
    settings = load_settings()
    dialog = OnboardingDialog(settings, None, aida_home / "skills")
    # Never blank/crashed — the exact pass/fail split depends on the
    # sandbox's own writable dirs and keyring backend, which this test
    # doesn't control, so it only checks the summary line's shape.
    assert "environment checks passed" in dialog._checks_label.text()


def test_checks_label_survives_run_checks_raising(
    qapp, aida_home: Path, records_home: Path, monkeypatch
):
    def _boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("aida.ui.qt.onboarding_dialog.run_checks", _boom)
    settings = load_settings()
    dialog = OnboardingDialog(settings, None, aida_home / "skills")  # must not raise
    assert "could not run environment checks" in dialog._checks_label.text()


def test_add_profile_opens_profiles_dialog_and_refreshes_status(
    qapp, aida_home: Path, records_home: Path, monkeypatch
):
    settings = load_settings()
    dialog = OnboardingDialog(settings, None, aida_home / "skills")

    opened = []

    class _FakeProfilesDialog:
        def __init__(self, settings_arg, bridge_arg, parent_arg):
            opened.append(True)
            self._settings = settings_arg

        def exec(self):
            # Simulate the user adding a profile while the sub-dialog was open.
            self._settings.providers.profiles["mock-profile"] = ProviderProfile(
                name="mock-profile", kind="openai_compat", model="m"
            )
            return 0

    monkeypatch.setattr("aida.ui.qt.profiles_dialog.ProfilesDialog", _FakeProfilesDialog)
    dialog._on_add_profile()

    assert opened == [True]
    assert dialog._workspace_button.isEnabled()


def test_add_workspace_opens_workspace_management_dialog(
    qapp, aida_home: Path, records_home: Path, monkeypatch
):
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="m"
    )
    dialog = OnboardingDialog(settings, None, aida_home / "skills")

    opened = []

    class _FakeWorkspaceManagementDialog:
        def __init__(self, settings_arg, skills_dir_arg, parent_arg):
            opened.append(True)
            self._settings = settings_arg

        def exec(self):
            self._settings.workspaces.workspaces["pyirena"] = WorkspaceConfig(name="pyirena")
            return 0

    monkeypatch.setattr(
        "aida.ui.qt.workspace_management_dialog.WorkspaceManagementDialog",
        _FakeWorkspaceManagementDialog,
    )
    dialog._on_add_workspace()

    assert opened == [True]
    assert "1 workspace(s) configured" in dialog._status_label.text()
