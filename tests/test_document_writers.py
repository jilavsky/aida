"""Writer tests (PLAN.md Phase 6 task): Obsidian MD + sidecar images +
relative links roundtrip, and the basic DOCX writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.documents.writers.docx_writer import DocxSection, write_docx_document
from aida.documents.writers.md_obsidian import ImageToEmbed, write_markdown_document
from tests.mock_mcp_server import TINY_PNG_BYTES


def _saved_image_artifact(
    tmp_path: Path, artifact_store: ArtifactStore, name: str = "plot.png"
) -> ImageArtifact:
    artifact = ImageArtifact(data=TINY_PNG_BYTES, mime_type="image/png", filename=name)
    return artifact_store.save_image(artifact)


def test_write_markdown_document_plain_text_only(tmp_path: Path):
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="My Report",
        body="Some findings here.",
        artifact_store=store,
    )
    assert path == target_dir / "report.md"
    text = path.read_text(encoding="utf-8")
    assert "# My Report" in text
    assert "Some findings here." in text


def test_write_markdown_document_with_image_creates_sidecar_and_relative_link(tmp_path: Path):
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    image = _saved_image_artifact(tmp_path, store)

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="Report With Plot",
        body="See the plot below.",
        artifact_store=store,
        images=[ImageToEmbed(artifact=image, alt_text="the plot")],
        sidecar_dirname="figures",
    )

    sidecar_dir = target_dir / "figures"
    copied_files = list(sidecar_dir.iterdir())
    assert len(copied_files) == 1
    assert copied_files[0].read_bytes() == TINY_PNG_BYTES

    text = path.read_text(encoding="utf-8")
    assert f"![the plot](figures/{copied_files[0].name})" in text


def test_write_markdown_document_relative_links_work_after_moving_target_dir(tmp_path: Path):
    """The whole point of relative links: the folder can be moved/zipped
    and the images still resolve, since nothing in the .md file is an
    absolute path."""
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    image = _saved_image_artifact(tmp_path, store)

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="Report",
        body="",
        artifact_store=store,
        images=[ImageToEmbed(artifact=image)],
    )
    text = path.read_text(encoding="utf-8")
    assert str(target_dir) not in text
    assert str(tmp_path) not in text
    import re

    match = re.search(r"\]\(([^)]+)\)", text)
    assert match is not None
    link = match.group(1)
    assert not Path(link).is_absolute()
    assert (target_dir / link).exists()


def test_write_markdown_document_collision_safe_filename(tmp_path: Path):
    target_dir = tmp_path / "records"
    target_dir.mkdir(parents=True)
    (target_dir / "report.md").write_text("existing", encoding="utf-8")
    store = ArtifactStore(base_dir=tmp_path / "artifacts")

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="New Report",
        body="new content",
        artifact_store=store,
    )
    assert path.name == "report (1).md"
    assert (target_dir / "report.md").read_text(encoding="utf-8") == "existing"


def test_write_markdown_document_multiple_images_all_linked(tmp_path: Path):
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    image1 = _saved_image_artifact(tmp_path, store, name="a.png")
    image2 = ImageArtifact(data=TINY_PNG_BYTES, mime_type="image/png", filename="b.png")
    image2 = store.save_image(image2)

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="Two Plots",
        body="",
        artifact_store=store,
        images=[ImageToEmbed(artifact=image1), ImageToEmbed(artifact=image2)],
    )
    text = path.read_text(encoding="utf-8")
    assert "figures/a.png" in text
    assert "figures/b.png" in text
    assert len(list((target_dir / "figures").iterdir())) == 2


# --- Inline image placeholders (PLAN.md §1.5) -------------------------------


def test_write_markdown_document_image_placeholder_places_it_inline(tmp_path: Path):
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    image = _saved_image_artifact(tmp_path, store, name="plot.png")

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="Report",
        body=f"Before the plot.\n\n{{{{image:{image.id}}}}}\n\nAfter the plot.",
        artifact_store=store,
        images=[ImageToEmbed(artifact=image, alt_text="the plot")],
    )
    text = path.read_text(encoding="utf-8")
    before_idx = text.index("Before the plot.")
    image_idx = text.index("![the plot]")
    after_idx = text.index("After the plot.")
    assert before_idx < image_idx < after_idx


def test_write_markdown_document_unreferenced_image_still_appended_after_body(tmp_path: Path):
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    placed = _saved_image_artifact(tmp_path, store, name="a.png")
    leftover = store.save_image(
        ImageArtifact(data=TINY_PNG_BYTES, mime_type="image/png", filename="b.png")
    )

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="Report",
        body=f"See {{{{image:{placed.id}}}}} above.",
        artifact_store=store,
        images=[ImageToEmbed(artifact=placed), ImageToEmbed(artifact=leftover)],
    )
    text = path.read_text(encoding="utf-8")
    assert "figures/a.png" in text
    assert "figures/b.png" in text
    # placed.id's image lands inline in the sentence; leftover (no placeholder) follows after the body.
    assert text.index("figures/a.png") < text.index("above.") < text.index("figures/b.png")


def test_write_markdown_document_placeholder_for_unknown_id_left_as_is(tmp_path: Path):
    target_dir = tmp_path / "records"
    store = ArtifactStore(base_dir=tmp_path / "artifacts")

    path = write_markdown_document(
        target_dir=target_dir,
        filename_stem="report",
        title="Report",
        body="See {{image:does-not-exist}} above.",
        artifact_store=store,
    )
    text = path.read_text(encoding="utf-8")
    assert "{{image:does-not-exist}}" in text


# --- DOCX writer -----------------------------------------------------------


def test_write_docx_document_headings_and_paragraphs(tmp_path: Path):
    docx = pytest.importorskip("docx")
    path = write_docx_document(
        target_dir=tmp_path,
        filename_stem="report",
        title="My Title",
        sections=[
            DocxSection(kind="heading", text="Section One", level=1),
            DocxSection(kind="paragraph", text="Body text here."),
        ],
    )
    assert path.exists()
    document = docx.Document(str(path))
    texts = [p.text for p in document.paragraphs]
    assert "My Title" in texts
    assert "Section One" in texts
    assert "Body text here." in texts


def test_write_docx_document_with_table(tmp_path: Path):
    docx = pytest.importorskip("docx")
    path = write_docx_document(
        target_dir=tmp_path,
        filename_stem="report",
        title="Table Report",
        sections=[DocxSection(kind="table", columns=["name", "value"], rows=[["a", 1], ["b", 2]])],
    )
    document = docx.Document(str(path))
    assert len(document.tables) == 1
    table = document.tables[0]
    assert [c.text for c in table.rows[0].cells] == ["name", "value"]
    assert [c.text for c in table.rows[1].cells] == ["a", "1"]
    assert [c.text for c in table.rows[2].cells] == ["b", "2"]


def test_write_docx_document_with_image(tmp_path: Path):
    docx = pytest.importorskip("docx")
    image_path = tmp_path / "plot.png"
    image_path.write_bytes(TINY_PNG_BYTES)
    image = ImageArtifact(data=b"", mime_type="image/png", path=str(image_path))

    path = write_docx_document(
        target_dir=tmp_path / "out",
        filename_stem="report",
        title="Report With Image",
        sections=[DocxSection(kind="image", image=image)],
    )
    document = docx.Document(str(path))
    assert len(document.inline_shapes) == 1


def test_write_docx_document_collision_safe_filename(tmp_path: Path):
    pytest.importorskip("docx")
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "report.docx").write_bytes(b"not a real docx but occupies the name")
    path = write_docx_document(
        target_dir=tmp_path, filename_stem="report", title="New", sections=[]
    )
    assert path.name == "report (1).docx"
