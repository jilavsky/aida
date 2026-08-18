"""Shared pytest fixtures.

The most important fixture here is ``aida_home``: it points ``AIDA_HOME`` at
a temp directory for the duration of a test, so nothing under test ever
touches a real developer's ``~/.aida``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def aida_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".aida"
    monkeypatch.setenv("AIDA_HOME", str(home))
    return home


@pytest.fixture
def records_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the default records dir (~/Documents/Aida) for a test."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home
