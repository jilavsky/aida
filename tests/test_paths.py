from __future__ import annotations

from pathlib import Path

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
