"""``KnowledgeManagementDialog`` (Phase 8, planning/phase08_rag.md): add/
edit/remove RAG knowledge bases and rebuild/update their indexes, entirely
from the GUI, no manual ``knowledge.yaml`` editing.

Structural precedent: mirrors ``aida.ui.qt.mcp_management_dialog``'s split
between config CRUD (persisted here, directly, the moment it happens — same
"no deferred Save to Workspace step" reasoning) and live actions (build/
update) that go through ``ChatBridge`` so a real embedding pass never blocks
the Qt thread. ``KnowledgeBaseFormDialog`` mirrors ``ServerFormDialog``'s
self-contained ``QFormLayout`` + ``QDialogButtonBox`` shape; source folders
use a one-per-line ``QPlainTextEdit`` (``ServerFormDialog``'s own
convention for its ``args`` field) rather than reusing
``aida.ui.qt.selectors.FolderDisplay`` — that widget is tightly coupled to
the *active chat session's* workspace state (its Remove/Change buttons
mutate live session folders and its own "Save to Workspace" signal), not a
generic reusable folder-list editor for arbitrary config.
"""

from __future__ import annotations

import contextlib

from aida.config.paths import knowledge_db_path
from aida.config.settings import KnowledgeBaseConfig, Settings, save_knowledge_config
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.ingest import IngestResult, normalize_source_folder
from aida.ui.qt._qt import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
    QWidget,
)


def _chunk_count_for(name: str) -> int:
    conn = kb_index.connect(knowledge_db_path(name))
    try:
        return kb_index.chunk_count(conn)
    finally:
        conn.close()


# --- Add/Edit knowledge base sub-dialog --------------------------------------


