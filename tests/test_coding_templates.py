from __future__ import annotations

from pathlib import Path

from aida.coding.templates import load_templates, templates_context_text


def test_load_templates_reads_py_files_with_docstrings(tmp_path: Path):
    (tmp_path / "move_sample.py").write_text(
        '"""Move the sample stage to a named position."""\n\ndef move_sample():\n    pass\n',
        encoding="utf-8",
    )
    templates = load_templates(tmp_path)
    assert len(templates) == 1
    assert templates[0].name == "move_sample"
    assert templates[0].docstring == "Move the sample stage to a named position."
    assert "def move_sample" in templates[0].source


def test_load_templates_ignores_non_python_files(tmp_path: Path):
    (tmp_path / "readme.md").write_text("not a template", encoding="utf-8")
    assert load_templates(tmp_path) == []


def test_load_templates_is_not_recursive(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text('"""Nested."""\n', encoding="utf-8")
    assert load_templates(tmp_path) == []


def test_load_templates_skips_a_file_with_a_syntax_error(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def (((", encoding="utf-8")
    (tmp_path / "good.py").write_text('"""Good template."""\n', encoding="utf-8")
    templates = load_templates(tmp_path)
    assert [t.name for t in templates] == ["good"]


def test_load_templates_handles_missing_directory(tmp_path: Path):
    assert load_templates(tmp_path / "does-not-exist") == []


def test_load_templates_handles_a_file_with_no_docstring(tmp_path: Path):
    (tmp_path / "no_doc.py").write_text("def f():\n    pass\n", encoding="utf-8")
    templates = load_templates(tmp_path)
    assert templates[0].docstring is None


def test_templates_context_text_empty_for_no_templates():
    assert templates_context_text([]) == ""


def test_templates_context_text_includes_name_and_docstring(tmp_path: Path):
    (tmp_path / "move_sample.py").write_text('"""Move the sample stage."""\n', encoding="utf-8")
    templates = load_templates(tmp_path)
    text = templates_context_text(templates)
    assert "move_sample" in text
    assert "Move the sample stage." in text


def test_templates_context_text_handles_missing_docstring(tmp_path: Path):
    (tmp_path / "no_doc.py").write_text("x = 1\n", encoding="utf-8")
    templates = load_templates(tmp_path)
    text = templates_context_text(templates)
    assert "no_doc" in text
    assert "(no docstring)" in text
