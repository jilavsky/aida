"""Integration tests for
aida.ui.qt.knowledge_management_dialog.KnowledgeManagementDialog (Phase 8)
— mirrors tests/ui/test_mcp_management_dialog.py's split between config-only
tests (no bridge needed) and live build/update tests against a real
``ChatBridge`` with a real temp corpus, monkeypatching only the embeddings
provider (``MockEmbeddings`` — deterministic, no network)."""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import (
    EmbeddingProfile,
    KnowledgeBaseConfig,
    KnowledgeConfig,
    ProviderProfile,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.knowledge.rag import index as kb_index
from aida.providers.mock import MockProvider, MockTurn
from aida.providers.mock_embeddings import MockEmbeddings
from aida.ui.qt._qt import QMessageBox
from aida.ui.qt.bridge import ChatBridge
from aida.ui.qt.knowledge_management_dialog import (
    KnowledgeBaseFormDialog,
    KnowledgeManagementDialog,
)
from aida.ui.qt.main_window import MainWindow
from aida.ui.qt.retrieval_widget import RetrievalRow
from tests.ui._qt_test_utils import pump_until


def _settings_with_profile(name: str = "mock-profile") -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(name=name, kind="openai_compat", model="mock-model")
    return settings


def _settings_with_embedding_profile(name: str = "embed-profile") -> Settings:
    settings = _settings_with_profile()
    settings.providers.embedding_profiles[name] = EmbeddingProfile(name=name, kind="openai_compat", model="embed-model")
    return settings


def _make_corpus(tmp_path: Path) -> Path:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "fitting.md").write_text(
        "# Unified Fit\n\nUnified Fit models a SAXS curve with multiple structural levels.\n",
        encoding="utf-8",
    )
    return folder


# --- construction / listing (no bridge needed) -------------------------------


def test_dialog_with_no_bridge_shows_configured_knowledge_bases(qapp, aida_home: Path):
    settings = load_settings()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={"usaxs-docs": KnowledgeBaseConfig(name="usaxs-docs", embedding_profile="embed-profile")}
    )
    dialog = KnowledgeManagementDialog(settings, None)
    assert dialog._kb_list.count() == 1
    assert "usaxs-docs" in dialog._kb_list.item(0).text()


def test_dialog_with_no_knowledge_bases_is_empty(qapp, aida_home: Path):
    dialog = KnowledgeManagementDialog(load_settings(), None)
    assert dialog._kb_list.count() == 0
    assert "no knowledge base selected" in dialog._details_label.text()


# --- add / edit / remove (config-only) ---------------------------------------


def test_add_knowledge_base_persists_to_settings_and_disk(qapp, aida_home: Path):
    from aida.config.settings import load_knowledge_config

    settings = _settings_with_embedding_profile()
    dialog = KnowledgeManagementDialog(settings, None)

    form = KnowledgeBaseFormDialog(embedding_profile_names=["embed-profile"])
    form._name_edit.setText("usaxs-docs")
    form._folders_edit.setPlainText("/data/usaxs\n/data/obsidian")
    form._chunk_size_spin.setValue(500)
    form.accept()

    config = form.result_config()
    assert config.name == "usaxs-docs"
    assert config.source_folders == ["/data/usaxs", "/data/obsidian"]
    assert config.embedding_profile == "embed-profile"
    assert config.chunk_size == 500

    settings.knowledge.knowledge_bases[config.name] = config
    from aida.config.settings import save_knowledge_config

    save_knowledge_config(settings.knowledge)
    dialog._refresh_kb_list()

    assert "usaxs-docs" in load_knowledge_config(aida_home).knowledge_bases
    assert dialog._kb_list.count() == 1


