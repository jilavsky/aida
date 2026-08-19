"""Widgets for the two artifact events AIDA's agent loop emits
(``aida.core.events.ImageArtifactCreated``/``FileArtifactCreated``) —
PLAN.md Phase 5: "Inline images: scaled inline pixmap; click -> full-size
viewer; context menu: Save As / copy / Reveal in file manager" and "File
artifacts shown as cards with Open / Reveal actions".

Every action a context menu/button can trigger (save, copy, open, reveal)
is also a plain method, deliberately, so tests can call it directly instead
of simulating a real mouse click + modal file dialog.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from aida.ui.qt._qt import (
    QDesktopServices,
    QDialog,
    QFileDialog,
    QFrame,
    QGuiApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPixmap,
    QPushButton,
    Qt,
    QUrl,
    QVBoxLayout,
    QWidget,
)

INLINE_MAX_WIDTH = 480


def _reveal_in_file_manager(path: Path) -> None:
    """"Reveal" == open the containing folder — the same thing every OS's
    file manager understands via a plain ``file://`` URL; there's no
    cross-platform "select this specific file" API without a native shell
    call per OS, which is more than this phase needs."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class FullSizeImageDialog(QDialog):
    """A plain, unscaled view of the image — what clicking an inline
    thumbnail opens."""

    def __init__(self, pixmap: QPixmap, title: str = "Image", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        label = QLabel(self)
        label.setPixmap(pixmap)
        layout.addWidget(label)


class InlineImageWidget(QFrame):
    """A scaled-down inline thumbnail for one ``ImageArtifactCreated``
    event. Click opens a full-size viewer; right-click offers Save As /
    Copy / Reveal in file manager."""

    def __init__(
        self,
        *,
        path: str,
        artifact_id: str,
        mime_type: str,
        max_width: int = INLINE_MAX_WIDTH,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.artifact_id = artifact_id
        self.mime_type = mime_type
        self._full_pixmap = QPixmap(path)

        layout = QVBoxLayout(self)
        self._image_label = QLabel(self)
        if not self._full_pixmap.isNull() and self._full_pixmap.width() > max_width:
            scaled = self._full_pixmap.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
        else:
            scaled = self._full_pixmap
        self._image_label.setPixmap(scaled)
        self._image_label.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._image_label)

        caption = QLabel(Path(path).name, self)
        caption.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(caption)

    @property
    def is_valid_image(self) -> bool:
        return not self._full_pixmap.isNull()

    # --- actions (each callable directly, independent of any real click) --

    def show_full_size(self) -> FullSizeImageDialog:
        dialog = FullSizeImageDialog(self._full_pixmap, title=Path(self.path).name, parent=self)
        dialog.show()
        return dialog

    def save_as(self, dest_path: str | None = None) -> str | None:
        """Copy the artifact file to ``dest_path`` (prompting via a native
        Save dialog if not given). Returns the destination path, or
        ``None`` if the dialog was cancelled."""
        if dest_path is None:
            dest_path, _ = QFileDialog.getSaveFileName(self, "Save Image As", Path(self.path).name)
            if not dest_path:
                return None
        shutil.copy2(self.path, dest_path)
        return dest_path

    def copy_to_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setPixmap(self._full_pixmap)

    def reveal_in_file_manager(self) -> None:
        _reveal_in_file_manager(Path(self.path))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.show_full_size()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        menu = QMenu(self)
        menu.addAction("Save As…", self.save_as)
        menu.addAction("Copy", self.copy_to_clipboard)
        menu.addAction("Reveal in File Manager", self.reveal_in_file_manager)
        menu.exec(event.globalPos())


class FileArtifactCard(QFrame):
    """A card for one ``FileArtifactCreated`` event: filename, Open, and
    Reveal actions."""

    def __init__(self, *, path: str, artifact_id: str, mime_type: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.artifact_id = artifact_id
        self.mime_type = mime_type
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        name_label = QLabel(Path(path).name, self)
        layout.addWidget(name_label)

        open_button = QPushButton("Open", self)
        open_button.clicked.connect(self.open_file)
        layout.addWidget(open_button)

        reveal_button = QPushButton("Reveal", self)
        reveal_button.clicked.connect(self.reveal_in_file_manager)
        layout.addWidget(reveal_button)

    def open_file(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def reveal_in_file_manager(self) -> None:
        _reveal_in_file_manager(Path(self.path))


__all__ = ["FileArtifactCard", "FullSizeImageDialog", "InlineImageWidget"]
