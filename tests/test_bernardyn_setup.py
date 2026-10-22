"""Tests for aida.mcp.bernardyn_setup — detection of Bernardyn's MCP server
and the one-click config it produces.

``find_bernardyn_mcp`` is a thin wrapper around
``env_discovery.find_console_script`` (see tests/test_env_discovery.py for
the search logic); these tests cover what is specific to Bernardyn: the
server config shape, its two env vars, and the cross-environment ``PATH``
that keeps the subprocess able to load its own extension modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aida.mcp.bernardyn_setup import (
    DEFAULT_GROUPS,
    DEFAULT_SERVER_NAME,
    BernardynMcpCandidate,
    bernardyn_server_config,
    bernardyn_version,
    find_bernardyn_mcp,
)

SCRIPT_NAME = "bernardyn-mcp.exe" if os.name == "nt" else "bernardyn-mcp"
BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


@pytest.fixture(autouse=True)
def _no_real_installations(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aida.mcp.env_discovery.shutil.which", lambda _name: None)
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", list)
    fake_bin = tmp_path / "empty-env" / BIN_DIRNAME
    fake_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))


def test_no_installation_found_is_an_empty_list_not_an_error():
    assert find_bernardyn_mcp() == []


def test_finds_a_sibling_conda_environment(monkeypatch, tmp_path: Path):
    """The expected layout: Bernardyn in its own env, because its PySide6
    pin is not AIDA's and the two Qt builds must not share a process."""
    envs = tmp_path / "miniconda3" / "envs"
    script_dir = envs / "bernardyn" / BIN_DIRNAME
    script_dir.mkdir(parents=True)
    script = script_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", lambda: [envs])

    candidates = find_bernardyn_mcp()

    assert [c.command for c in candidates] == [str(script)]
    assert "bernardyn" in candidates[0].source


def test_server_config_defaults():
    config = bernardyn_server_config(
        BernardynMcpCandidate(command="/opt/envs/bernardyn/bin/bernardyn-mcp")
    )

    assert config.name == DEFAULT_SERVER_NAME
    assert config.command == "/opt/envs/bernardyn/bin/bernardyn-mcp"
    assert config.groups == list(DEFAULT_GROUPS)
    assert config.skills == []
    # No data/output root unless asked for — Bernardyn's own defaults apply.
    assert "BERNARDYN_DATA_ROOT" not in config.env
    assert "BERNARDYN_OUTPUT_ROOT" not in config.env


def test_server_config_is_in_the_pyirena_group_too():
    """Five tools is cheap enough that an analysis workspace gets plotting
    without a second configuration step — the deliberate choice documented
    in DEFAULT_GROUPS."""
    config = bernardyn_server_config(BernardynMcpCandidate(command="bernardyn-mcp"))
    assert "pyirena-analysis" in config.groups
    assert "bernardyn-plots" in config.groups


def test_server_config_sets_both_roots(tmp_path: Path):
    config = bernardyn_server_config(
        BernardynMcpCandidate(command="bernardyn-mcp"),
        data_root=str(tmp_path / "data"),
        output_root=str(tmp_path / "out"),
    )
    assert config.env["BERNARDYN_DATA_ROOT"] == str(tmp_path / "data")
    assert config.env["BERNARDYN_OUTPUT_ROOT"] == str(tmp_path / "out")


def test_server_config_expands_a_tilde_in_the_roots():
    config = bernardyn_server_config(
        BernardynMcpCandidate(command="bernardyn-mcp"),
        data_root="~/data",
        output_root="~/plots",
    )
    assert config.env["BERNARDYN_DATA_ROOT"] == str(Path.home() / "data")
    assert config.env["BERNARDYN_OUTPUT_ROOT"] == str(Path.home() / "plots")


def test_server_config_puts_the_candidates_environment_first_on_path(tmp_path: Path):
    """The cross-environment launch fix: without its own prefix on PATH, a
    Bernardyn installed in a separate conda env dies at import on Windows
    with exit 127 and no traceback (see env_discovery.launch_path)."""
    prefix = tmp_path / "envs" / "bernardyn"
    script = prefix / BIN_DIRNAME / SCRIPT_NAME
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")

    config = bernardyn_server_config(BernardynMcpCandidate(command=str(script)))

    assert config.env["PATH"].startswith(str(prefix / BIN_DIRNAME) + os.pathsep) or config.env[
        "PATH"
    ].startswith(str(prefix))


def test_server_config_omits_path_for_a_bare_command():
    """A command with no resolvable environment prefix gets no PATH key at
    all, rather than one that would shadow the inherited environment."""
    config = bernardyn_server_config(BernardynMcpCandidate(command="bernardyn-mcp"))
    assert "PATH" not in config.env


def test_server_config_custom_name_and_groups():
    config = bernardyn_server_config(
        BernardynMcpCandidate(command="bernardyn-mcp"), name="plots", groups=["figures"]
    )
    assert config.name == "plots"
    assert config.groups == ["figures"]


def test_version_is_none_when_the_interpreter_cannot_be_found():
    assert bernardyn_version(BernardynMcpCandidate(command="/does/not/exist/bernardyn-mcp")) is None
