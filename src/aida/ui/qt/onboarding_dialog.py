"""``OnboardingDialog`` (U4, planning/improvement_plan_2026-08.md §3): the
first-run experience. Previously a genuine first launch (no
``providers.yaml`` profiles at all — the common case for anyone who just
installed AIDA) hit the bare "No profile given: pass --profile NAME, or
--workspace NAME with a profile configured." critical dialog, with no path
forward short of finding and hand-editing ``providers.yaml``.

Shown by ``MainWindow._on_startup_failed`` in place of that dialog whenever
startup failed *and* zero provider profiles are configured — any other
startup failure (an unknown workspace, a typo'd ``--mcp`` name, a broken
resume) still gets the plain critical dialog, since those mean "you
configured something and it's wrong," not "you haven't set anything up
yet."

Runs the same checks ``aida doctor`` does (env/config/writable-dirs — never
a slow one here, since with zero profiles configured the provider-
reachability check returns immediately, see
``aida.cli.doctor._check_provider_endpoints``), then hands off to U2's
``ProfilesDialog`` and U1's ``WorkspaceManagementDialog`` for the actual
setup work rather than duplicating either.
"""

from __future__ import annotations

from pathlib import Path

from aida.cli.doctor import run_checks
from aida.config.settings import Settings
from aida.ui.qt._qt import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    Qt,
    QVBoxLayout,
    QWidget,
)


class OnboardingDialog(QDialog):
    def __init__(self, settings: Settings, bridge, skills_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to AIDA")
        self.resize(480, 360)
        self._settings = settings
        self._bridge = bridge
        self._skills_dir = skills_dir

        layout = QVBoxLayout(self)

        intro = QLabel(
            "AIDA needs at least one provider profile before it can start a chat "
            "session. Add one below — then, optionally, set up a workspace with "
            "your usual source/target folders, MCP tools, and skills.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._checks_label = QLabel(self)
        self._checks_label.setWordWrap(True)
        self._checks_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._checks_label)
        self._run_checks()

        self._profile_button = QPushButton("Add a Provider Profile…", self)
        self._profile_button.clicked.connect(self._on_add_profile)
        layout.addWidget(self._profile_button)

        self._workspace_button = QPushButton("Create a Workspace…", self)
        self._workspace_button.clicked.connect(self._on_add_workspace)
        layout.addWidget(self._workspace_button)

        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._refresh_status()

    def _run_checks(self) -> None:
        """Best-effort — a check that itself blows up must never stop the
        onboarding panel from opening at all (the one thing worse than "no
        profile given" is a *crash* on first launch)."""
        try:
            results = run_checks()
        except Exception as exc:  # noqa: BLE001 - see docstring above
            self._checks_label.setText(f"(could not run environment checks: {exc})")
            return
        n_fail = sum(1 for r in results if not r.ok)
        lines = [f"{len(results) - n_fail}/{len(results)} environment checks passed."]
        lines.extend(f"- {r.name}: {r.detail}" for r in results if not r.ok)
        self._checks_label.setText("\n".join(lines))

    def _refresh_status(self) -> None:
        n_profiles = len(self._settings.providers.profiles)
        n_workspaces = len(self._settings.workspaces.workspaces)
        self._workspace_button.setEnabled(n_profiles > 0)
        if n_profiles == 0:
            self._status_label.setText("No provider profiles configured yet.")
        else:
            self._status_label.setText(
                f"{n_profiles} provider profile(s) configured, {n_workspaces} workspace(s) configured. "
                "Closing this dialog starts a chat session."
            )

    def _on_add_profile(self) -> None:
        from aida.ui.qt.profiles_dialog import ProfilesDialog

        dialog = ProfilesDialog(self._settings, self._bridge, self)
        dialog.exec()
        self._refresh_status()

    def _on_add_workspace(self) -> None:
        from aida.ui.qt.workspace_management_dialog import WorkspaceManagementDialog

        dialog = WorkspaceManagementDialog(self._settings, self._skills_dir, self)
        dialog.exec()
        self._refresh_status()


__all__ = ["OnboardingDialog"]
