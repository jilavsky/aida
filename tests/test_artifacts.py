from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import (
    FileArtifact,
    ImageArtifact,
    JsonArtifact,
    TableArtifact,
    TextArtifact,
)
from aida.artifacts.policy import describe_for_model
from aida.artifacts.store import ArtifactStore

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a")  # PNG magic bytes, enough for a fake-but-typed test


# --- dataclasses -------------------------------------------------------------


def test_artifacts_get_unique_ids():
    a1 = TextArtifact(text="a")
    a2 = TextArtifact(text="b")
    assert a1.id != a2.id


def test_image_artifact_path_starts_none():
    art = ImageArtifact(data=PNG_BYTES, mime_type="image/png")
    assert art.path is None


# --- store ---------------------------------------------------------------------


def test_store_save_image_writes_file_and_sets_path(tmp_path: Path):
    store = ArtifactStore(base_dir=tmp_path)
    art = ImageArtifact(data=PNG_BYTES, mime_type="image/png")

    saved = store.save_image(art)

    assert saved.path is not None
    written = Path(saved.path)
    assert written.exists()
    assert written.read_bytes() == PNG_BYTES
    assert written.suffix == ".png"


def test_store_save_image_uses_explicit_filename(tmp_path: Path):
    store = ArtifactStore(base_dir=tmp_path)
    art = ImageArtifact(data=PNG_BYTES, mime_type="image/png", filename="plot.png")
    saved = store.save_image(art)
    assert Path(saved.path).name == "plot.png"


def test_store_save_file_from_bytes(tmp_path: Path):
    store = ArtifactStore(base_dir=tmp_path)
    art = FileArtifact(data=b"hello world", filename="notes.txt")
    saved = store.save_file(art)
    assert Path(saved.path).read_bytes() == b"hello world"


def test_store_save_file_already_on_disk_not_rewritten(tmp_path: Path):
    existing = tmp_path / "already-there.txt"
    existing.write_text("original")
    store = ArtifactStore(base_dir=tmp_path)
    art = FileArtifact(path=str(existing))
    saved = store.save_file(art)
    assert saved.path == str(existing)
    assert existing.read_text() == "original"


def test_store_tracks_metadata(tmp_path: Path):
    store = ArtifactStore(base_dir=tmp_path)
    store.save_image(ImageArtifact(data=PNG_BYTES, mime_type="image/png"))
    store.save_file(FileArtifact(data=b"x", filename="a.txt"))
    meta = store.list_metadata()
    assert len(meta) == 2
    assert {m.kind for m in meta} == {"ImageArtifact", "FileArtifact"}


def test_store_copy_to_target(tmp_path: Path):
    store = ArtifactStore(base_dir=tmp_path / "artifacts")
    art = store.save_image(ImageArtifact(data=PNG_BYTES, mime_type="image/png", filename="p.png"))
    target = tmp_path / "target_folder"
    dest = store.copy_to_target(art, target)
    assert dest.exists()
    assert dest.read_bytes() == PNG_BYTES
    assert dest.parent == target


def test_store_copy_to_target_requires_saved_artifact(tmp_path: Path):
    store = ArtifactStore(base_dir=tmp_path)
    art = ImageArtifact(data=PNG_BYTES, mime_type="image/png")  # never saved
    with pytest.raises(ValueError):
        store.copy_to_target(art, tmp_path / "target")


def test_store_creates_base_dir_if_missing(tmp_path: Path):
    base = tmp_path / "does" / "not" / "exist"
    ArtifactStore(base_dir=base)
    assert base.is_dir()


# --- policy (what the LLM sees) --------------------------------------------


def test_describe_text_artifact():
    art = TextArtifact(text="hello")
    assert describe_for_model(art) == "hello"


def test_describe_text_artifact_truncates():
    art = TextArtifact(text="x" * 10_000)
    out = describe_for_model(art, max_chars=100)
    assert len(out) <= 100
    assert "truncated" in out


def test_describe_image_artifact_never_includes_raw_bytes():
    art = ImageArtifact(data=PNG_BYTES, mime_type="image/png", path="/tmp/x.png")
    out = describe_for_model(art)
    assert "image/png" in out
    assert "/tmp/x.png" in out
    assert str(PNG_BYTES) not in out
    # No stray base64-looking blob either.
    assert len(out) < 200


def test_describe_image_artifact_without_path_still_useful():
    art = ImageArtifact(data=PNG_BYTES, mime_type="image/png")
    out = describe_for_model(art)
    assert "image/png" in out


def test_describe_file_artifact():
    art = FileArtifact(path="/tmp/report.md", mime_type="text/markdown")
    out = describe_for_model(art)
    assert "/tmp/report.md" in out
    assert "text/markdown" in out


def test_describe_json_artifact():
    art = JsonArtifact(data={"sample": "S001", "rg": 34.2})
    out = describe_for_model(art)
    assert '"sample"' in out
    assert "S001" in out


def test_describe_json_artifact_non_serializable_falls_back_to_str():
    art = JsonArtifact(data={"weird": object()})
    out = describe_for_model(art)
    assert "weird" in out


def test_describe_table_artifact():
    art = TableArtifact(columns=["sample", "rg"], rows=[["S001", 34.2], ["S002", 41.0]])
    out = describe_for_model(art)
    assert "sample" in out
    assert "S001" in out
    assert "S002" in out


def test_describe_table_artifact_truncates_rows():
    columns = ["a"]
    rows = [[str(i) * 20] for i in range(500)]
    art = TableArtifact(columns=columns, rows=rows)
    out = describe_for_model(art, max_chars=500)
    assert len(out) <= 500
    assert "truncated" in out
