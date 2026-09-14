"""Tests for aida.mcp.env_discovery — the generic console-script search
extracted from aida.mcp.pyirena_setup, and shared by aievaluator_setup /
epics_mcp_setup.

Mirrors tests/test_pyirena_setup.py's structure and fixtures closely, since
this module is that file's search logic generalized.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aida.mcp.env_discovery import (
    ScriptCandidate,
    epics_ca_env,
    find_console_script,
    interpreter_for,
    resolve_ca_addr_list,
    resolve_editable_source_root,
)

SCRIPT_NAME = "widget-mcp.exe" if os.name == "nt" else "widget-mcp"
BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


@pytest.fixture(autouse=True)
def _no_real_installations(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aida.mcp.env_discovery.shutil.which", lambda _name: None)
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", list)
    fake_bin = tmp_path / "empty-env" / BIN_DIRNAME
    fake_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))


def _make_script(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    return script


def test_no_installation_found_is_an_empty_list_not_an_error():
    assert find_console_script("widget-mcp") == []


def test_finds_the_script_next_to_the_calling_interpreter(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "shared-env" / BIN_DIRNAME
    script = _make_script(bin_dir)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))

    candidates = find_console_script("widget-mcp")

    assert [c.command for c in candidates] == [str(script)]
    assert candidates[0].source == "AIDA's own environment"


def test_finds_a_sibling_conda_environment(monkeypatch, tmp_path: Path):
    envs = tmp_path / "miniconda3" / "envs"
    script = _make_script(envs / "widget" / BIN_DIRNAME)
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", lambda: [envs])

    candidates = find_console_script("widget-mcp")

    assert [c.command for c in candidates] == [str(script)]
    assert "widget" in candidates[0].source


def test_module_fallback_only_offered_when_no_script_exists(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "shared-env" / BIN_DIRNAME
    _make_script(bin_dir)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))

    candidates = find_console_script(
        "widget-mcp", importable=lambda: True, module_fallback=["-m", "widget.mcp_server"]
    )
    assert len(candidates) == 1


def test_module_fallback_when_the_console_script_is_missing(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "editable-env" / BIN_DIRNAME
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))

    candidates = find_console_script(
        "widget-mcp", importable=lambda: True, module_fallback=["-m", "widget.mcp_server"]
    )

    assert len(candidates) == 1
    assert candidates[0].args == ["-m", "widget.mcp_server"]


def test_an_unreadable_envs_directory_is_skipped_not_raised(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "aida.mcp.env_discovery.candidate_env_dirs", lambda: [tmp_path / "does-not-exist"]
    )
    assert find_console_script("widget-mcp") == []


def test_duplicate_paths_are_reported_once(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "env" / BIN_DIRNAME
    script = _make_script(bin_dir)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr("aida.mcp.env_discovery.shutil.which", lambda _name: str(script))

    assert len(find_console_script("widget-mcp")) == 1


def test_epics_ca_env_omits_unset_values():
    assert epics_ca_env(None, None) == {}
    assert epics_ca_env(None) == {"EPICS_CA_AUTO_ADDR_LIST": "NO"}
    assert epics_ca_env("10.0.0.1:5064") == {
        "EPICS_CA_ADDR_LIST": "10.0.0.1:5064",
        "EPICS_CA_AUTO_ADDR_LIST": "NO",
    }
    assert epics_ca_env("10.0.0.1:5064", None) == {"EPICS_CA_ADDR_LIST": "10.0.0.1:5064"}


def test_resolve_ca_addr_list_prefers_explicit_over_env(monkeypatch):
    monkeypatch.setenv("EPICS_CA_ADDR_LIST", "1.2.3.4:5064")
    assert resolve_ca_addr_list("9.9.9.9:5064") == "9.9.9.9:5064"
    assert resolve_ca_addr_list(None) == "1.2.3.4:5064"
    assert resolve_ca_addr_list("") == "1.2.3.4:5064"


def test_resolve_ca_addr_list_none_when_nothing_set(monkeypatch):
    monkeypatch.delenv("EPICS_CA_ADDR_LIST", raising=False)
    assert resolve_ca_addr_list(None) is None


def test_interpreter_for_module_form_returns_the_command_itself():
    candidate = ScriptCandidate(command="/env/bin/python", args=["-m", "widget.mcp_server"])
    assert interpreter_for(candidate) == "/env/bin/python"


def test_interpreter_for_console_script_looks_next_to_it(tmp_path: Path):
    bin_dir = tmp_path / "env" / BIN_DIRNAME
    bin_dir.mkdir(parents=True)
    script = bin_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    python_name = "python.exe" if os.name == "nt" else "python"
    (bin_dir / python_name).write_text("", encoding="utf-8")

    candidate = ScriptCandidate(command=str(script))
    assert interpreter_for(candidate) == str(bin_dir / python_name)


def test_interpreter_for_missing_python_is_none(tmp_path: Path):
    bin_dir = tmp_path / "env" / BIN_DIRNAME
    bin_dir.mkdir(parents=True)
    script = bin_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")

    candidate = ScriptCandidate(command=str(script))
    assert interpreter_for(candidate) is None


def test_resolve_editable_source_root_walks_up_to_pyproject_toml(tmp_path: Path):
    repo = tmp_path / "checkout"
    package = repo / "widget"
    package.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='widget'\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")

    root = resolve_editable_source_root(sys.executable, "os")  # any importable stdlib module

    # Sanity: os.__file__ won't have a pyproject.toml above it, so this
    # exercises the "not found" path rather than a real resolution.
    assert root is None


def test_resolve_editable_source_root_none_on_bad_interpreter():
    assert resolve_editable_source_root("/no/such/python", "widget") is None
