"""Tests for aida.mcp.epics_mcp_setup — detection, config-building (one
candidate -> up to two server configs), and the policy/catalog file install
that epics-mcp itself doesn't provide.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aida.mcp.env_discovery import ScriptCandidate
from aida.mcp.epics_mcp_setup import (
    STAFF_SERVER_NAME,
    USER_SERVER_NAME,
    epics_mcp_server_configs,
    find_epics_mcp,
    find_epics_mcp_examples_dir,
    install_epics_mcp_policies,
)

SCRIPT_NAME = "epics-mcp.exe" if os.name == "nt" else "epics-mcp"
BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


@pytest.fixture(autouse=True)
def _no_real_installations(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aida.mcp.env_discovery.shutil.which", lambda _name: None)
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", list)
    fake_bin = tmp_path / "empty-env" / BIN_DIRNAME
    fake_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))


def test_no_installation_found_is_an_empty_list_not_an_error():
    assert find_epics_mcp() == []


def test_finds_a_sibling_conda_environment(monkeypatch, tmp_path: Path):
    envs = tmp_path / ".conda" / "envs"
    script_dir = envs / "epics-mcp" / BIN_DIRNAME
    script_dir.mkdir(parents=True)
    script = script_dir / SCRIPT_NAME
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("aida.mcp.env_discovery.candidate_env_dirs", lambda: [envs])

    candidates = find_epics_mcp()
    assert [c.command for c in candidates] == [str(script)]


def test_default_configs_are_read_only_only():
    configs = epics_mcp_server_configs(
        ScriptCandidate(command="/opt/envs/epics-mcp/bin/epics-mcp")
    )
    assert set(configs) == {USER_SERVER_NAME}
    user = configs[USER_SERVER_NAME]
    assert user.args == ["--policy", "usaxs-user"]
    assert user.groups == ["instrument-status"]
    assert user.confirm_tools == []


def test_include_staff_adds_the_write_capable_server():
    configs = epics_mcp_server_configs(
        ScriptCandidate(command="epics-mcp"), include_staff=True
    )
    assert set(configs) == {USER_SERVER_NAME, STAFF_SERVER_NAME}
    staff = configs[STAFF_SERVER_NAME]
    assert staff.args == ["--policy", "usaxs-staff"]
    assert staff.groups == ["instrument-staff"]
    assert staff.confirm_tools == ["epics_pv_put"]


def test_env_vars_applied_to_both_configs():
    configs = epics_mcp_server_configs(
        ScriptCandidate(command="epics-mcp"),
        ca_addr_list="10.54.122.63:16661",
        include_staff=True,
    )
    for config in configs.values():
        assert config.env["EPICS_CA_ADDR_LIST"] == "10.54.122.63:16661"


def test_find_examples_dir_none_when_interpreter_cannot_be_found():
    candidate = ScriptCandidate(command="/does/not/exist/epics-mcp")
    assert find_epics_mcp_examples_dir(candidate) is None


# --- install_epics_mcp_policies ------------------------------------------


def _make_examples_dir(tmp_path: Path) -> Path:
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "policy_usaxs_readonly.yaml").write_text("mode: read-only\n", encoding="utf-8")
    (examples / "policy_usaxs_staff.yaml").write_text("mode: read-write\n", encoding="utf-8")
    (examples / "pv_catalog_usaxs.txt").write_text("usxLAX:m1 a motor\n", encoding="utf-8")
    return examples


def test_install_read_only_policy_and_catalog(tmp_path: Path):
    examples = _make_examples_dir(tmp_path)
    policy_dir = tmp_path / "policies"

    installed = install_epics_mcp_policies(examples, policy_dir=policy_dir)

    assert installed == ["usaxs-user"]
    assert (policy_dir / "usaxs-user.yaml").read_text(encoding="utf-8") == "mode: read-only\n"
    assert (policy_dir / "pv_catalog_usaxs.txt").is_file()
    assert not (policy_dir / "usaxs-staff.yaml").exists()


def test_install_staff_policy_when_requested(tmp_path: Path):
    examples = _make_examples_dir(tmp_path)
    policy_dir = tmp_path / "policies"

    installed = install_epics_mcp_policies(examples, include_staff=True, policy_dir=policy_dir)

    assert set(installed) == {"usaxs-user", "usaxs-staff"}
    assert (policy_dir / "usaxs-staff.yaml").read_text(encoding="utf-8") == "mode: read-write\n"
    assert (policy_dir / "pv_catalog_usaxs.txt").is_file()


def test_install_never_overwrites_an_existing_policy(tmp_path: Path):
    examples = _make_examples_dir(tmp_path)
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir(parents=True)
    (policy_dir / "usaxs-user.yaml").write_text("mode: read-only\ntuned: true\n", encoding="utf-8")

    installed = install_epics_mcp_policies(examples, policy_dir=policy_dir)

    assert installed == []
    assert "tuned: true" in (policy_dir / "usaxs-user.yaml").read_text(encoding="utf-8")


def test_install_defaults_to_home_epics_mcp_policies(monkeypatch, tmp_path: Path):
    examples = _make_examples_dir(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    install_epics_mcp_policies(examples)

    assert (fake_home / ".epics-mcp" / "policies" / "usaxs-user.yaml").is_file()
