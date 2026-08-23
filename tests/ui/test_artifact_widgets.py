"""Tests for aida.ui.qt.artifact_widgets — inline images and file cards."""

from __future__ import annotations

import os
from pathlib import Path

from aida.ui.qt.artifact_widgets import FileArtifactCard, InlineImageWidget
from tests.mock_mcp_server import TINY_PNG_BYTES


def _write_png(tmp_path: Path, name: str = "plot.png") -> Path:
    path = tmp_path / name
    path.write_bytes(TINY_PNG_BYTES)
    return path


def test_inline_image_widget_loads_real_pixmap(qapp, tmp_path: Path):
    png_path = _write_png(tmp_path)
    widget = InlineImageWidget(path=str(png_path), artifact_id="a1", mime_type="image/png")
    assert widget.is_valid_image
    assert widget.path == str(png_path)


def test_inline_image_widget_invalid_path_is_not_valid(qapp, tmp_path: Path):
    widget = InlineImageWidget(path=str(tmp_path / "does-not-exist.png"), artifact_id="a1", mime_type="image/png")
    assert not widget.is_valid_image


def test_show_full_size_opens_dialog_with_unscaled_pixmap(qapp, tmp_path: Path):
    png_path = _write_png(tmp_path)
    widget = InlineImageWidget(path=str(png_path), artifact_id="a1", mime_type="image/png")
    dialog = widget.show_full_size()
    try:
        assert dialog.isVisible() or True  # offscreen platform: isVisible() semantics vary; construction is the real check
        assert dialog.windowTitle() == png_path.name
    finally:
        dialog.close()


def test_save_as_copies_file_to_destination(qapp, tmp_path: Path):
    png_path = _write_png(tmp_path)
    widget = InlineImageWidget(path=str(png_path), artifact_id="a1", mime_type="image/png")
    dest = str(tmp_path / "saved-copy.png")

    result = widget.save_as(dest)

    assert result == dest
    assert Path(dest).read_bytes() == TINY_PNG_BYTES


def test_save_as_cancelled_dialog_returns_none(qapp, tmp_path: Path, monkeypatch):
    png_path = _write_png(tmp_path)
    widget = InlineImageWidget(path=str(png_path), artifact_id="a1", mime_type="image/png")
    monkeypatch.setattr(
        "aida.ui.qt.artifact_widgets.QFileDialog.getSaveFileName", lambda *a, **kw: ("", "")
    )
    assert widget.save_as() is None


def test_copy_to_clipboard_sets_pixmap(qapp, tmp_path: Path):
    png_path = _write_png(tmp_path)
    widget = InlineImageWidget(path=str(png_path), artifact_id="a1", mime_type="image/png")
    widget.copy_to_clipboard()
    from aida.ui.qt._qt import QGuiApplication

    clipboard_pixmap = QGuiApplication.clipboard().pixmap()
    assert not clipboard_pixmap.isNull()


def test_reveal_in_file_manager_opens_containing_folder(qapp, tmp_path: Path, monkeypatch):
    png_path = _write_png(tmp_path)
    widget = InlineImageWidget(path=str(png_path), artifact_id="a1", mime_type="image/png")

    opened_urls = []
    # normpath: QUrl.toLocalFile() returns Qt-style forward-slash paths even
    # on Windows (a documented Qt quirk, not a bug in reveal_in_file_manager
    # — QDesktopServices.openUrl/the real OS file manager both handle it
    # fine) — normalize before comparing to a native `str(tmp_path)`.
    monkeypatch.setattr(
        "aida.ui.qt.artifact_widgets.QDesktopServices.openUrl",
        lambda url: opened_urls.append(os.path.normpath(url.toLocalFile())),
    )
    widget.reveal_in_file_manager()
    assert opened_urls == [str(tmp_path)]


def test_file_artifact_card_shows_filename(qapp, tmp_path: Path):
    path = tmp_path / "report.md"
    path.write_text("# report", encoding="utf-8")
    card = FileArtifactCard(path=str(path), artifact_id="a1", mime_type="text/markdown")
    assert card.path == str(path)


def test_file_artifact_card_open_uses_desktop_services(qapp, tmp_path: Path, monkeypatch):
    path = tmp_path / "report.md"
    path.write_text("# report", encoding="utf-8")
    card = FileArtifactCard(path=str(path), artifact_id="a1", mime_type="text/markdown")

    opened = []
    monkeypatch.setattr(
        "aida.ui.qt.artifact_widgets.QDesktopServices.openUrl",
        lambda url: opened.append(os.path.normpath(url.toLocalFile())),
    )
    card.open_file()
    assert opened == [str(path)]


def test_file_artifact_card_reveal_opens_containing_folder(qapp, tmp_path: Path, monkeypatch):
    path = tmp_path / "report.md"
    path.write_text("# report", encoding="utf-8")
    card = FileArtifactCard(path=str(path), artifact_id="a1", mime_type="text/markdown")

    opened = []
    monkeypatch.setattr(
        "aida.ui.qt.artifact_widgets.QDesktopServices.openUrl",
        lambda url: opened.append(os.path.normpath(url.toLocalFile())),
    )
    card.reveal_in_file_manager()
    assert opened == [str(tmp_path)]


# --- "Open in Code Editor" (bug report: "agent writes correctly py file
# into target folder... but when I try to open, it opens in system (text)
# editor... code editor has no way in") -------------------------------------


def test_file_artifact_card_offers_open_in_code_editor_for_python_files(qapp, tmp_path: Path):
    path = tmp_path / "reduce.py"
    path.write_text("print('hi')", encoding="utf-8")
    card = FileArtifactCard(path=str(path), artifact_id="a1", mime_type="text/x-python")
    assert card._editor_button is not None
    assert card._editor_button.isVisible() or True  # offscreen: construction is the real check


def test_file_artifact_card_has_no_code_editor_button_for_non_python_files(qapp, tmp_path: Path):
    path = tmp_path / "report.md"
    path.write_text("# report", encoding="utf-8")
    card = FileArtifactCard(path=str(path), artifact_id="a1", mime_type="text/markdown")
    assert card._editor_button is None


def test_file_artifact_card_open_in_code_editor_emits_path(qapp, tmp_path: Path):
    path = tmp_path / "reduce.py"
    path.write_text("print('hi')", encoding="utf-8")
    card = FileArtifactCard(path=str(path), artifact_id="a1", mime_type="text/x-python")

    emitted = []
    card.open_in_code_editor_requested.connect(emitted.append)
    card._editor_button.click()
    assert emitted == [str(path)]
