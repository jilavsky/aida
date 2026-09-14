"""Tests for aida.mcp.aievaluator_setup — detection of aievaluator's MCP
server and the one-click config it produces.

``find_aievaluator_mcp`` itself is a thin wrapper around
``env_discovery.find_console_script`` (see tests/test_env_discovery.py for
the search logic); these tests cover what's specific to aievaluator: server
config shape, env resolution, and skills-directory discovery.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aida.mcp.aievaluator_setup import (
    DEFAULT_CONFIRM_TOOLS,
    DEFAULT_GROUPS,
    DEFAULT_SERVER_NAME,
    DEFAULT_SKILLS,
    AievaluatorMcpCandidate,
    aievaluator_server_config,
    find_aievaluator_mcp,
    find_aievaluator_skills_dir,
)

SCRIPT_NAME = "aievaluator-mcp.exe" if os.name == "nt" else "aievaluator-mcp"
BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


@pytest.fixture(autouse=True)
def _no_real_installations(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aida.mcp.env_discovery.shutil.which", lambda _name: None)
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", list)
    fake_bin = tmp_path / "empty-env" / BIN_DIRNAME
    fake_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))


def test_no_installation_found_is_an_empty_list_not_an_error():
    assert find_aievaluator_mcp() == []


def test_finds_a_sibling_conda_environment(monkeypatch, tmp_path: Path):
    """The beamline layout: ~/.conda/envs/aievaluator, which is exactly
    /home/beams/USAXS/.conda/envs/aievaluator on usaxscontrol."""
    envs = tmp_path / ".conda" / "envs"
    script_dir = envs / "aievaluator" / BIN_DIRNAME
    script_dir.mkdir(parents=True)
    script = script_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", lambda: [envs])

    candidates = find_aievaluator_mcp()

    assert [c.command for c in candidates] == [str(script)]
    assert "aievaluator" in candidates[0].source


def test_server_config_defaults():
    config = aievaluator_server_config(
        AievaluatorMcpCandidate(command="/opt/envs/aievaluator/bin/aievaluator-mcp")
    )

    assert config.name == DEFAULT_SERVER_NAME
    assert config.command == "/opt/envs/aievaluator/bin/aievaluator-mcp"
    assert config.groups == list(DEFAULT_GROUPS)
    assert config.skills == list(DEFAULT_SKILLS)
    assert config.confirm_tools == list(DEFAULT_CONFIRM_TOOLS)
    assert config.env == {"EPICS_CA_AUTO_ADDR_LIST": "NO"}


def test_server_config_sets_epics_addr_list():
    config = aievaluator_server_config(
        AievaluatorMcpCandidate(command="aievaluator-mcp"), ca_addr_list="10.54.122.63:16661"
    )
    assert config.env == {
        "EPICS_CA_ADDR_LIST": "10.54.122.63:16661",
        "EPICS_CA_AUTO_ADDR_LIST": "NO",
    }


def test_server_config_custom_name():
    config = aievaluator_server_config(
        AievaluatorMcpCandidate(command="aievaluator-mcp"), name="my-aievaluator"
    )
    assert config.name == "my-aievaluator"


def test_find_skills_dir_none_when_interpreter_cannot_be_found():
    candidate = AievaluatorMcpCandidate(command="/does/not/exist/aievaluator-mcp")
    assert find_aievaluator_skills_dir(candidate) is None


def test_find_skills_dir_resolves_via_editable_install(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "env" / BIN_DIRNAME
    bin_dir.mkdir(parents=True)
    script = bin_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    python_name = "python.exe" if os.name == "nt" else "python"
    (bin_dir / python_name).write_text("", encoding="utf-8")

    repo = tmp_path / "checkout"
    (repo / "skills").mkdir(parents=True)

    monkeypatch.setattr(
        "aida.mcp.aievaluator_setup.resolve_editable_source_root", lambda _python, _mod: repo
    )

    candidate = AievaluatorMcpCandidate(command=str(script))
    assert find_aievaluator_skills_dir(candidate) == repo / "skills"


def test_find_skills_dir_none_when_repo_root_has_no_skills_folder(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "env" / BIN_DIRNAME
    bin_dir.mkdir(parents=True)
    script = bin_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    python_name = "python.exe" if os.name == "nt" else "python"
    (bin_dir / python_name).write_text("", encoding="utf-8")

    repo = tmp_path / "checkout"
    repo.mkdir()

    monkeypatch.setattr(
        "aida.mcp.aievaluator_setup.resolve_editable_source_root", lambda _python, _mod: repo
    )

    candidate = AievaluatorMcpCandidate(command=str(script))
    assert find_aievaluator_skills_dir(candidate) is None
