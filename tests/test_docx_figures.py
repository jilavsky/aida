"""Figure extraction from .docx — the stdlib half of
``aida.documents.figures``.

Bug report: "I attached docx file and agent stated ... the extraction tool
couldn't get them from this docx ... I assumed that docx container contains
zipped pictures or something in that line and extraction should be easy".
It does, and it is: these tests build the container by hand (zipfile +
XML text) so they need no Word, no ``python-docx`` and no ``docs`` extra —
the same reason the extractor itself needs none.

What is worth testing here is the *labelling*, not the copying: an
unlabelled figure is worse than no figure (see ``aida.documents.figures``),
so each test below pins one way a caption is or isn't found, and what the
index admits when it isn't.
"""

from __future__ import annotations

import asyncio
import base64
import zipfile
from pathlib import Path

from aida.documents.attachments import assets_dir_for, store_attachment
from aida.documents.figure_tools import default_figure_tools
from aida.documents.figures import describe_index, extract_docx_figures

#: A real 1x1 PNG. The extractor copies bytes rather than decoding them,
#: but a fixture that is a valid image keeps the test honest about what a
#: .docx actually holds.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
)

#: 300pt square, comfortably over MIN_FIGURE_EDGE_PT.
BIG = 300 * 12700
#: 20pt square — a logo, which the size filter is there to drop.
TINY = 20 * 12700


def _text_p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _image_p(rel_id: str, *, cx: int = BIG, cy: int = BIG, text: str = "") -> str:
    run_text = f"<w:r><w:t>{text}</w:t></w:r>" if text else ""
    return (
        "<w:p>"
        f"{run_text}"
        "<w:r><w:drawing><wp:inline>"
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        "<a:graphic><a:graphicData>"
        f'<a:blip r:embed="{rel_id}"/>'
        "</a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r>"
        "</w:p>"
    )


def _make_docx(path: Path, body: str, media: dict[str, bytes]) -> Path:
    """Write a .docx holding ``body`` (a run of ``w:p`` elements) and
    ``media`` ({"rId1": ...} -> word/media/imageN.<ext>, extension taken
    from the value's key order below)."""
    rels = ["".join([])]
    rel_entries = []
    for index, rel_id in enumerate(media, start=1):
        target = f"media/image{index}{_suffix_for(rel_id)}"
        rel_entries.append(
            f'<Relationship Id="{rel_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/image" Target="{target}"/>'
        )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(rel_entries)}</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        for index, (rel_id, data) in enumerate(media.items(), start=1):
            archive.writestr(f"word/media/image{index}{_suffix_for(rel_id)}", data)
    return path


def _suffix_for(rel_id: str) -> str:
    """Fixture convention: a rel id ending in ``-emf`` stands for a pasted
    vector image, everything else for a PNG."""
    return ".emf" if rel_id.endswith("-emf") else ".png"


def test_caption_in_the_following_paragraph_names_the_figure(tmp_path: Path):
    docx = _make_docx(
        tmp_path / "paper.docx",
        _text_p("Some introductory prose about the sample.")
        + _image_p("rId1")
        + _text_p("Figure 1. USAXS data for the aged specimen."),
        {"rId1": PNG_1PX},
    )
    entries, note = extract_docx_figures(docx, tmp_path / "paper.assets")

    assert [e.label for e in entries] == ["Figure 1"]
    assert entries[0].caption == "Figure 1. USAXS data for the aged specimen."
    assert entries[0].confidence == "high"
    assert note == ""
    assert (tmp_path / "paper.assets" / entries[0].file).read_bytes() == PNG_1PX


def test_caption_in_the_images_own_paragraph_is_found(tmp_path: Path):
    """Word's own Insert Caption often leaves the text in the same
    paragraph as an inline picture."""
    docx = _make_docx(
        tmp_path / "p.docx",
        _image_p("rId1", text="Figure 2. Scattering geometry."),
        {"rId1": PNG_1PX},
    )
    entries, _note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert [e.label for e in entries] == ["Figure 2"]


def test_an_uncaptioned_image_says_so_rather_than_inventing_a_number(tmp_path: Path):
    docx = _make_docx(
        tmp_path / "p.docx",
        _text_p("Body text.") + _image_p("rId1") + _text_p("More body text, not a caption."),
        {"rId1": PNG_1PX},
    )
    entries, _note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert [e.label for e in entries] == ["image 1"]
    assert entries[0].caption == ""
    assert entries[0].confidence == "none"


