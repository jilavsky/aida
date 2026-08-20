"""Smoke tests for aida.cli.__main__'s command dispatch table — makes sure
the Phase 4 ``conversations``/``workspace`` subcommands are actually wired
up (not just importable in isolation), by invoking the real top-level
``main()`` the ``aida`` console script uses."""

from __future__ import annotations

from pathlib import Path

from aida.cli.__main__ import _COMMANDS, main


def test_commands_table_includes_phase4_subcommands():
    assert "conversations" in _COMMANDS
    assert "workspace" in _COMMANDS


def test_commands_table_includes_phase7_mcp_subcommand():
    assert "mcp" in _COMMANDS


def test_top_level_help_lists_new_commands(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "conversations" in out
    assert "workspace" in out
    assert "mcp" in out


def test_dispatches_to_conversations_list(aida_home: Path, records_home: Path, capsys):
    rc = main(["conversations", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No conversations yet." in out


def test_dispatches_to_workspace_list(aida_home: Path, records_home: Path, capsys):
    rc = main(["workspace", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No workspaces configured." in out


def test_dispatches_to_mcp_server_list(aida_home: Path, records_home: Path, capsys):
    rc = main(["mcp", "server", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No MCP servers configured." in out


def test_unknown_command_reports_error(capsys):
    rc = main(["not-a-real-command"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Unknown command" in out
