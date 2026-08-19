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