def test_a_caption_two_paragraphs_away_is_not_stolen(tmp_path: Path):
    """Caught on a real Word file: an uncaptioned picture followed by a
    line of body text used to reach past it and claim the *next* figure's
    caption, reported as high confidence. A positional "image 1" is the
    honest answer here."""
    docx = _make_docx(
        tmp_path / "p.docx",
        _image_p("rId1")
        + _text_p("Plain body text, not a caption.")
        + _text_p("Figure 4. Belongs to a different picture.")
        + _image_p("rId2"),
        {"rId1": PNG_1PX, "rId2": PNG_1PX + b"\x00"},
    )
    entries, _note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert [e.label for e in entries] == ["image 1", "Figure 4"]
    assert entries[0].caption == ""
    assert entries[0].confidence == "none"


def test_word_has_no_pages_so_none_is_claimed(tmp_path: Path):
    """A .docx is a flow, not pages. Reporting "page 1" would be a made-up
    fact, and describe_index must not print one either."""
    docx = _make_docx(
        tmp_path / "p.docx",
        _image_p("rId1") + _text_p("Figure 1. A caption."),
        {"rId1": PNG_1PX},
    )
    entries, _note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert entries[0].page == 0
    from aida.documents.figures import FigureIndex

    described = describe_index("p.docx", FigureIndex(source="p.docx", figures=entries))
    assert "page" not in described.split("Call get_document_figure")[0]


def test_small_decorations_are_not_figures(tmp_path: Path):
    """A letterhead logo passes as an image and fails as a figure — the
    same size filter the PDF path applies, read off Word's own extent."""
    docx = _make_docx(
        tmp_path / "p.docx",
        _image_p("rId1", cx=TINY, cy=TINY) + _image_p("rId2") + _text_p("Figure 1. The real one."),
        {"rId1": PNG_1PX, "rId2": PNG_1PX},
    )
    entries, _note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert [e.label for e in entries] == ["Figure 1"]


def test_the_same_picture_used_twice_is_one_figure(tmp_path: Path):
    docx = _make_docx(
        tmp_path / "p.docx",
        _image_p("rId1") + _text_p("Figure 1. Shown here.") + _image_p("rId1"),
        {"rId1": PNG_1PX},
    )
    entries, _note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert len(entries) == 1


def test_vector_images_are_reported_rather_than_silently_dropped(tmp_path: Path):
    """An EMF (a pasted Excel chart, typically) cannot be shown to a vision
    model. Saying how many were skipped is what lets the agent answer "you
    listed one figure but my document has two"."""
    docx = _make_docx(
        tmp_path / "p.docx",
        _image_p("rId1") + _text_p("Figure 1. A photo.") + _image_p("rId2-emf"),
        {"rId1": PNG_1PX, "rId2-emf": b"not really an emf, but the suffix is what matters"},
    )
    entries, note = extract_docx_figures(docx, tmp_path / "p.assets")

    assert [e.label for e in entries] == ["Figure 1"]
    assert "1 embedded image(s) were skipped" in note
    assert "EMF" in note


def test_a_damaged_docx_yields_an_empty_index_not_an_exception(tmp_path: Path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"this is not a zip at all")

    entries, note = extract_docx_figures(broken, tmp_path / "broken.assets")

    assert entries == []
    assert note == ""


def test_list_document_figures_now_answers_for_an_attached_docx(tmp_path: Path):
    """End to end through the tool the agent actually calls — this is the
    path that used to report "no figures could be extracted" for every
    .docx, because only .pdf was extractable."""
    source = _make_docx(
        tmp_path / "source.docx",
        _text_p("Intro.") + _image_p("rId1") + _text_p("Figure 1. The measured curve."),
        {"rId1": PNG_1PX},
    )
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    stored = store_attachment(source, attachments)
    assert stored.stored_path

    tools = default_figure_tools(lambda: attachments)
    listed = asyncio.run(tools["list_document_figures"].func({"document": "source.docx"}))
    assert not listed.is_error
    assert "Figure 1" in listed.content
    assert "The measured curve" in listed.content

    got = asyncio.run(
        tools["get_document_figure"].func({"document": "source.docx", "label": "Figure 1"})
    )
    assert not got.is_error
    assert assets_dir_for(Path(stored.stored_path)).is_dir()
