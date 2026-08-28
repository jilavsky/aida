"""Tests for aida.mcp.pyirena_setup — detection of pyIrena's MCP server and
the one-click config it produces.

Detection touches the real filesystem and the real `sys.executable`, so
every test here fakes both rather than depending on whether pyIrena happens
to be installed in the environment running the suite.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aida.mcp.pyirena_setup import (
    DEFAULT_GROUP,
    DEFAULT_SKILLS,
    PyirenaMcpCandidate,
    find_pyirena_mcp,
    pyirena_server_config,
)

SCRIPT_NAME = "pyirena-mcp.exe" if os.name == "nt" else "pyirena-mcp"
BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


@pytest.fixture(autouse=True)
def _no_real_installations(monkeypatch, tmp_path: Path):
    """Neutralize every search location by default, so each test opts into
    exactly the one candidate it is about."""
    monkeypatch.setattr("aida.mcp.pyirena_setup.shutil.which", lambda _name: None)
    monkeypatch.setattr("aida.mcp.pyirena_setup._pyirena_importable", lambda: False)
    monkeypatch.setattr("aida.mcp.pyirena_setup._candidate_env_dirs", list)
    fake_bin = tmp_path / "empty-env" / BIN_DIRNAME
    fake_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))


def _make_script(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    return script


def test_no_installation_found_is_an_empty_list_not_an_error():
    assert find_pyirena_mcp() == []


def test_finds_the_script_next_to_aidas_own_interpreter(monkeypatch, tmp_path: Path):
    """The `pip install aida-workbench pyirena[mcp]` into one environment
    case — and the only candidate guaranteed to stay in step with AIDA."""
    bin_dir = tmp_path / "shared-env" / BIN_DIRNAME
    script = _make_script(bin_dir)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))

    candidates = find_pyirena_mcp()

    assert [c.command for c in candidates] == [str(script)]
    assert candidates[0].source == "AIDA's own environment"


def test_finds_a_sibling_conda_environment(monkeypatch, tmp_path: Path):
    """The common beamline layout: pyIrena in its own env because its
    dependency set is heavy. AIDA speaks to it over stdio, so they never
    have to share an interpreter."""
    envs = tmp_path / "miniconda3" / "envs"
    script = _make_script(envs / "pyirena" / BIN_DIRNAME)
    monkeypatch.setattr("aida.mcp.pyirena_setup._candidate_env_dirs", lambda: [envs])

    candidates = find_pyirena_mcp()

    assert [c.command for c in candidates] == [str(script)]
    assert "pyirena" in candidates[0].source


def test_python_dash_m_is_only_offered_when_no_script_exists(monkeypatch, tmp_path: Path):
    """`python -m` in the same environment launches the identical server, so
    offering it alongside the console script would make every ordinary
    install look like an ambiguous choice the user has to resolve."""
    bin_dir = tmp_path / "shared-env" / BIN_DIRNAME
    _make_script(bin_dir)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr("aida.mcp.pyirena_setup._pyirena_importable", lambda: True)

    assert len(find_pyirena_mcp()) == 1


def test_python_dash_m_fallback_when_the_console_script_is_missing(monkeypatch, tmp_path: Path):
    """An editable install whose entry points were never linked still has an
    importable pyirena.mcp.server."""
    bin_dir = tmp_path / "editable-env" / BIN_DIRNAME
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr("aida.mcp.pyirena_setup._pyirena_importable", lambda: True)

    candidates = find_pyirena_mcp()

    assert len(candidates) == 1
    assert candidates[0].args == ["-m", "pyirena.mcp.server"]


def test_an_unreadable_envs_directory_is_skipped_not_raised(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "aida.mcp.pyirena_setup._candidate_env_dirs", lambda: [tmp_path / "does-not-exist"]
    )
    assert find_pyirena_mcp() == []


def test_duplicate_paths_are_reported_once(monkeypatch, tmp_path: Path):
    """The same script reachable both next to sys.executable and on PATH is
    one installation, not two."""
    bin_dir = tmp_path / "env" / BIN_DIRNAME
    script = _make_script(bin_dir)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr("aida.mcp.pyirena_setup.shutil.which", lambda _name: str(script))

    assert len(find_pyirena_mcp()) == 1


def test_server_config_defaults_match_what_the_docs_promise():
    config = pyirena_server_config(PyirenaMcpCandidate(command="/opt/envs/pyirena/bin/pyirena-mcp"))

    assert config.name == "pyirena"
    assert config.command == "/opt/envs/pyirena/bin/pyirena-mcp"
    assert config.groups == [DEFAULT_GROUP]
    assert config.skills == list(DEFAULT_SKILLS)
    # Set explicitly rather than left to pyIrena's default: it is the one
    # knob controlling how much context a single tool result can eat, so a
    # user tuning it should find it already in their mcp.json.
    assert config.env["PYIRENA_MAX_ARRAY_POINTS"] == "500"
    assert "PYIRENA_DATA_ROOT" not in config.env


def test_data_root_is_expanded_and_set(tmp_path: Path):
    config = pyirena_server_config(
        PyirenaMcpCandidate(command="pyirena-mcp"), data_root=str(tmp_path / "saxs")
    )
    assert config.env["PYIRENA_DATA_ROOT"] == str(tmp_path / "saxs")


def test_the_python_dash_m_form_round_trips_into_command_and_args():
    candidate = PyirenaMcpCandidate(command="/env/bin/python", args=["-m", "pyirena.mcp.server"])
    config = pyirena_server_config(candidate)
    assert config.command == "/env/bin/python"
    assert config.args == ["-m", "pyirena.mcp.server"]
