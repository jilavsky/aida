"""``PageTestDialog``: the result of Settings → Documents → "Test on a page".

Shows the rendered page beside the Markdown the model returned for it, raw
and as a preview, with which model did it and how long it took, so a user
can judge an OCR or vision profile without the command line. The figures a
chat would extract from the page are listed in their own tab and, when they
pair with the Markdown's image placeholders, shown in place in the preview. The conversion
itself runs in ``aida.documents.ocr.standalone.run_page_test``; this module
only displays a ``PageTestResult``.
"""

from __future__ import annotations

from aida.documents.ocr.common import IMAGE_PLACEHOLDER_RE
from aida.documents.ocr.standalone import PageTestResult
from aida.ui.qt._qt import (
    QDialog,
    QDialogButtonBox,
    QImage,
    QLabel,
    QPixmap,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    Qt,
    QTabWidget,
    QTextBrowser,
    QTextDocument,
    QUrl,
    QVBoxLayout,
    QWidget,
)

#: Width the page image is scaled to; it scrolls vertically.
PAGE_IMAGE_WIDTH_PX = 560

#: Widest a figure is shown, in the preview and the Figures tab.
FIGURE_WIDTH_PX = 480


def figures_note(result: PageTestResult) -> str:
    """What was extracted from the page and whether it pairs with the
    Markdown's placeholders, by the rule a chat uses."""
    count, placeholders = len(result.figures), len(result.placeholders)
    if not count:
        return (
            "No figures were extracted from this page."
            if not placeholders
            else f"The Markdown has {placeholders} image placeholder(s), but no figure was "
            "extracted from this page."
        )
    if any(f.placeholder for f in result.figures):
        return (
            f"{count} figure(s) extracted and paired in order with the image placeholders in "
            "the Markdown, as a chat would pair them. The preview shows them in place."
        )
    return (
        f"{count} figure(s) extracted, but the Markdown has {placeholders} image "
        "placeholder(s), so a chat keeps the extractor's own captions and does not pair them."
    )


def preview_markdown(markdown: str) -> str:
    """``markdown`` with each image placeholder in a paragraph of its own,
    so the caption on the next line is not run onto the image's line."""
    return IMAGE_PLACEHOLDER_RE.sub(lambda match: f"\n\n{match.group(0)}\n\n", markdown)


def _scaled(image: QImage) -> QImage:
    if image.width() > FIGURE_WIDTH_PX:
        return image.scaledToWidth(FIGURE_WIDTH_PX, Qt.TransformationMode.SmoothTransformation)
    return image


class _PreviewBrowser(QTextBrowser):
    """A Markdown preview that resolves image placeholders (``fig-1``) to
    the extracted figures they are paired with."""

    def __init__(self, images: dict[str, QImage], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._images = images

    def loadResource(self, kind: int, name: QUrl) -> object:  # noqa: N802 - Qt override
        image = self._images.get(name.toString())
        if image is not None and kind == QTextDocument.ResourceType.ImageResource.value:
            return image
        return super().loadResource(kind, name)


def summary_text(result: PageTestResult) -> str:
    """The one-line headline: what was converted, by which model, how fast."""
    where = f"{result.pdf.name}, page {result.page_index + 1}"
    conversion = result.conversion
    if result.markdown:
        return f"{where}: converted by {conversion.route_label} in {result.seconds:.1f} s."
    why = conversion.unavailable or " ".join(conversion.notes) or "no reason was given"
    return f"{where}: not converted. {why}"


class PageTestDialog(QDialog):
    def __init__(self, result: PageTestResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test on a Page")
        self.resize(1150, 780)
        layout = QVBoxLayout(self)

        self._summary_label = QLabel(summary_text(result), self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._summary_label)
        notes = [n for n in result.conversion.notes if result.markdown]
        if notes:
            notes_label = QLabel("\n".join(notes), self)
            notes_label.setWordWrap(True)
            layout.addWidget(notes_label)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._page_label = QLabel(self)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        pixmap = QPixmap()
        if result.page_image and pixmap.loadFromData(result.page_image):
            self._page_label.setPixmap(
                pixmap.scaledToWidth(
                    PAGE_IMAGE_WIDTH_PX, Qt.TransformationMode.SmoothTransformation
                )
            )
        else:
            self._page_label.setText("(the page could not be rendered)")
        page_scroll = QScrollArea(splitter)
        page_scroll.setWidget(self._page_label)
        page_scroll.setWidgetResizable(True)
        splitter.addWidget(page_scroll)

        self._tabs = QTabWidget(splitter)
        self._markdown_edit = QPlainTextEdit(result.markdown, self._tabs)
        self._markdown_edit.setReadOnly(True)
        self._tabs.addTab(self._markdown_edit, "Markdown")
        images: dict[str, QImage] = {}
        for figure in result.figures:
            image = QImage.fromData(figure.image)
            if figure.placeholder and not image.isNull():
                images[figure.placeholder] = _scaled(image)
        self._preview = _PreviewBrowser(images, self._tabs)
        self._preview.setMarkdown(preview_markdown(result.markdown))
        self._tabs.addTab(self._preview, "Preview")
        self._tabs.addTab(self._figures_tab(result), f"Figures ({len(result.figures)})")
        splitter.addWidget(self._tabs)
        splitter.setSizes([PAGE_IMAGE_WIDTH_PX + 30, 560])
        layout.addWidget(splitter, stretch=1)

        hint = QLabel(
            "Converted afresh, not from the cache. The preview shows LaTeX equations as text.",
            self,
        )
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _figures_tab(self, result: PageTestResult) -> QWidget:
        content = QWidget()
        column = QVBoxLayout(content)
        self._figures_note = QLabel(figures_note(result), content)
        self._figures_note.setWordWrap(True)
        column.addWidget(self._figures_note)
        for figure in result.figures:
            entry = figure.entry
            paired = f", placeholder {figure.placeholder}" if figure.placeholder else ""
            header = QLabel(
                f"<b>{entry.label}</b> — {entry.file} (caption confidence: "
                f"{entry.confidence}{paired})",
                content,
            )
            column.addWidget(header)
            picture = QLabel(content)
            image = QImage.fromData(figure.image)
            if image.isNull():
                picture.setText("(this image format cannot be shown here)")
            else:
                picture.setPixmap(QPixmap.fromImage(_scaled(image)))
            column.addWidget(picture)
            if entry.caption:
                caption = QLabel(entry.caption, content)
                caption.setWordWrap(True)
                caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                column.addWidget(caption)
        column.addStretch(1)
        scroll = QScrollArea(self._tabs)
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        return scroll

    def markdown(self) -> str:
        return self._markdown_edit.toPlainText()

    def figures_note(self) -> str:
        return self._figures_note.text()


__all__ = [
    "FIGURE_WIDTH_PX",
    "PAGE_IMAGE_WIDTH_PX",
    "PageTestDialog",
    "figures_note",
    "summary_text",
]