def test_edit_knowledge_base_via_dialog_action(qapp, aida_home: Path):
    from aida.config.settings import load_knowledge_config, save_knowledge_config

    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(name="usaxs-docs", source_folders=["/old"], embedding_profile="embed-profile")
        }
    )
    dialog = KnowledgeManagementDialog(settings, None)
    dialog._kb_list.setCurrentRow(0)

    form = KnowledgeBaseFormDialog(
        kb=settings.knowledge.knowledge_bases["usaxs-docs"], embedding_profile_names=["embed-profile"]
    )
    assert form._name_edit.isReadOnly(), "name must not be changeable on edit"
    form._folders_edit.setPlainText("/new")
    updated = form.result_config()

    settings.knowledge.knowledge_bases["usaxs-docs"] = updated
    save_knowledge_config(settings.knowledge)
    dialog._refresh_kb_list()

    assert load_knowledge_config(aida_home).knowledge_bases["usaxs-docs"].source_folders == ["/new"]


def test_remove_knowledge_base_yes_deletes_config_and_index_file(qapp, aida_home: Path, monkeypatch):
    from aida.config.paths import knowledge_db_path
    from aida.config.settings import load_knowledge_config

    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={"usaxs-docs": KnowledgeBaseConfig(name="usaxs-docs", embedding_profile="embed-profile")}
    )
    index_path = knowledge_db_path("usaxs-docs")
    kb_index.connect(index_path).close()
    dialog = KnowledgeManagementDialog(settings, None)
    dialog._kb_list.setCurrentRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._on_remove()

    assert dialog._kb_list.count() == 0
    assert "usaxs-docs" not in load_knowledge_config(aida_home).knowledge_bases
    assert not index_path.exists()


def test_remove_knowledge_base_no_removes_config_but_keeps_index_file(qapp, aida_home: Path, monkeypatch):
    from aida.config.paths import knowledge_db_path
    from aida.config.settings import load_knowledge_config

    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={"usaxs-docs": KnowledgeBaseConfig(name="usaxs-docs", embedding_profile="embed-profile")}
    )
    index_path = knowledge_db_path("usaxs-docs")
    kb_index.connect(index_path).close()
    dialog = KnowledgeManagementDialog(settings, None)
    dialog._kb_list.setCurrentRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog._on_remove()

    assert dialog._kb_list.count() == 0
    assert "usaxs-docs" not in load_knowledge_config(aida_home).knowledge_bases
    assert index_path.exists()


def test_remove_knowledge_base_cancelled_keeps_it(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={"usaxs-docs": KnowledgeBaseConfig(name="usaxs-docs", embedding_profile="embed-profile")}
    )
    dialog = KnowledgeManagementDialog(settings, None)
    dialog._kb_list.setCurrentRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    dialog._on_remove()

    assert dialog._kb_list.count() == 1


def test_add_without_any_embedding_profile_configured_offers_to_open_providers_dialog(
    qapp, aida_home: Path, monkeypatch
):
    """U2 fixed the actual dead end this used to be — "Configure an
    embedding profile in providers.yaml first" with no GUI path to do that
    — by offering to open the new Providers… dialog right here. Declining
    (answer=No) behaves like the old bare warning: nothing added."""
    settings = load_settings()  # no embedding_profiles configured at all
    dialog = KnowledgeManagementDialog(settings, None)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog._on_add()

    assert dialog._kb_list.count() == 0


