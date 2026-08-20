"""``RetrievalRow`` (Phase 8, planning/phase08_rag.md): the retrieval-
transparency row — "event carries retrieval info so the GUI can show 'used
these passages'". Same collapsed-summary/expandable-details interaction
idiom as ``aida.ui.qt.tool_call_widget.ToolCallRow``, built once per
``aida.core.events.RetrievalPerformed`` (there is no "finished" half to
wait for — retrieval already happened by the time the event is emitted).
"""

from __future__ import annotations

from pathlib import Path

from aida.ui.qt._qt import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget


def _format_details(passages_by_kb: dict[str, list[dict]]) -> str:
    lines: list[str] = []
    for kb_name, passages in passages_by_kb.items():
        lines.append(f"From knowledge base: {kb_name}")
        for passage in passages:
            heading = passage.get("heading")
            heading_suffix = f" — {heading}" if heading else ""
            source = Path(passage.get("source_path", "")).name
            score = passage.get("score", 0.0)
            lines.append(f"  [{source}{heading_suffix}] (score {score:.2f})")
            lines.append(f"    {passage.get('text', '')}")
        lines.append("")
    return "\n".join(lines).rstrip()


class RetrievalRow(QFrame):
    """Starts collapsed, showing ``📚 Retrieved N passage(s) from M
    knowledge base(s)``; the expand button reveals every passage's source
    file, heading, score, and text."""

    def __init__(self, *, passages_by_kb: dict[str, list[dict]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.passages_by_kb = passages_by_kb
        self._expanded = False
        self.setFrameShape(QFrame.Shape.StyledPanel)

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        header.addWidget(self._summary_label, stretch=1)
        self._toggle_button = QPushButton("Details", self)
        self._toggle_button.clicked.connect(self.toggle_expanded)
        header.addWidget(self._toggle_button)
        outer.addLayout(header)

        self._detail_text = QTextEdit(self)
        self._detail_text.setReadOnly(True)
        self._detail_text.setPlainText(_format_details(passages_by_kb))
        self._detail_text.hide()
        outer.addWidget(self._detail_text)

        passage_count = sum(len(passages) for passages in passages_by_kb.values())
        kb_count = len(passages_by_kb)
        self._summary_label.setText(f"📚 Retrieved {passage_count} passage(s) from {kb_count} knowledge base(s)")

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._detail_text.setVisible(self._expanded)

    @property
    def is_expanded(self) -> bool:
        return self._expanded


__all__ = ["RetrievalRow"]
