"""Figure extraction and the pull tools (documents_implementation.md Phase C).

The design these test: **nothing pushes images at the model.** Extraction
builds an index (label, caption, page, confidence); the agent pulls the one
or two figures it needs by label. An unlabelled figure is worse than no
figure, so the tests that matter most are the ones about labelling being
*honest* — a guess reported as a guess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aida.documents.attachments import assets_dir_for, store_attachment
from aida.documents.figure_tools import default_figure_tools
from aida.documents.figures import (
    FigureEntry,
    describe_index,
    extract_pdf_figures,
    read_index,
    write_index,
)

pymupdf = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")


def _png(color: str = "red", size: int = 240) -> bytes:
    import io

    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("RGB", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _single_column_pdf(path: Path, *, caption: str = "Figure 1. SAXS patterns at 25 C") -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction text for this paper. " * 6)
    page.insert_image(pymupdf.Rect(72, 200, 320, 420), stream=_png())
    page.insert_text((72, 450), caption)
    doc.save(str(path))
    doc.close()
    return path


def _two_column_pdf(path: Path) -> Path:
    """A real side-by-side layout: two text columns that overlap vertically
    and are horizontally disjoint — which is what makes caption pairing a
    guess rather than a fact."""
    doc = pymupdf.open()
    page = doc.new_page()
    # insert_textbox, not insert_text: two runs of text placed at the same
    # y with insert_text are merged by pymupdf into one wide block, which
    # looks single-column to any detector and would make this fixture test
    # nothing. Separate boxes produce the genuinely disjoint blocks a real
    # two-column journal page has.
    body = "Body text of this column. " * 12
    page.insert_textbox(pymupdf.Rect(50, 90, 270, 390), body)
    page.insert_textbox(pymupdf.Rect(320, 90, 545, 390), body)
    page.insert_image(pymupdf.Rect(60, 420, 280, 620), stream=_png("blue"))
    page.insert_text((60, 650), "Figure 1. The left column figure")
    doc.save(str(path))
    doc.close()
    return path


# --- extraction ----------------------------------------------------------


def test_extracts_a_figure_and_pairs_it_with_its_caption(tmp_path: Path):
    pdf = _single_column_pdf(tmp_path / "paper.pdf")
    entries = extract_pdf_figures(pdf, tmp_path / "assets")

    assert len(entries) == 1
    entry = entries[0]
    assert entry.label == "Figure 1"
    assert "SAXS patterns" in entry.caption
    assert entry.page == 1
    assert entry.confidence == "high"
    assert (tmp_path / "assets" / entry.file).exists()


def test_a_multi_column_page_lowers_confidence_rather_than_lying(tmp_path: Path):
    """The honest-labelling requirement. On a two-column layout the text
    below an image is regularly the next column, so the label is a guess and
    must be reported as one."""
    pdf = _two_column_pdf(tmp_path / "journal.pdf")
    entries = extract_pdf_figures(pdf, tmp_path / "assets")

    assert entries and entries[0].label == "Figure 1"
    assert entries[0].confidence == "low"
    assert "uncertain" in describe_index("journal.pdf", entries)


def test_an_uncaptioned_image_gets_a_positional_label_not_an_invented_one(tmp_path: Path):
    pdf = _single_column_pdf(tmp_path / "nocaption.pdf", caption="Just some ordinary prose here.")
    entries = extract_pdf_figures(pdf, tmp_path / "assets")

    assert len(entries) == 1
    assert entries[0].confidence == "none"
    assert entries[0].label.startswith("image 1")
    assert entries[0].caption == ""
    assert "positional" in describe_index("nocaption.pdf", entries)


def test_tiny_ornaments_and_rules_are_not_figures(tmp_path: Path):
    """A journal PDF is full of logos and hairlines; without the size and
    aspect filters the index fills with specks and the real figures are
    lost among them."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(10, 10, 30, 30), stream=_png("green", 20))  # speck
    page.insert_image(pymupdf.Rect(40, 700, 550, 706), stream=_png("black", 8))  # rule
    page.insert_image(pymupdf.Rect(72, 200, 320, 420), stream=_png("red"))  # real
    page.insert_text((72, 450), "Figure 1. The only real figure")
    path = tmp_path / "ornaments.pdf"
    doc.save(str(path))
    doc.close()

    entries = extract_pdf_figures(path, tmp_path / "assets")
    assert len(entries) == 1
    assert entries[0].label == "Figure 1"


def test_a_logo_repeated_on_every_page_is_one_entry(tmp_path: Path):
    logo = _png("purple")
    doc = pymupdf.open()
    for _ in range(5):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(72, 100, 250, 280), stream=logo)
        page.insert_text((72, 320), "Body text on this page. " * 5)
    path = tmp_path / "logo.pdf"
    doc.save(str(path))
    doc.close()

    assert len(extract_pdf_figures(path, tmp_path / "assets")) == 1


