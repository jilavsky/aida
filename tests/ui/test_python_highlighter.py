"""Spot checks for aida.ui.qt.python_highlighter.PythonHighlighter — not
exhaustive (it's a regex highlighter, not a real tokenizer), just "this
token gets some non-default format" checks."""

from __future__ import annotations

from aida.ui.qt._qt import QPlainTextEdit
from aida.ui.qt.python_highlighter import PythonHighlighter


def _properties_at(editor: QPlainTextEdit, offset: int) -> tuple[int, str]:
    """Returns (font_weight, foreground_color_name) at ``offset`` — read
    out as plain Python values *inside* the format-range loop, since the
    QTextCharFormat a QTextLayout.FormatRange.format hands back is a
    shiboken temporary that gets invalidated once the range object itself
    goes out of scope (a real PySide6 lifetime quirk, not a bug in the
    highlighter under test)."""
    block = editor.document().findBlock(offset)
    layout = block.layout()
    local_offset = offset - block.position()
    for fmt_range in layout.formats():
        if fmt_range.start <= local_offset < fmt_range.start + fmt_range.length:
            fmt = fmt_range.format
            return fmt.fontWeight(), fmt.foreground().color().name()
    return 0, "#000000"


def test_keyword_gets_highlighted(qapp):
    editor = QPlainTextEdit()
    PythonHighlighter(editor.document())
    editor.setPlainText("def f():\n    return 1\n")
    offset = editor.toPlainText().index("def")
    weight, _color = _properties_at(editor, offset)
    assert weight == 700  # bold — keywords are bold in _format()


def test_string_gets_a_distinct_color_from_default(qapp):
    editor = QPlainTextEdit()
    PythonHighlighter(editor.document())
    editor.setPlainText('x = "hello"\n')
    offset = editor.toPlainText().index('"hello"')
    _weight, color = _properties_at(editor, offset)
    assert color != "#000000"


def test_comment_gets_a_distinct_format(qapp):
    editor = QPlainTextEdit()
    PythonHighlighter(editor.document())
    editor.setPlainText("x = 1  # a comment\n")
    offset = editor.toPlainText().index("# a comment")
    _weight, color = _properties_at(editor, offset)
    assert color != "#000000"


def test_plain_identifier_is_not_highlighted(qapp):
    editor = QPlainTextEdit()
    PythonHighlighter(editor.document())
    editor.setPlainText("some_variable = 1\n")
    offset = editor.toPlainText().index("some_variable")
    weight, _color = _properties_at(editor, offset)
    assert weight != 700
