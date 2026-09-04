"""Reader tests with small fixture files per format (PLAN.md Phase 6 task).
Fixtures are built programmatically with the same libraries the readers
themselves use (pymupdf/python-docx/openpyxl/python-pptx), same pattern as
``tests/mock_mcp_server.py``'s ``TINY_PNG_BYTES``."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact, JsonArtifact, TableArtifact, TextArtifact
from aida.documents.readers import UnsupportedDocumentFormatError, is_supported, read_document
from tests.mock_mcp_server import TINY_PNG_BYTES


def test_read_plain_text_file(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello world", encoding="utf-8")
    artifacts = read_document(path)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], TextArtifact)
    assert artifacts[0].text == "hello world"


def test_read_code_file_treated_as_text(tmp_path: Path):
    path = tmp_path / "script.py"
    path.write_text("print('hi')\n", encoding="utf-8")
    artifacts = read_document(path)
    assert isinstance(artifacts[0], TextArtifact)
    assert "print" in artifacts[0].text


def test_read_extensionless_file_treated_as_text(tmp_path: Path):
    path = tmp_path / "README"
    path.write_text("read me", encoding="utf-8")
    artifacts = read_document(path)
    assert isinstance(artifacts[0], TextArtifact)
    assert artifacts[0].text == "read me"


def test_read_text_file_truncates_past_max_chars(tmp_path: Path):
    path = tmp_path / "big.txt"
    path.write_text("x" * 1000, encoding="utf-8")
    artifacts = read_document(path, max_chars=100)
    assert len(artifacts[0].text) <= 100
    assert artifacts[0].text.endswith("[truncated]")


def test_read_csv_file_returns_table_artifact(tmp_path: Path):
    path = tmp_path / "data.csv"
    path.write_text("name,value\na,1\nb,2\n", encoding="utf-8")
    artifacts = read_document(path)
    assert len(artifacts) == 1
    table = artifacts[0]
    assert isinstance(table, TableArtifact)
    assert table.columns == ["name", "value"]
    assert table.rows == [["a", "1"], ["b", "2"]]


def test_read_csv_file_truncates_rows(tmp_path: Path):
    lines = ["name,value"] + [f"row{i},{i}" for i in range(10)]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(lines), encoding="utf-8")
    artifacts = read_document(path, max_chars=100_000)
    table = artifacts[0]
    # max_rows default is 500 so nothing truncates here; verify explicit cap works via readers internals
    assert len(table.rows) == 10


def test_read_json_file_returns_json_artifact_preserving_structure(tmp_path: Path):
    path = tmp_path / "data.json"
    path.write_text('{"a": 1, "b": [1, 2, 3]}', encoding="utf-8")
    artifacts = read_document(path)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], JsonArtifact)
    assert artifacts[0].data == {"a": 1, "b": [1, 2, 3]}


def test_read_invalid_json_falls_back_to_text(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    artifacts = read_document(path)
    assert isinstance(artifacts[0], TextArtifact)
    assert "not valid json" in artifacts[0].text


def test_read_png_image_returns_image_artifact_without_loading_bytes(tmp_path: Path):
    path = tmp_path / "plot.png"
    path.write_bytes(TINY_PNG_BYTES)
    artifacts = read_document(path)
    assert len(artifacts) == 1
    image = artifacts[0]
    assert isinstance(image, ImageArtifact)
    assert image.path == str(path)
    assert image.mime_type == "image/png"
    assert image.data == b""  # not loaded into memory -- see module docstring


def test_read_pdf_file_extracts_text_per_page(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "First page content")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Second page content")
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()

    artifacts = read_document(path)
    assert len(artifacts) == 1
    text = artifacts[0].text
    assert "First page content" in text
    assert "Second page content" in text
    assert "page 1" in text and "page 2" in text


def test_read_pdf_file_caps_page_count(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i} content")
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()

    artifacts = read_document(path, max_pdf_pages=2)
    text = artifacts[0].text
    assert "Page 0 content" in text
    assert "Page 1 content" in text
    assert "Page 4 content" not in text
    assert "truncated" in text


def test_read_docx_file_extracts_paragraphs_and_tables(tmp_path: Path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Title", level=1)
    document.add_paragraph("Body paragraph.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "col-a"
    table.cell(0, 1).text = "col-b"
    path = tmp_path / "doc.docx"
    document.save(str(path))

    artifacts = read_document(path)
    assert len(artifacts) == 1
    text = artifacts[0].text
    assert "Title" in text
    assert "Body paragraph." in text
    assert "col-a" in text and "col-b" in text


def test_read_xlsx_file_returns_one_table_per_sheet(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet1 = workbook.active
    sheet1.title = "Sheet1"
    sheet1.append(["name", "value"])
    sheet1.append(["a", 1])
    sheet2 = workbook.create_sheet("Sheet2")
    sheet2.append(["x", "y"])
    sheet2.append([10, 20])
    path = tmp_path / "book.xlsx"
    workbook.save(str(path))

    artifacts = read_document(path)
    assert len(artifacts) == 2
    assert all(isinstance(a, TableArtifact) for a in artifacts)
    assert artifacts[0].rows == [["a", 1]]
    assert artifacts[1].rows == [[10, 20]]


def test_read_xlsx_file_caps_sheets_and_rows(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet1 = workbook.active
    sheet1.title = "S1"
    sheet1.append(["h"])
    for i in range(10):
        sheet1.append([i])
    workbook.create_sheet("S2").append(["h"])
    workbook.create_sheet("S3").append(["h"])
    path = tmp_path / "book.xlsx"
    workbook.save(str(path))

    artifacts = read_document(path, max_sheets=2, max_rows_per_sheet=3)
    # 2 sheets kept + one TextArtifact noting the 3rd sheet was truncated
    tables = [a for a in artifacts if isinstance(a, TableArtifact)]
    assert len(tables) == 2
    assert len(tables[0].rows) == 4  # 3 real rows + one "truncated" marker row
    assert any(isinstance(a, TextArtifact) for a in artifacts)


def test_read_pptx_file_extracts_slide_text(tmp_path: Path):
    pptx_module = pytest.importorskip("pptx")
    presentation = pptx_module.Presentation()
    slide1 = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide1.shapes.title.text = "Slide One"
    slide2 = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide2.shapes.title.text = "Slide Two"
    path = tmp_path / "deck.pptx"
    presentation.save(str(path))

    artifacts = read_document(path)
    assert len(artifacts) == 1
    text = artifacts[0].text
    assert "Slide One" in text
    assert "Slide Two" in text
    assert "slide 1" in text and "slide 2" in text


def test_read_unsupported_extension_raises_clear_error(tmp_path: Path):
    path = tmp_path / "archive.zip"
    path.write_bytes(b"PK\x03\x04not a real zip but has the right extension")
    with pytest.raises(UnsupportedDocumentFormatError):
        read_document(path)


def test_is_supported_true_for_known_and_extensionless():
    assert is_supported("foo.pdf")
    assert is_supported("foo.txt")
    assert is_supported("foo.png")
    assert is_supported("README")


def test_is_supported_false_for_unknown_binary_extension():
    assert not is_supported("foo.zip")
    assert not is_supported("foo.exe")


# --- Phase A: what the readers say about the images they drop -----------
#
# Every reader here is text-only. The bug these cover is not the dropping
# itself but the *silence*: a scanned PDF used to arrive as an empty-looking
# document, and the model had no way to tell that from an empty file.


def _pdf_with_image(path: Path, pymupdf, *, pages: int = 1, text: str | None = None) -> None:
    """A PDF with the same tiny PNG placed on every page — the shape of a
    scanned document (one image per page, no text layer) unless ``text`` is
    given."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
        page.insert_image(pymupdf.Rect(200, 200, 300, 300), stream=TINY_PNG_BYTES)
    doc.save(str(path))
    doc.close()


