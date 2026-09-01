"""``CollapsibleSection``: a titled header that shows or hides one widget.

User request: "we need to make the right tab vertically scrollable, so user
can actually get to the content. Maybe better, we could make the different
subwindows in the right panel collapsible — have them collapse to small
vertical size and open only if user wants to change the content."

Both, in the end: ``MainWindow`` puts each session panel in one of these
*and* wraps the whole column in a scroll area. Collapsing is what makes the
column usable day to day (four stacked panels do not fit a laptop window);
scrolling is what guarantees content is always reachable even with
everything expanded.

The wrapped widget keeps its own identity — ``MainWindow.quick_tasks_panel``
is still the panel, not the section around it — so nothing else has to know
this exists. The one adjustment is that a wrapped ``QGroupBox`` has its own
title cleared: the section header already shows it, and two titles stacked
on one panel reads as a bug.

Collapsed state is plain data (``is_collapsed`` / ``set_collapsed``) that
``MainWindow`` persists in ``AppConfig.collapsed_panels``; this widget never
touches config itself, like every other widget in ``aida.ui.qt``.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QGroupBox,
    QSizePolicy,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
    Signal,
)


class CollapsibleSection(QWidget):
    """One collapsible panel: a click-to-toggle header plus its content."""

    toggled = Signal(str, bool)  # (title, is_collapsed)

    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._content = content
        if isinstance(content, QGroupBox):
            # The header below already says it — see the module docstring.
            content.setTitle("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QToolButton(self)
        self._header.setText(title)
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.DownArrow)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setStyleSheet("QToolButton { border: none; font-weight: bold; padding: 4px 2px; }")
        self._header.clicked.connect(self._on_header_clicked)
        layout.addWidget(self._header)

        content.setParent(self)
        layout.addWidget(content)

    # --- state ------------------------------------------------------------

    @property
    def title(self) -> str:
        return self._title

    @property
    def content(self) -> QWidget:
        return self._content

    @property
    def is_collapsed(self) -> bool:
        return not self._header.isChecked()

    def set_collapsed(self, collapsed: bool, *, notify: bool = False) -> None:
        """Collapse or expand. ``notify=False`` (the default) is for
        *restoring* saved state at construction time — emitting ``toggled``
        then would write the state straight back to config for every panel
        on every launch, which is noise at best and a lost setting at worst
        if two panels' restores interleave with a save."""
        if collapsed == self.is_collapsed:
            return
        self._header.setChecked(not collapsed)
        self._apply()
        if notify:
            self.toggled.emit(self._title, collapsed)

    def _on_header_clicked(self) -> None:
        self._apply()
        self.toggled.emit(self._title, self.is_collapsed)

    def _apply(self) -> None:
        expanded = self._header.isChecked()
        self._content.setVisible(expanded)
        self._header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


__all__ = ["CollapsibleSection"]