def test_add_without_any_embedding_profile_opens_providers_dialog_on_yes(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()  # no embedding_profiles configured at all
    dialog = KnowledgeManagementDialog(settings, None)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    opened = []

    class _FakeProfilesDialog:
        def __init__(self, *a, **k):
            opened.append(True)

        def exec(self):
            return 0

    monkeypatch.setattr("aida.ui.qt.profiles_dialog.ProfilesDialog", _FakeProfilesDialog)
    dialog._on_add()

    assert opened == [True]
    # Still no embedding profiles were actually added by the fake dialog,
    # so _on_add bails out the same way it did before — no KB form opened.
    assert dialog._kb_list.count() == 0


def test_form_normalizes_a_file_uri_source_folder(qapp, aida_home: Path):
    """Real-use bug: pasting a folder path copied via a file manager's
    "Copy as URI" action (Obsidian's among them) came through as
    `file:///Users/...`, which silently indexed zero files at build time
    with no error anywhere. The form now normalizes it to a plain path at
    save time, same as the CLI's `aida kb add/edit`."""
    form = KnowledgeBaseFormDialog(embedding_profile_names=["embed-profile"])
    form._folders_edit.setPlainText("file:///data/usaxs")
    config = form.result_config()
    assert config.source_folders == ["/data/usaxs"]


# --- live rebuild/update against a real ChatBridge + MockEmbeddings ---------


def _make_bridge(qapp, loop_thread, settings, monkeypatch) -> ChatBridge:
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    bridge = ChatBridge(loop_thread)
    bridge.start(settings, profile_name="mock-profile")
    assert pump_until(qapp, lambda: bridge.session is not None, timeout=10.0)
    return bridge


def test_rebuild_ingests_the_corpus_and_updates_the_list(
    qapp, loop_thread, aida_home: Path, records_home: Path, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("aida.ui.qt.bridge.build_embeddings_provider", lambda profile: MockEmbeddings())
    corpus = _make_corpus(tmp_path)

    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(
                name="usaxs-docs", source_folders=[str(corpus)], embedding_profile="embed-profile"
            )
        }
    )
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch)
    dialog = KnowledgeManagementDialog(settings, bridge)
    try:
        dialog._kb_list.setCurrentRow(0)
        dialog._on_rebuild()

        assert pump_until(qapp, lambda: "added 1" in dialog._status_label.text(), timeout=10.0)
        assert "(1 chunk" in dialog._kb_list.item(0).text() or "chunk(s)" in dialog._kb_list.item(0).text()

        from aida.config.paths import knowledge_db_path
        from aida.knowledge.rag import index as kb_index

        conn = kb_index.connect(knowledge_db_path("usaxs-docs"))
        try:
            assert kb_index.chunk_count(conn) > 0
        finally:
            conn.close()
    finally:
        bridge.shutdown()


def test_rebuild_with_unbuildable_embedding_profile_reports_failure(
    qapp, loop_thread, aida_home: Path, records_home: Path, tmp_path: Path, monkeypatch
):
    corpus = _make_corpus(tmp_path)
    settings = _settings_with_profile()
    settings.providers.embedding_profiles["embed-profile"] = EmbeddingProfile(name="embed-profile", kind="totally-unknown")
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(
                name="usaxs-docs", source_folders=[str(corpus)], embedding_profile="embed-profile"
            )
        }
    )
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch)
    dialog = KnowledgeManagementDialog(settings, bridge)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    try:
        dialog._kb_list.setCurrentRow(0)
        dialog._on_rebuild()

        assert pump_until(qapp, lambda: "FAILED" in dialog._status_label.text(), timeout=10.0)
    finally:
        bridge.shutdown()


def test_rebuild_with_a_missing_source_folder_warns_instead_of_silently_no_oping(
    qapp, loop_thread, aida_home: Path, records_home: Path, tmp_path: Path, monkeypatch
):
    """Real-use bug: a source folder that doesn't resolve to a real
    directory (in the original report, a `file://` URI) used to produce
    "added 0, updated 0" with zero indication why. A rebuild against a
    knowledge base with a missing folder must now pop an explicit
    warning."""
    monkeypatch.setattr("aida.ui.qt.bridge.build_embeddings_provider", lambda profile: MockEmbeddings())
    missing = tmp_path / "does-not-exist"

    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(
                name="usaxs-docs", source_folders=[str(missing)], embedding_profile="embed-profile"
            )
        }
    )
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch)
    dialog = KnowledgeManagementDialog(settings, bridge)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    try:
        dialog._kb_list.setCurrentRow(0)
        dialog._on_rebuild()

        assert pump_until(qapp, lambda: "added 0" in dialog._status_label.text(), timeout=10.0)
        assert warned == [True]
    finally:
        bridge.shutdown()


