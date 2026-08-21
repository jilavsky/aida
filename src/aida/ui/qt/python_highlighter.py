"""``PythonHighlighter`` (Phase 9): a small, regex-based Python syntax
highlighter for the code editor's ``QPlainTextEdit`` — not a full
tokenizer (PLAN.md §8: "Qt plain-text editor + syntax highlighting;
consider QScintilla only if genuinely needed" — this is the "first" half
of that, plain enough that QScintilla hasn't proven necessary). First use
of ``QSyntaxHighlighter``/``QTextCharFormat`` anywhere in the repo — both
added to ``aida.ui.qt._qt``'s shim for this.
"""

from __future__ import annotations

import keyword
import re

from aida.ui.qt._qt import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

_KEYWORD_RE = re.compile(r"\b(" + "|".join(keyword.kwlist) + r")\b")
_STRING_RE = re.compile(r"(\"\"\".*?\"\"\"|'''.*?'''|\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|'[^'\\\n]*(?:\\.[^'\\\n]*)*')", re.DOTALL)
_COMMENT_RE = re.compile(r"#[^\n]*")
_NUMBER_RE = re.compile(r"\b\d+(\.\d+)?\b")
_DEF_CLASS_RE = re.compile(r"\b(def|class)\s+(\w+)")


def _format(color: str, *, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


class PythonHighlighter(QSyntaxHighlighter):
    """Highlights keywords, strings, comments, numbers, and def/class
    names. Applied per-block (Qt calls ``highlightBlock`` for each line as
    it's edited) — multi-line triple-quoted strings work because the regex
    itself is ``DOTALL`` and Qt re-highlights affected blocks when a
    preceding block's content changes."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._keyword_format = _format("#569CD6", bold=True)
        self._string_format = _format("#CE9178")
        self._comment_format = _format("#6A9955")
        self._number_format = _format("#B5CEA8")
        self._def_class_format = _format("#4EC9B0", bold=True)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - QSyntaxHighlighter's own override name
        for match in _KEYWORD_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._keyword_format)
        for match in _NUMBER_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._number_format)
        for match in _DEF_CLASS_RE.finditer(text):
            self.setFormat(match.start(2), match.end(2) - match.start(2), self._def_class_format)
        for match in _STRING_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._string_format)
        for match in _COMMENT_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._comment_format)


__all__ = ["PythonHighlighter"]
