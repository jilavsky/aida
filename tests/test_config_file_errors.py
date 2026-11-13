"""Malformed config files give a ``ConfigFileError`` naming the file (and,
for syntax errors, the line) instead of a parser traceback or an
``AttributeError`` from deep inside a ``from_dict`` (review B5/T5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.config.settings import ConfigFileError, load_settings, load_workflow


@pytest.mark.parametrize(
    ("filename", "content", "line"),
    [
        ("config.yaml", "key: [unclosed\n", 2),
        ("config.yaml", "- a\n- b\n", None),
        ("workspaces.yaml", "workspaces: {w: null}\n", None),
        ("workspaces.yaml", "workspaces: hello\n", None),
        ("providers.yaml", "profiles: [a, b]\n", None),
        ("mcp.json", '{"mcpServers": \n', 2),
        ("mcp.json", "[1, 2]\n", None),
    ],
)
def test_malformed_config_file_names_the_file(tmp_path: Path, filename, content, line):
    (tmp_path / filename).write_text(content, encoding="utf-8")
    with pytest.raises(ConfigFileError) as info:
        load_settings(tmp_path)
    assert info.value.path == tmp_path / filename
    assert filename in str(info.value)
    assert info.value.line == line


def test_empty_config_files_still_load_as_defaults(tmp_path: Path):
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    (tmp_path / "mcp.json").write_text("{}", encoding="utf-8")
    load_settings(tmp_path)


def test_malformed_workflow_file_is_a_config_file_error(tmp_path: Path):
    (tmp_path / "broken.yaml").write_text("steps: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigFileError, match="broken.yaml"):
        load_workflow("broken", tmp_path)
