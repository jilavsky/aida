"""``aida config`` — inspect/edit on-device configuration.

Phase 1 provides just enough to show where things live; richer editing
(profile add/remove, workspace edit, etc.) arrives alongside the phases that
give those concepts real behavior (Phase 2 providers, Phase 4 workspaces).
"""

from __future__ import annotations

from aida.config.paths import app_dir, ensure_records_dir


def main(_argv: list[str] | None = None) -> int:
    print(f"AIDA config directory: {app_dir()}")
    print(f"AIDA records directory: {ensure_records_dir()}")
    return 0
