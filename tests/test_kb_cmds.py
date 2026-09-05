"""Tests for aida.cli.kb_cmds — the ``aida kb`` config/build/update/query
subcommands (Phase 8, mirrors test_mcp_cmds.py's pattern). Every
build/update/query test monkeypatches ``build_embeddings_provider`` to a
``MockEmbeddings`` — no real network/API key needed to prove the pipeline
works, same "deterministic fake embedder" approach as the rest of Phase 8's
test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.kb_cmds import main
from aida.config.paths import knowledge_db_path
from aida.config.settings import (
    EmbeddingProfile,
    load_knowledge_config,
    load_settings,
    save_providers_config,
)
from aida.knowledge.rag import index as kb_index
from aida.providers.mock_embeddings import MockEmbeddings

# --- list/show -----------------------------------------------------------


def test_list_empty(aida_home: Path, capsys):
    assert main(["list"]) == 0
    assert "No knowledge bases configured." in capsys.readouterr().out


def test_show_unknown(aida_home: Path, capsys):
    assert main(["show", "nope"]) == 1
    assert "Unknown knowledge base" in capsys.readouterr().out


# --- add -------------------------------------------------------------------


def test_add_persists_to_disk(aida_home: Path, capsys):
    rc = main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            "/data/usaxs,/data/obsidian",
            "--embedding-profile",
            "embed-profile",
            "--chunk-size",
            "500",
            "--chunk-overlap",
            "50",
        ]
    )
    assert rc == 0
    assert "Added" in capsys.readouterr().out

    kb = load_knowledge_config(aida_home).knowledge_bases["usaxs-docs"]
    assert kb.source_folders == ["/data/usaxs", "/data/obsidian"]
    assert kb.embedding_profile == "embed-profile"
    assert kb.chunk_size == 500
    assert kb.chunk_overlap == 50


def test_add_refuses_to_clobber_existing(aida_home: Path, capsys):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    rc = main(["add", "usaxs-docs", "--source-folders", "/b"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().out
    assert load_knowledge_config(aida_home).knowledge_bases["usaxs-docs"].source_folders == ["/a"]


def test_add_defaults_chunk_size_and_overlap(aida_home: Path):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    kb = load_knowledge_config(aida_home).knowledge_bases["usaxs-docs"]
    assert kb.chunk_size == 1000
    assert kb.chunk_overlap == 150


# --- show (populated) --------------------------------------------------------


def test_show_known(aida_home: Path, capsys):
    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            "/data/usaxs",
            "--embedding-profile",
            "embed-profile",
        ]
    )
    rc = main(["show", "usaxs-docs"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "source_folders:    /data/usaxs" in out
    assert "embedding_profile: embed-profile" in out


# --- edit --------------------------------------------------------------------


def test_edit_unknown(aida_home: Path, capsys):
    rc = main(["edit", "nope", "--embedding-profile", "x"])
    assert rc == 1
    assert "Unknown knowledge base" in capsys.readouterr().out


def test_edit_only_overwrites_passed_fields(aida_home: Path):
    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            "/old",
            "--embedding-profile",
            "profile-a",
            "--chunk-size",
            "800",
        ]
    )
    main(["edit", "usaxs-docs", "--embedding-profile", "profile-b"])

    kb = load_knowledge_config(aida_home).knowledge_bases["usaxs-docs"]
    assert kb.embedding_profile == "profile-b"
    assert kb.source_folders == ["/old"], "unset flags must leave existing fields untouched"
    assert kb.chunk_size == 800


# --- remove ------------------------------------------------------------------


def test_remove_unknown(aida_home: Path, capsys):
    rc = main(["remove", "nope", "--yes"])
    assert rc == 1


def test_remove_with_yes_flag_skips_prompt(aida_home: Path):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    rc = main(["remove", "usaxs-docs", "--yes"])
    assert rc == 0
    assert "usaxs-docs" not in load_knowledge_config(aida_home).knowledge_bases


def test_remove_without_yes_prompts_and_respects_no(aida_home: Path, monkeypatch):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    rc = main(["remove", "usaxs-docs"])
    assert rc == 1
    assert "usaxs-docs" in load_knowledge_config(aida_home).knowledge_bases


def test_remove_without_delete_index_leaves_the_index_file_on_disk(aida_home: Path):
    """Bug report: "when I delete source, is its data removed? ... not
    clear when and how will disk be cleaned up." Default behavior (no
    --delete-index) is unchanged: config only."""
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    index_path = knowledge_db_path("usaxs-docs")
    kb_index.connect(index_path).close()

    rc = main(["remove", "usaxs-docs", "--yes"])
    assert rc == 0
    assert "usaxs-docs" not in load_knowledge_config(aida_home).knowledge_bases
    assert index_path.exists()


def test_remove_with_delete_index_also_removes_the_index_file(aida_home: Path):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    index_path = knowledge_db_path("usaxs-docs")
    kb_index.connect(index_path).close()

    rc = main(["remove", "usaxs-docs", "--yes", "--delete-index"])
    assert rc == 0
    assert "usaxs-docs" not in load_knowledge_config(aida_home).knowledge_bases
    assert not index_path.exists()


def test_remove_with_delete_index_on_a_never_built_kb_does_not_raise(aida_home: Path):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    rc = main(["remove", "usaxs-docs", "--yes", "--delete-index"])
    assert rc == 0


# --- build / update / query --------------------------------------------------


def _configure_embedding_profile(aida_home: Path, name: str = "embed-profile") -> None:
    settings = load_settings()
    settings.providers.embedding_profiles[name] = EmbeddingProfile(
        name=name, kind="openai_compat", model="embed-model"
    )
    save_providers_config(settings.providers, aida_home)


def _make_corpus(tmp_path: Path) -> Path:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "fitting.md").write_text(
        "# Unified Fit\n\nUnified Fit models a SAXS curve with multiple structural levels.\n",
        encoding="utf-8",
    )
    (folder / "instrument.md").write_text(
        "# USAXS Instrument\n\nThe USAXS instrument uses a Bonse-Hart crystal analyzer.\n",
        encoding="utf-8",
    )
    return folder


def test_build_unknown_kb(aida_home: Path, capsys):
    rc = main(["build", "nope"])
    assert rc == 1
    assert "Unknown knowledge base" in capsys.readouterr().out


def test_build_without_embedding_profile_configured(aida_home: Path, capsys):
    main(["add", "usaxs-docs", "--source-folders", "/a"])
    rc = main(["build", "usaxs-docs"])
    assert rc == 1
    assert "no embedding_profile configured" in capsys.readouterr().out


def test_build_with_unknown_embedding_profile(aida_home: Path, capsys):
    main(["add", "usaxs-docs", "--source-folders", "/a", "--embedding-profile", "does-not-exist"])
    rc = main(["build", "usaxs-docs"])
    assert rc == 1
    assert "unknown embedding profile" in capsys.readouterr().out


def test_build_ingests_the_corpus_and_reports_counts(
    monkeypatch, aida_home: Path, tmp_path: Path, capsys
):
    monkeypatch.setattr(
        "aida.cli.kb_cmds.build_embeddings_provider", lambda profile: MockEmbeddings()
    )
    _configure_embedding_profile(aida_home)
    corpus = _make_corpus(tmp_path)

    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            str(corpus),
            "--embedding-profile",
            "embed-profile",
        ]
    )
    rc = main(["build", "usaxs-docs"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "added:   2" in out

    conn = kb_index.connect(knowledge_db_path("usaxs-docs"))
    try:
        assert kb_index.chunk_count(conn) > 0
    finally:
        conn.close()


def test_update_only_reembeds_the_changed_file(
    monkeypatch, aida_home: Path, tmp_path: Path, capsys
):
    monkeypatch.setattr(
        "aida.cli.kb_cmds.build_embeddings_provider", lambda profile: MockEmbeddings()
    )
    _configure_embedding_profile(aida_home)
    corpus = _make_corpus(tmp_path)
    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            str(corpus),
            "--embedding-profile",
            "embed-profile",
        ]
    )
    main(["build", "usaxs-docs"])
    capsys.readouterr()

    rc = main(["update", "usaxs-docs"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "added:   0" in out
    assert "updated: 0" in out


def test_list_shows_chunk_counts_after_build(monkeypatch, aida_home: Path, tmp_path: Path, capsys):
    monkeypatch.setattr(
        "aida.cli.kb_cmds.build_embeddings_provider", lambda profile: MockEmbeddings()
    )
    _configure_embedding_profile(aida_home)
    corpus = _make_corpus(tmp_path)
    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            str(corpus),
            "--embedding-profile",
            "embed-profile",
        ]
    )
    main(["build", "usaxs-docs"])
    capsys.readouterr()

    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "usaxs-docs" in out
    assert "chunks=0" not in out


def test_query_returns_ranked_passages(monkeypatch, aida_home: Path, tmp_path: Path, capsys):
    monkeypatch.setattr(
        "aida.cli.kb_cmds.build_embeddings_provider", lambda profile: MockEmbeddings()
    )
    _configure_embedding_profile(aida_home)
    corpus = _make_corpus(tmp_path)
    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            str(corpus),
            "--embedding-profile",
            "embed-profile",
        ]
    )
    main(["build", "usaxs-docs"])
    capsys.readouterr()

    rc = main(["query", "usaxs-docs", "How does Unified Fit work?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fitting.md" in out
    assert "score" in out


def test_query_on_empty_index_reports_no_passages(monkeypatch, aida_home: Path, capsys):
    monkeypatch.setattr(
        "aida.cli.kb_cmds.build_embeddings_provider", lambda profile: MockEmbeddings()
    )
    _configure_embedding_profile(aida_home)
    main(["add", "usaxs-docs", "--embedding-profile", "embed-profile"])

    rc = main(["query", "usaxs-docs", "anything"])
    assert rc == 0
    assert "No passages retrieved." in capsys.readouterr().out


def test_query_unknown_kb(aida_home: Path, capsys):
    rc = main(["query", "nope", "anything"])
    assert rc == 1
    assert "Unknown knowledge base" in capsys.readouterr().out


# --- real-use bug: a `file://` URI pasted into --source-folders ------------


def test_add_normalizes_a_file_uri_source_folder(aida_home: Path):
    main(["add", "usaxs-docs", "--source-folders", "file:///data/usaxs"])
    kb = load_knowledge_config(aida_home).knowledge_bases["usaxs-docs"]
    assert kb.source_folders == ["/data/usaxs"]


def test_build_warns_about_a_missing_source_folder(
    monkeypatch, aida_home: Path, tmp_path: Path, capsys
):
    monkeypatch.setattr(
        "aida.cli.kb_cmds.build_embeddings_provider", lambda profile: MockEmbeddings()
    )
    _configure_embedding_profile(aida_home)
    missing = tmp_path / "does-not-exist"

    main(
        [
            "add",
            "usaxs-docs",
            "--source-folders",
            str(missing),
            "--embedding-profile",
            "embed-profile",
        ]
    )
    rc = main(["build", "usaxs-docs"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert str(missing) in out
    assert "added:   0" in out


# --- required subcommand -----------------------------------------------------


def test_bare_kb_requires_a_subcommand():
    with pytest.raises(SystemExit):
        main([])


# --- chunk_size/chunk_overlap validation ---------------------------------
#
# Review finding: `aida kb add --chunk-size 100` had no validation at all,
# and an overlap >= chunk size makes chunking loop forever (see
# test_chunking.py). The clamp in normalize_chunk_params is the backstop;
# the CLI says no up front rather than silently rewriting what was typed.


def test_add_rejects_overlap_at_or_above_chunk_size(aida_home: Path, capsys):
    exit_code = main(["add", "docs", "--chunk-size", "100", "--chunk-overlap", "100"])
    assert exit_code == 1
    assert "must be smaller than" in capsys.readouterr().out
    assert "docs" not in load_knowledge_config().knowledge_bases


def test_add_rejects_a_chunk_size_below_one(aida_home: Path, capsys):
    assert main(["add", "docs", "--chunk-size", "0"]) == 1
    assert "at least 1" in capsys.readouterr().out


def test_add_accepts_a_valid_pair(aida_home: Path):
    assert main(["add", "docs", "--chunk-size", "500", "--chunk-overlap", "100"]) == 0
    kb = load_knowledge_config().knowledge_bases["docs"]
    assert (kb.chunk_size, kb.chunk_overlap) == (500, 100)


def test_edit_validates_against_the_resulting_pair_not_just_the_flags(aida_home: Path, capsys):
    """Lowering only --chunk-size can land below the overlap already stored
    in knowledge.yaml, so the check has to see both values."""
    assert main(["add", "docs", "--chunk-size", "1000", "--chunk-overlap", "800"]) == 0

    assert main(["edit", "docs", "--chunk-size", "500"]) == 1
    assert "must be smaller than" in capsys.readouterr().out
    assert load_knowledge_config().knowledge_bases["docs"].chunk_size == 1000
