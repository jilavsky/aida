from __future__ import annotations

from pathlib import Path

import pytest

from aida.config import paths


def test_app_dir_created(aida_home: Path):
    d = paths.app_dir()
    assert d == aida_home
    assert d.is_dir()


def test_subdirs_created_idempotently(aida_home: Path):
    for _ in range(2):
        assert paths.logs_dir().is_dir()
        assert paths.artifacts_dir().is_dir()
        assert paths.skills_dir().is_dir()
        assert paths.workflows_dir().is_dir()


def test_config_dir_is_app_dir(aida_home: Path):
    assert paths.config_dir() == aida_home


def test_db_path_under_app_dir(aida_home: Path):
    assert paths.db_path().parent == aida_home
    assert paths.db_path().name == "aida.db"


def test_schedules_path_under_app_dir(aida_home: Path):
    assert paths.schedules_path() == aida_home / "schedules.yaml"


def test_scheduler_lock_path_under_app_dir(aida_home: Path):
    assert paths.scheduler_lock_path() == aida_home / "scheduler.lock"


def test_default_records_dir(records_home: Path):
    expected = records_home / "Documents" / "Aida"
    assert paths.default_records_dir() == expected


def test_ensure_records_dir_override(tmp_path: Path):
    custom = tmp_path / "somewhere" / "else"
    result = paths.ensure_records_dir(custom)
    assert result == custom
    assert custom.is_dir()


def test_default_scratch_dir_under_app_dir(aida_home: Path):
    assert paths.default_scratch_dir() == aida_home / "tmp"


def test_ensure_scratch_dir_creates_default(aida_home: Path):
    result = paths.ensure_scratch_dir()
    assert result == aida_home / "tmp"
    assert result.is_dir()


def test_ensure_scratch_dir_override(tmp_path: Path):
    custom = tmp_path / "somewhere" / "scratch"
    result = paths.ensure_scratch_dir(custom)
    assert result == custom
    assert custom.is_dir()


# --- bundled skills -------------------------------------------------------


def test_install_bundled_skills_copies_the_shipped_samples(aida_home):
    """A `pip install aida-workbench` user has no repo to copy skills from,
    while workspaces.yaml examples and the pyIrena MCP setup both reference
    them by name."""
    from aida.config.paths import bundled_skills_dir, install_bundled_skills, skills_dir

    if bundled_skills_dir() is None:
        pytest.skip("no bundled skills in this layout")

    installed = install_bundled_skills(["saxs-basics"])

    assert installed == ["saxs-basics"]
    assert (skills_dir() / "saxs-basics.md").is_file()
    # The README is documentation about the folder, not a skill.
    assert not (skills_dir() / "README.md").exists()


def test_install_bundled_skills_never_overwrites_a_users_edited_copy(aida_home):
    """Once a skill is in the user's folder it is theirs — tailored to their
    beamline. A later AIDA upgrade silently replacing it would be the worst
    kind of data loss."""
    from aida.config.paths import bundled_skills_dir, install_bundled_skills, skills_dir

    if bundled_skills_dir() is None:
        pytest.skip("no bundled skills in this layout")

    mine = skills_dir() / "saxs-basics.md"
    mine.write_text("# my own version\n", encoding="utf-8")

    assert install_bundled_skills(["saxs-basics"]) == []
    assert mine.read_text(encoding="utf-8") == "# my own version\n"