class KnowledgeBaseFormDialog(QDialog):
    """Add (``kb=None``) or edit (``kb`` given) one knowledge base."""

    def __init__(
        self,
        *,
        kb: KnowledgeBaseConfig | None = None,
        embedding_profile_names: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = kb is not None
        self.setWindowTitle("Edit Knowledge Base" if self._is_edit else "Add Knowledge Base")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(kb.name if kb else "", self)
        self._name_edit.setReadOnly(self._is_edit)  # name is the identity; not renameable in-place
        form.addRow("Name:", self._name_edit)

        self._folders_edit = QPlainTextEdit("\n".join(kb.source_folders) if kb else "", self)
        self._folders_edit.setPlaceholderText(
            "One folder or individual file per line — an Obsidian vault is just a folder of .md files"
        )
        form.addRow("Source folders:", self._folders_edit)

        self._profile_combo = QComboBox(self)
        self._profile_combo.addItems(embedding_profile_names)
        if kb and kb.embedding_profile:
            index = self._profile_combo.findText(kb.embedding_profile)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)
        form.addRow("Embedding profile:", self._profile_combo)

        self._chunk_size_spin = QSpinBox(self)
        self._chunk_size_spin.setRange(100, 100_000)
        self._chunk_size_spin.setValue(kb.chunk_size if kb else 1000)
        form.addRow("Chunk size:", self._chunk_size_spin)

        # Overlap must stay strictly below chunk size: chunking advances by
        # (chunk_size - overlap) characters per piece, so an equal-or-larger
        # overlap never advances and spins forever, eating memory — and
        # since ingest runs on the shared AsyncLoopThread, it takes the chat
        # session down with it. These two spin boxes were independently
        # ranged before (size down to 100, overlap up to 100,000), so that
        # state was two clicks away. Capping overlap against the current
        # chunk size makes it unreachable from this dialog rather than
        # merely warned about on OK.
        self._chunk_overlap_spin = QSpinBox(self)
        self._chunk_overlap_spin.setRange(0, self._chunk_size_spin.value() - 1)
        self._chunk_overlap_spin.setValue(kb.chunk_overlap if kb else 150)
        self._chunk_size_spin.valueChanged.connect(self._on_chunk_size_changed)
        form.addRow("Chunk overlap:", self._chunk_overlap_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_chunk_size_changed(self, value: int) -> None:
        """Keep the overlap cap in step with the chunk size — see where the
        two spin boxes are built for why the relationship is enforced
        rather than validated."""
        self._chunk_overlap_spin.setMaximum(max(0, value - 1))

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A knowledge base needs a name.")
            return
        if not self._profile_combo.currentText():
            QMessageBox.warning(self, "Embedding Profile Required", "Configure an embedding profile first (Providers… dialog).")
            return
        self.accept()

    def result_config(self) -> KnowledgeBaseConfig:
        # Bug report: a folder path pasted from a file manager's "Copy as
        # URI" action (e.g. Obsidian) comes through as `file:///...`, which
        # silently failed to resolve as a directory at ingest time with no
        # error anywhere. Normalizing here means a saved config always
        # holds a plain path — the "Details" panel and knowledge.yaml both
        # show something a user recognizes, not a raw URI.
        source_folders = [
            normalize_source_folder(line) for line in self._folders_edit.toPlainText().splitlines() if line.strip()
        ]
        return KnowledgeBaseConfig(
            name=self._name_edit.text().strip(),
            source_folders=source_folders,
            embedding_profile=self._profile_combo.currentText() or None,
            chunk_size=self._chunk_size_spin.value(),
            chunk_overlap=self._chunk_overlap_spin.value(),
        )


# --- Main dialog ---------------------------------------------------------


class KnowledgeManagementDialog(QDialog):
    def __init__(self, settings: Settings, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Knowledge Bases")
        self.resize(680, 460)
        self._settings = settings
        self._bridge = bridge

        outer = QHBoxLayout(self)

        left = QVBoxLayout()
        self._kb_list = QListWidget(self)
        self._kb_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._kb_list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        left.addWidget(self._kb_list)

        buttons_col = QVBoxLayout()
        for label, handler in [
            ("Add…", self._on_add),
            ("Edit…", self._on_edit),
            ("Remove…", self._on_remove),
            ("Rebuild", self._on_rebuild),
            ("Update", self._on_update),
        ]:
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            buttons_col.addWidget(button)
        buttons_col.addStretch(1)
        left.addLayout(buttons_col)
        outer.addLayout(left, stretch=1)

        right = QVBoxLayout()
        details_box = QGroupBox("Details", self)
        details_layout = QVBoxLayout(details_box)
        self._details_label = QLabel(self)
        self._details_label.setWordWrap(True)
        self._details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_label)
        details_layout.addStretch(1)
        right.addWidget(details_box, stretch=1)

        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        right.addWidget(self._status_label)

        outer.addLayout(right, stretch=2)

        if self._bridge is not None:
            self._bridge.kb_ingest_finished.connect(self._on_ingest_finished)
            self._bridge.kb_ingest_failed.connect(self._on_ingest_failed)

        self._refresh_kb_list()

    def done(self, result: int) -> None:
        """Disconnect from ``self._bridge`` before closing — same leaked-
        connection fix as ``McpManagementDialog.done`` (see its docstring):
        without this, reopening this dialog N times and then rebuilding
        once would pop N status updates from N still-connected, already-
        closed instances."""
        if self._bridge is not None:
            for signal, slot in (
                (self._bridge.kb_ingest_finished, self._on_ingest_finished),
                (self._bridge.kb_ingest_failed, self._on_ingest_failed),
            ):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(slot)
        super().done(result)

    # --- rendering -----------------------------------------------------------

    def _configs(self) -> dict[str, KnowledgeBaseConfig]:
        return self._settings.knowledge.knowledge_bases

    def _selected_name(self) -> str | None:
        item = self._kb_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_kb_list(self) -> None:
        previous = self._selected_name()
        self._kb_list.clear()
        for name in sorted(self._configs()):
            count = _chunk_count_for(name)
            item = QListWidgetItem(f"{name}  ({count} chunk(s))", self._kb_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._kb_list.setCurrentItem(item)
        if self._kb_list.currentItem() is None and self._kb_list.count():
            self._kb_list.setCurrentRow(0)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        name = self._selected_name()
        kb = self._configs().get(name) if name else None
        if kb is None:
            self._details_label.setText("(no knowledge base selected)")
            return
        detail_lines = [
            f"name: {kb.name}",
            f"source_folders: {', '.join(kb.source_folders) or '(none)'}",
            f"embedding_profile: {kb.embedding_profile or '(none)'}",
            f"chunk_size: {kb.chunk_size}",
            f"chunk_overlap: {kb.chunk_overlap}",
            f"indexed chunks: {_chunk_count_for(kb.name)}",
        ]
        self._details_label.setText("\n".join(detail_lines))

    # --- add/edit/remove ---------------------------------------------------

    def _embedding_profile_names(self) -> list[str]:
        return sorted(self._settings.providers.embedding_profiles)

    def _on_add(self) -> None:
        if not self._embedding_profile_names():
            # U2 fixed the actual dead end this used to be ("Configure an
            # embedding profile in providers.yaml first" — with no GUI path
            # to do that): offer to open the new Providers… dialog right
            # here instead of sending the user to a text editor.
            answer = QMessageBox.question(
                self,
                "No Embedding Profiles",
                "No embedding profiles are configured yet. Open the Providers… dialog to add one now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                from aida.ui.qt.profiles_dialog import ProfilesDialog

                ProfilesDialog(self._settings, self._bridge, self).exec()
            if not self._embedding_profile_names():
                return
        dialog = KnowledgeBaseFormDialog(embedding_profile_names=self._embedding_profile_names(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.result_config()
        if config.name in self._configs():
            QMessageBox.warning(self, "Already Exists", f"A knowledge base named {config.name!r} already exists.")
            return
        self._settings.knowledge.knowledge_bases[config.name] = config
        save_knowledge_config(self._settings.knowledge)
        self._refresh_kb_list()

    def _on_edit(self) -> None:
        name = self._selected_name()
        kb = self._configs().get(name) if name else None
        if kb is None:
            return
        dialog = KnowledgeBaseFormDialog(kb=kb, embedding_profile_names=self._embedding_profile_names(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_config()
        self._settings.knowledge.knowledge_bases[name] = updated
        save_knowledge_config(self._settings.knowledge)
        self._refresh_kb_list()

    def _on_remove(self) -> None:
        # Bug report: "when I delete source, is its data removed? Warning
        # states that 'its index file is left on disk' which is ambiguous
        # and not clear when and how will disk be cleaned up." Three-way
        # choice makes cleanup an explicit, opt-in action instead of a
        # permanent, unexplained leftover file.
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Knowledge Base",
            f"Remove knowledge base {name!r}?\n\n"
            "Yes — remove it and delete its index file from disk.\n"
            "No — remove it from configuration but keep the index file "
            f"({knowledge_db_path(name)}) in case you re-add it later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        del self._settings.knowledge.knowledge_bases[name]
        save_knowledge_config(self._settings.knowledge)
        if answer == QMessageBox.StandardButton.Yes:
            knowledge_db_path(name).unlink(missing_ok=True)
        self._refresh_kb_list()

    # --- build/update --------------------------------------------------------

    def _run_ingest(self, *, rebuild: bool) -> None:
        name = self._selected_name()
        kb = self._configs().get(name) if name else None
        if kb is None or self._bridge is None:
            return
        if not kb.embedding_profile:
            QMessageBox.warning(self, "No Embedding Profile", f"Knowledge base {kb.name!r} has no embedding_profile configured.")
            return
        profile = self._settings.providers.embedding_profiles.get(kb.embedding_profile)
        if profile is None:
            QMessageBox.warning(
                self, "Unknown Embedding Profile",
                f"Knowledge base {kb.name!r} references unknown embedding profile {kb.embedding_profile!r}.",
            )
            return
        self._status_label.setText(f"{'Rebuilding' if rebuild else 'Updating'} {kb.name!r}…")
        if rebuild:
            self._bridge.rebuild_knowledge_base(kb, profile)
        else:
            self._bridge.update_knowledge_base(kb, profile)

    def _on_rebuild(self) -> None:
        self._run_ingest(rebuild=True)

    def _on_update(self) -> None:
        self._run_ingest(rebuild=False)

    # --- bridge signal handlers --------------------------------------------

    def _on_ingest_finished(self, name: str, result: IngestResult) -> None:
        self._status_label.setText(
            f"{name}: added {len(result.added_files)}, updated {len(result.updated_files)}, "
            f"removed {len(result.removed_files)}, skipped {len(result.skipped_files)} "
            f"({result.chunk_count} chunk(s) written this pass)"
        )
        if result.missing_folders:
            # Real-use bug: a source folder that doesn't resolve to a real
            # directory (typo, deleted folder, or a `file://` URI pasted
            # from a file manager) used to fail with zero indication why —
            # "added 0" and nothing else. This is the fix's user-facing half.
            QMessageBox.warning(
                self,
                "Source Folder Not Found",
                f"{name}: the following source folder(s) don't exist — nothing was indexed from them:\n\n"
                + "\n".join(result.missing_folders),
            )
        self._refresh_kb_list()

    def _on_ingest_failed(self, name: str, error: str) -> None:
        self._status_label.setText(f"{name}: FAILED — {error}")
        QMessageBox.warning(self, "Knowledge Base Ingest Failed", f"{name}: {error}")


__all__ = ["KnowledgeBaseFormDialog", "KnowledgeManagementDialog"]