def test_closing_the_dialog_disconnects_it_from_the_bridge(
    qapp, loop_thread, aida_home: Path, records_home: Path, tmp_path: Path, monkeypatch
):
    """Same leaked-connection regression class as
    test_mcp_management_dialog.py's equivalent test: a closed dialog must
    stop reacting to bridge signals."""
    monkeypatch.setattr("aida.ui.qt.bridge.build_embeddings_provider", lambda profile: MockEmbeddings())
    corpus = _make_corpus(tmp_path)
    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(
                name="usaxs-docs", source_folders=[str(corpus)], embedding_profile="embed-profile"
            )
        }
    )
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch)
    try:
        first = KnowledgeManagementDialog(settings, bridge)
        first.done(0)  # simulate closing it, same hook QDialog.exec()/close() go through
        second = KnowledgeManagementDialog(settings, bridge)

        second._kb_list.setCurrentRow(0)
        second._on_rebuild()
        assert pump_until(qapp, lambda: "added 1" in second._status_label.text(), timeout=10.0)

        # The first (closed) dialog's status label must never have updated.
        assert "added 1" not in first._status_label.text()
    finally:
        bridge.shutdown()


# --- full end-to-end: workspace with a KB attached, rebuild, chat turn -----


def test_full_workflow_rebuild_then_chat_turn_shows_retrieval_row(
    qapp, loop_thread, aida_home: Path, records_home: Path, tmp_path: Path, monkeypatch
):
    """Phase 8's own GUI acceptance item: open the Knowledge dialog against
    a real temp corpus, rebuild, confirm chunk count updates; then send a
    chat turn through a workspace with that KB attached and confirm a
    RetrievalPerformed row appears in the chat transcript."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="here you go")]))
    monkeypatch.setattr("aida.cli.chat.build_embeddings_provider", lambda profile: MockEmbeddings())
    monkeypatch.setattr("aida.ui.qt.bridge.build_embeddings_provider", lambda profile: MockEmbeddings())
    corpus = _make_corpus(tmp_path)

    settings = _settings_with_embedding_profile()
    settings.knowledge = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(
                name="usaxs-docs", source_folders=[str(corpus)], embedding_profile="embed-profile"
            )
        }
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", knowledge_bases=["usaxs-docs"])
        }
    )

    window = MainWindow(settings, loop_thread, start_kwargs={"workspace_name": "use-ws"})
    try:
        assert pump_until(qapp, lambda: window.statusBar().currentMessage().startswith("Ready"), timeout=10.0)
        assert window.bridge.session.active_knowledge_bases, "session must have resolved the workspace's KB"

        dialog = KnowledgeManagementDialog(settings, window.bridge)
        dialog._kb_list.setCurrentRow(0)
        dialog._on_rebuild()
        assert pump_until(qapp, lambda: "added 1" in dialog._status_label.text(), timeout=10.0)
        dialog.done(0)

        window.input_box.set_text("How does Unified Fit work?")
        window.input_box._send_button.click()
        assert pump_until(
            qapp,
            lambda: any(isinstance(window.chat_panel.widget_at(i), RetrievalRow) for i in range(window.chat_panel.widget_count)),
            timeout=10.0,
        )
    finally:
        window.close()


# --- chunk overlap can no longer exceed chunk size ------------------------
#
# Review finding: the two spin boxes were ranged independently (size down to
# 100, overlap up to 100,000), so a chunk_overlap >= chunk_size was two
# clicks away — and chunking then loops forever, on the shared
# AsyncLoopThread, taking the chat session with it (see test_chunking.py).


def test_overlap_cannot_be_set_at_or_above_the_chunk_size(qapp):
    dialog = KnowledgeBaseFormDialog(embedding_profile_names=["mock"])
    dialog._chunk_size_spin.setValue(100)

    assert dialog._chunk_overlap_spin.maximum() == 99

    dialog._chunk_overlap_spin.setValue(100_000)
    assert dialog._chunk_overlap_spin.value() < dialog._chunk_size_spin.value()


def test_lowering_the_chunk_size_pulls_the_overlap_down_with_it(qapp):
    dialog = KnowledgeBaseFormDialog(embedding_profile_names=["mock"])
    dialog._chunk_size_spin.setValue(2000)
    dialog._chunk_overlap_spin.setValue(1500)

    dialog._chunk_size_spin.setValue(500)

    assert dialog._chunk_overlap_spin.value() <= 499
    assert dialog.result_config().chunk_overlap < dialog.result_config().chunk_size