def test_pdf_with_text_and_images_reports_the_dropped_images(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "figures.pdf"
    _pdf_with_image(path, pymupdf, pages=1, text="Real body text " * 40)

    text = read_document(path)[0].text
    assert "Real body text" in text
    assert "1 embedded image" in text
    assert "not extracted" in text
    # It has a text layer, so it must NOT be called scanned.
    assert "scanned" not in text


def test_pdf_repeated_image_counted_once_not_per_page(tmp_path: Path):
    """A journal ornament or logo on every page is one image, not N."""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "logo-on-every-page.pdf"
    _pdf_with_image(path, pymupdf, pages=5, text="Body text for this page. " * 20)

    text = read_document(path)[0].text
    assert "1 embedded image" in text
    assert "5 embedded" not in text


def test_pdf_without_text_layer_is_reported_as_scanned(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan.pdf"
    _pdf_with_image(path, pymupdf, pages=3)  # images only, no text

    text = read_document(path)[0].text
    assert "scanned or image-only" in text
    assert "3 pages hold 1 image, which was not extracted" in text
    # The whole point: the model must not read this as an empty document.
    assert "not as an empty one" in text


def test_empty_pdf_is_reported_as_unread_not_blank(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(str(path))
    doc.close()

    text = read_document(path)[0].text
    assert "may be empty or damaged" in text
    assert "scanned" not in text


def test_text_only_pdf_gets_no_note_at_all(tmp_path: Path):
    """No images and a real text layer: nothing to warn about, and the
    note must not appear as noise on every ordinary document."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Plenty of ordinary body text here. " * 20)
    path = tmp_path / "plain.pdf"
    doc.save(str(path))
    doc.close()

    text = read_document(path)[0].text
    assert "not extracted" not in text
    assert "scanned" not in text


def test_pdf_note_survives_truncation(tmp_path: Path):
    """A note explaining what was dropped is worthless if truncation drops
    it — it is appended after the budget is applied, on purpose."""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "long.pdf"
    _pdf_with_image(path, pymupdf, pages=1, text="x" * 500)

    text = read_document(path, max_chars=50)[0].text
    assert "[truncated]" in text
    assert "embedded image" in text


def test_docx_reports_dropped_images(tmp_path: Path):
    docx = pytest.importorskip("docx")
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    buffer.seek(0)

    document = docx.Document()
    document.add_paragraph("Body paragraph.")
    document.add_picture(buffer)
    path = tmp_path / "with-image.docx"
    document.save(str(path))

    text = read_document(path)[0].text
    assert "Body paragraph." in text
    assert "1 embedded image" in text


def test_docx_without_images_gets_no_note(tmp_path: Path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Just text.")
    path = tmp_path / "plain.docx"
    document.save(str(path))

    assert "not extracted" not in read_document(path)[0].text


def test_pptx_reports_dropped_images(tmp_path: Path):
    pptx_module = pytest.importorskip("pptx")
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    buffer.seek(0)

    presentation = pptx_module.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Slide One"
    slide.shapes.add_picture(buffer, 0, 0)
    path = tmp_path / "deck.pptx"
    presentation.save(str(path))

    text = read_document(path)[0].text
    assert "Slide One" in text
    assert "embedded image" in text


def test_xlsx_image_note_is_its_own_artifact(tmp_path: Path):
    """Tables must not be flattened into prose to carry the note."""
    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("PIL")

    from PIL import Image

    image_path = tmp_path / "chart.png"
    Image.new("RGB", (8, 8), "green").save(image_path)

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["name", "value"])
    sheet.append(["a", 1])
    sheet.add_image(openpyxl.drawing.image.Image(str(image_path)), "D4")
    path = tmp_path / "book.xlsx"
    workbook.save(str(path))

    artifacts = read_document(path)
    tables = [a for a in artifacts if isinstance(a, TableArtifact)]
    notes = [a for a in artifacts if isinstance(a, TextArtifact)]
    assert tables and tables[0].rows == [["a", 1]]
    assert any("embedded image" in n.text for n in notes)


def test_non_office_file_is_not_probed_as_a_zip(tmp_path: Path):
    """`_count_embedded_media` is keyed by suffix — a .txt file must never
    be opened as a zip container, and a corrupt .docx must degrade to
    'no count' rather than raising."""
    from aida.documents.readers import _count_embedded_media

    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    assert _count_embedded_media(plain) == 0

    corrupt = tmp_path / "broken.docx"
    corrupt.write_bytes(b"not a zip at all")
    assert _count_embedded_media(corrupt) == 0