def test_extraction_of_a_damaged_file_returns_empty_not_an_exception(tmp_path: Path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    assert extract_pdf_figures(broken, tmp_path / "assets") == []


# --- the index cache -----------------------------------------------------


def test_an_empty_index_is_cached_so_a_fruitless_scan_is_not_repeated(tmp_path: Path):
    assets = tmp_path / "assets"
    write_index(assets, "empty.pdf", [])
    assert read_index(assets) == []  # not None: "we looked and found nothing"


def test_no_index_yet_reads_as_none(tmp_path: Path):
    assert read_index(tmp_path / "never-scanned") is None


def test_a_corrupt_index_reads_as_unscanned_rather_than_failing(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.json").write_text("{ broken json")
    assert read_index(assets) is None


# --- the tools -----------------------------------------------------------


def _tools(attachments: Path):
    return default_figure_tools(lambda: attachments)


def _call(tools, name: str, **arguments):
    return asyncio.run(tools[name].func(arguments))


def _attached(tmp_path: Path) -> Path:
    attachments = tmp_path / "attachments" / "abcd1234"
    source = _single_column_pdf(tmp_path / "paper.pdf")
    store_attachment(source, attachments)
    return attachments


def test_list_then_get_is_the_whole_flow(tmp_path: Path):
    attachments = _attached(tmp_path)
    tools = _tools(attachments)

    listed = _call(tools, "list_document_figures", document="paper.pdf")
    assert not listed.is_error
    assert "Figure 1" in listed.content
    assert "SAXS patterns" in listed.content
    # The index is text only — no images are pushed at the model.
    assert not listed.artifacts

    got = _call(tools, "get_document_figure", document="paper.pdf", label="Figure 1")
    assert not got.is_error
    assert len(got.artifacts) == 1
    assert Path(got.artifacts[0].path).exists()


def test_extraction_is_lazy_and_then_cached(tmp_path: Path):
    """A paper nobody asks a figure question about costs nothing, and the
    second question is free."""
    attachments = _attached(tmp_path)
    assets = assets_dir_for(attachments / "paper.pdf")
    assert not assets.exists(), "attaching must not extract anything"

    _call(_tools(attachments), "list_document_figures", document="paper.pdf")
    assert (assets / "index.json").is_file()

    calls = {"n": 0}
    import aida.documents.figure_tools as ft

    original = ft.extract_pdf_figures

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    ft.extract_pdf_figures = counting
    try:
        _call(_tools(attachments), "list_document_figures", document="paper.pdf")
    finally:
        ft.extract_pdf_figures = original
    assert calls["n"] == 0, "a cached index must not be rebuilt"


def test_a_label_is_matched_case_insensitively_and_by_prefix(tmp_path: Path):
    attachments = _attached(tmp_path)
    tools = _tools(attachments)
    assert not _call(tools, "get_document_figure", document="paper.pdf", label="figure 1").is_error


def test_an_unknown_label_lists_what_is_available(tmp_path: Path):
    attachments = _attached(tmp_path)
    result = _call(_tools(attachments), "get_document_figure", document="paper.pdf", label="Figure 9")
    assert result.is_error
    assert "Figure 1" in result.content


def test_a_document_that_was_never_attached_says_so_plainly(tmp_path: Path):
    """Reporting an empty index would read as 'this paper has no figures',
    which is a different and wrong answer."""
    attachments = _attached(tmp_path)
    result = _call(_tools(attachments), "list_document_figures", document="not-here.pdf")
    assert result.is_error
    assert "paper.pdf" in result.content


def test_no_attachments_at_all_explains_why(tmp_path: Path):
    empty = tmp_path / "attachments" / "deadbeef"
    result = _call(_tools(empty), "list_document_figures", document="paper.pdf")
    assert result.is_error
    assert "read_file" in result.content


@pytest.mark.parametrize(
    "hostile", ["../../etc/passwd", "/etc/passwd", "../paper.pdf", "..", "sub/paper.pdf"]
)
def test_the_document_argument_cannot_escape_the_attachments_folder(tmp_path: Path, hostile: str):
    """A plain containment check rather than a SafetyGuard call — these are
    AIDA's own files — but the argument is still model-supplied."""
    attachments = _attached(tmp_path)
    outside = tmp_path / "paper.pdf"
    assert outside.exists(), "the fixture leaves a same-named file outside the folder"

    result = _call(_tools(attachments), "list_document_figures", document=hostile)
    assert result.is_error


def test_a_document_with_no_figures_is_reported_as_such(tmp_path: Path):
    attachments = tmp_path / "attachments" / "abcd1234"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "All text, no pictures. " * 20)
    plain = tmp_path / "plain.pdf"
    doc.save(str(plain))
    doc.close()
    store_attachment(plain, attachments)

    listed = _call(_tools(attachments), "list_document_figures", document="plain.pdf")
    assert "No figures" in listed.content
    got = _call(_tools(attachments), "get_document_figure", document="plain.pdf", label="Figure 1")
    assert got.is_error


def test_describe_index_names_every_figure_and_prompts_the_pull(tmp_path: Path):
    entries = [
        FigureEntry(label="Figure 1", caption="Guinier fits", file="fig-01.png", page=2, confidence="high"),
        FigureEntry(label="Figure 2", caption="", file="fig-02.png", page=3, confidence="high"),
    ]
    described = describe_index("paper.pdf", entries)
    assert "Figure 1" in described and "Figure 2" in described
    assert "Guinier fits" in described
    assert "get_document_figure" in described
    assert "uncertain" not in described
