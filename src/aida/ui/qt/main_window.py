"""``MainWindow`` (PLAN.md Phase 5): assembles every widget built so far
into the actual application window and is the one place allowed to know
about both Qt *and* the session/config/persistence layers at once — every
other widget in ``aida.ui.qt`` only knows plain data and signals.

Responsibilities, each mapping directly to a Phase 5 acceptance criterion:

- start a session via ``ChatBridge`` on construction and wire its signals
  to ``ChatPanel``/``InputBox`` (the flagship demo path)
- workspace switch -> confirm -> start a *new* conversation in that
  workspace
- profile switch -> ``ChatBridge.switch_profile`` (history carries over)
- conversations sidebar -> resume/delete/cleanup, backed by
  ``aida.persistence``
- Settings dialog -> font size takes effect immediately, without restart
- window geometry/font size persisted to ``AppConfig`` on close
"""

from __future__ import annotations

from pathlib import Path

from aida.config.logging_setup import configure_logging, get_logger
from aida.config.paths import ensure_records_dir
from aida.config.settings import Settings, save_app_config
from aida.mcp.groups import resolve_group
from aida.persistence.cleanup import delete_conversation, list_conversations_older_than
from aida.persistence.store import ArtifactRecord, ConversationStore
from aida.ui.qt._qt import (
    QAction,
    QApplication,
    QMainWindow,
    QMessageBox,
    QSplitter,
    Qt,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from aida.ui.qt.artifact_widgets import FileArtifactCard, InlineImageWidget
from aida.ui.qt.bridge import AsyncLoopThread, ChatBridge
from aida.ui.qt.chat_panel import ChatPanel
from aida.ui.qt.conversations_sidebar import ConversationsSidebar
from aida.ui.qt.icon import app_icon
from aida.ui.qt.input_box import InputBox
from aida.ui.qt.selectors import FolderDisplay, McpQuickPanel, ProfileSelector, WorkspaceSelector
from aida.ui.qt.settings_dialog import SettingsDialog
from aida.ui.qt.window_state import apply_font_size, apply_window_state, capture_window_state
from aida.workspace.workspaces import (
    WorkspaceConfig,
    get_workspace,
    list_workspace_names,
    save_workspace,
)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        loop_thread: AsyncLoopThread,
        *,
        start_kwargs: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._logger = get_logger("ui")
        self._loop_thread = loop_thread
        self._current_workspace_config: WorkspaceConfig | None = None
        self.setWindowTitle("AIDA")
        self.setWindowIcon(app_icon())

        self._build_ui()
        self._wire_ui_signals()

        self.bridge = ChatBridge(loop_thread, self)
        self._wire_bridge_signals()
        self.bridge.start(settings, **(start_kwargs or {}))

        apply_window_state(self, settings.app)
        self._refresh_conversations_sidebar()
        self._refresh_workspace_selector()
        self._refresh_profile_selector()

    # --- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        toolbar = QToolBar("Session", self)
        self.addToolBar(toolbar)
        self.workspace_selector = WorkspaceSelector(self)
        toolbar.addWidget(self.workspace_selector)
        self.profile_selector = ProfileSelector(self)
        toolbar.addWidget(self.profile_selector)

        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        toolbar.addAction(settings_action)

        self.sidebar = ConversationsSidebar(self)
        self.chat_panel = ChatPanel(self)
        self.input_box = InputBox(self)
        self.folder_display = FolderDisplay(self)
        self.mcp_panel = McpQuickPanel(self)

        chat_column = QWidget(self)
        chat_layout = QVBoxLayout(chat_column)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.addWidget(self.chat_panel, stretch=1)
        chat_layout.addWidget(self.input_box)

        session_column = QWidget(self)
        session_layout = QVBoxLayout(session_column)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.addWidget(self.folder_display)
        session_layout.addWidget(self.mcp_panel)
        session_layout.addStretch(1)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(chat_column)
        splitter.addWidget(session_column)
        splitter.setStretchFactor(1, 1)  # chat column gets the extra space
        self.setCentralWidget(splitter)

        self.statusBar().showMessage("Starting session…")

    def _wire_ui_signals(self) -> None:
        self.input_box.send_requested.connect(self._on_send_requested)
        self.input_box.folder_dropped.connect(self._on_folder_dropped)
        self.sidebar.resume_requested.connect(self._on_resume_requested)
        self.sidebar.delete_requested.connect(self._on_delete_requested)
        self.sidebar.cleanup_requested.connect(self._on_cleanup_requested)
        self.workspace_selector.workspace_changed.connect(self._on_workspace_changed)
        self.profile_selector.profile_changed.connect(self._on_profile_changed)
        self.folder_display.source_folders_changed.connect(self._on_source_folders_changed)
        self.folder_display.target_folder_changed.connect(self._on_target_folder_changed)
        self.folder_display.sidecar_folder_name_changed.connect(self._on_sidecar_folder_name_changed)
        self.folder_display.save_to_workspace_requested.connect(self._on_save_folders_to_workspace)

    def _wire_bridge_signals(self) -> None:
        self.bridge.session_ready.connect(self._on_session_ready)
        self.bridge.startup_failed.connect(self._on_startup_failed)
        self.bridge.event_received.connect(self.chat_panel.handle_event)
        self.bridge.turn_started.connect(lambda: self.input_box.set_busy(True))
        self.bridge.turn_finished.connect(lambda: self.input_box.set_busy(False))
        self.bridge.turn_failed.connect(self._on_turn_failed)
        self.bridge.confirmation_requested.connect(self._on_confirmation_requested)
        self.bridge.profile_switched.connect(self._on_profile_switched)
        self.input_box.cancel_requested.connect(self.bridge.cancel)
        self.profile_selector.profile_changed.connect(self.bridge.switch_profile)

    def _unwire_bridge_signals(self, bridge: ChatBridge) -> None:
        """Undo ``_wire_bridge_signals`` for a bridge being retired, in both
        directions: ``bridge.disconnect(self)`` drops the bridge's own
        signals into this window, and the two explicit calls drop the
        widget-to-bridge connections, where the *bridge* is the receiver and
        so isn't covered by that first call."""
        bridge.disconnect(self)
        self.input_box.cancel_requested.disconnect(bridge.cancel)
        self.profile_selector.profile_changed.disconnect(bridge.switch_profile)

    # --- session lifecycle -----------------------------------------------

    def _on_session_ready(self) -> None:
        session = self.bridge.session
        if session is None:
            # Defensive: every handler here reads through self.bridge, so a
            # signal that arrives while the current bridge has no session
            # (a superseded bridge finishing its start, a test driving the
            # handler directly) used to raise AttributeError straight out of
            # a Qt slot — leaving the window stuck on "Starting session…"
            # with the sidebar, MCP panel and folder display never
            # refreshed. _restart_session now disconnects superseded
            # bridges, so this should be unreachable; it stays as a guard
            # because a crash in a Qt slot is silent to the user.
            self._logger.debug("session_ready with no active session — ignoring")
            return
        self.statusBar().showMessage(f"Ready — {session.profile_name}", 5000)
        if session.recorder is not None:
            history = [m for m in session.messages if m.role != "system"]
            if history:
                self.chat_panel.load_history(history)
                self._load_resumed_artifacts(session.recorder.conversation_id)
        self._refresh_mcp_panel()
        self._refresh_folder_display()
        self._refresh_conversations_sidebar()
        self._save_last_session_selection()

    def _save_last_session_selection(self) -> None:
        """"App does not seem to open with last set of settings": persists
        the now-active workspace/profile to ``AppConfig`` every time a
        session actually starts, so the *next* launch of ``aida-gui`` (with
        no --workspace/--profile flag — see ``aida.ui.qt.app.main``) reopens
        the same one instead of landing on "No profile given". Saved
        immediately rather than only on window close, so it survives a
        crash or a force-quit."""
        session = self.bridge.session
        if session is None:
            return
        workspace_name = session.recorder.workspace_name if session.recorder else None
        self.settings.app.last_workspace_name = workspace_name
        self.settings.app.last_profile_name = session.profile_name
        save_app_config(self.settings.app)

    def _load_resumed_artifacts(self, conversation_id: str) -> None:
        """Acceptance criterion "resume yesterday's conversation... images
        still display": ``ChatPanel.load_history`` only replays the text
        messages (see its docstring) since the original streaming
        ``ImageArtifactCreated``/``FileArtifactCreated`` events aren't
        persisted/replayed — artifact *metadata* is, in the ``artifacts``
        table, so this re-derives one widget per still-present file
        directly from there. v1 simplicity: appended after the whole text
        history rather than interleaved at each artifact's original
        position (``ArtifactRecord`` has no ``seq``/position to interleave
        by); still satisfies "images still display" on resume."""
        store = ConversationStore()
        try:
            records: list[ArtifactRecord] = store.load_artifacts(conversation_id)
        finally:
            store.close()
        for record in records:
            if not record.path or not Path(record.path).exists():
                continue  # sidecar file moved/deleted since it was recorded
            if record.kind == "ImageArtifact":
                widget = InlineImageWidget(
                    path=record.path,
                    artifact_id=record.id,
                    mime_type=record.mime_type or "image/png",
                    parent=self.chat_panel,
                )
            elif record.kind == "FileArtifact":
                widget = FileArtifactCard(
                    path=record.path, artifact_id=record.id, mime_type=record.mime_type, parent=self.chat_panel
                )
            else:
                continue
            self.chat_panel.add_artifact_widget(widget)

    def _on_startup_failed(self, message: str) -> None:
        self.statusBar().showMessage("Startup failed", 5000)
        QMessageBox.critical(self, "Could Not Start Session", message)

    def _on_turn_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Turn Failed", message)

    def _on_confirmation_requested(self, request, future) -> None:
        """Handles ``ChatBridge.confirmation_requested`` (Phase 6): shows a
        real modal dialog for a ``SafetyGuard`` confirmation and resolves
        the paired ``concurrent.futures.Future`` with the answer, unblocking
        the background asyncio thread's ``await`` in ``ChatBridge._confirm``.
        A plain ``concurrent.futures.Future`` is safe to resolve from this
        (the Qt) thread — see that method's docstring."""
        answer = QMessageBox.question(
            self,
            "Confirm Action",
            request.detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        future.set_result(answer == QMessageBox.StandardButton.Yes)

    def _on_send_requested(self, text: str) -> None:
        attachments = self.input_box.attached_paths()
        self.input_box.clear_attachments()
        self.chat_panel.add_user_message(text)
        try:
            outgoing, failures = self._augment_with_attachments(text, attachments)
        except Exception as exc:  # noqa: BLE001 - belt-and-suspenders: see _read_attachment_for_model's
            # docstring for the real bug this whole two-layer defense is
            # guarding against — a send must never silently vanish.
            self._logger.error("unexpected error augmenting message with attachments %r: %s", attachments, exc)
            QMessageBox.warning(self, "Attachment Not Sent", f"Could not prepare the message to send:\n\n{exc}")
            return
        if failures:
            names = ", ".join(Path(p).name for p in failures)
            self.statusBar().showMessage(f"Could not read attachment(s): {names} — see chat for details", 8000)
        self.bridge.send(outgoing)

    def _augment_with_attachments(self, text: str, attachments: list[str]) -> tuple[str, list[str]]:
        """Drag & drop onto the chat -- "included in the next sent message"
        (PLAN.md Phase 6): each attached path's content is read directly via
        ``aida.documents.readers`` (dispatched by extension, same as the
        agent's own ``read_file``/document tools) and appended to the
        outgoing message text, so the model sees it without needing to call
        a tool itself. Deliberately *not* run through ``SafetyGuard`` — these
        are files the human explicitly picked via a native file dialog or
        drag-and-drop from their own machine, not a path the agent is
        choosing to access on its own; the safety model gates the agent's
        own filesystem actions (PLAN.md's "always-confirm-outside-allowed"
        rule), not a human manually attaching a file they already have
        access to.

        Returns ``(message_text, failed_paths)`` — a failed read still gets
        an inline "could not read" note in the message (so both the human
        and the model see it plainly) rather than being silently dropped or
        aborting the whole send; ``failed_paths`` is just so the caller can
        also flag it in the status bar without re-parsing the text."""
        if not attachments:
            return text, []
        sections = [text] if text else []
        failures: list[str] = []
        for path in attachments:
            rendered, ok = self._read_attachment_for_model(path)
            sections.append(rendered)
            if not ok:
                failures.append(path)
        return "\n\n".join(sections), failures

    def _read_attachment_for_model(self, path: str) -> tuple[str, bool]:
        """Returns ``(rendered_text, ok)`` — never raises. **Real bug this
        guards against**: this used to catch only
        ``(UnsupportedDocumentFormatError, OSError)``; an attached PDF on a
        machine missing the optional ``docs`` extra (``pymupdf`` et al, only
        imported lazily inside each reader) raised a bare
        ``ModuleNotFoundError`` instead, which propagated all the way up
        through the Qt slot handling Send, uncaught — the augmented message
        never reached ``ChatBridge.send`` at all, so *nothing* was sent to
        the model, yet the user's own text still appeared in the chat panel
        (added before this ran) looking exactly like a normal sent message.
        The user's next plain-text message then reached the model with zero
        file context, and the model tried to *find* "the paper" itself via
        read_file/find_files/search_text calls against guessed paths (home
        directory, "/", ...) — each one gated by ``SafetyGuard``'s
        outside-allowed-folders confirmation, which is what looked like a
        confusing string of repeated confirmation prompts with no clear
        file reference. Catching broadly here — and telling the model (and
        the user, via the status bar) plainly that the read failed instead
        of just not happening — is the fix."""
        from aida.documents.readers import read_document

        name = Path(path).name
        try:
            from aida.artifacts.policy import describe_for_model

            artifacts = read_document(path)
            body = "\n\n".join(describe_for_model(a) for a in artifacts)
        except Exception as exc:  # noqa: BLE001 - see docstring: must never propagate past this method
            self._logger.warning("could not read attachment %s: %s", path, exc, exc_info=True)
            detail = str(exc)
            if isinstance(exc, ImportError):
                detail += (
                    " — the optional 'docs' extra may not be installed; run "
                    'pip install -e ".[docs]" (or ".[dev,gui,docs]") in your AIDA environment'
                )
            return f"--- Attached file: {name} ---\n[could not read: {detail}]\n--- End of {name} ---", False
        return f"--- Attached file: {name} ---\n{body}\n--- End of {name} ---", True

    def _on_folder_dropped(self, folder: str) -> None:
        """A folder (rather than a file) dropped onto the chat: offer to add
        it as a source folder, per PLAN.md's "folder drop -> confirmation
        dialog offering 'add as allowed/source folder'". Persisting it
        (and the running session's ``SafetyGuard`` actually honoring it)
        both go through the same existing "Save to Workspace" + restart
        path as any other folder edit here — see ``_on_save_folders_to_workspace``
        and ``FolderDisplay``'s docstring."""
        if self._current_workspace_config is None:
            self.statusBar().showMessage(
                "No active workspace — create or switch to one to add source folders", 5000
            )
            return
        answer = QMessageBox.question(
            self,
            "Add Source Folder",
            f"Add {folder!r} as a source folder for workspace {self._current_workspace_config.name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if folder not in self._current_workspace_config.source_folders:
            self._current_workspace_config.source_folders.append(folder)
        self.folder_display.set_folders(
            source_folders=self._current_workspace_config.source_folders,
            target_folder=self._current_workspace_config.target_folder,
            sidecar_folder_name=self._current_workspace_config.sidecar_folder_name,
        )
        self.statusBar().showMessage(
            f"Added {folder} — click 'Save to Workspace', then switch/resume to apply it to this session", 8000
        )

    # --- workspace / profile switching ------------------------------------

    def _on_workspace_changed(self, name: str) -> None:
        answer = QMessageBox.question(
            self,
            "Switch Workspace",
            f"Switch to workspace {name or '(no workspace)'}? This starts a new conversation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._refresh_workspace_selector()  # revert the dropdown to the current workspace
            return
        self._restart_session(workspace_name=name or None, profile_name=None, resume_conversation_id=None)

    def _on_profile_changed(self, name: str) -> None:
        # aida.ui.qt.bridge.ChatBridge.switch_profile already does the
        # actual work (connected directly in _wire_bridge_signals); nothing
        # else to do here — kept as its own handler for symmetry/future use
        # (e.g. updating a "current profile" status label).
        pass

    def _on_profile_switched(self, _name: str) -> None:
        self._save_last_session_selection()

    def _restart_session(
        self, *, workspace_name: str | None, profile_name: str | None, resume_conversation_id: str | None
    ) -> None:
        old_bridge = self.bridge
        old_bridge.shutdown()  # waits for an in-flight start, then closes it
        # Retire the old bridge completely before the new one exists: its
        # signals are still connected to these same handlers, and every
        # handler resolves state through `self.bridge`. A superseded bridge
        # that emitted afterwards would therefore drive the window from the
        # *wrong* session's data. deleteLater() (rather than just leaving it
        # parented to the window) also stops one ChatBridge accumulating per
        # workspace switch for the life of the app.
        self._unwire_bridge_signals(old_bridge)
        old_bridge.setParent(None)
        old_bridge.deleteLater()

        self.chat_panel.clear()
        self.statusBar().showMessage("Starting session…")
        self.bridge = ChatBridge(self._loop_thread, self)
        self._wire_bridge_signals()
        kwargs: dict = {}
        if workspace_name:
            kwargs["workspace_name"] = workspace_name
        if profile_name:
            kwargs["profile_name"] = profile_name
        if resume_conversation_id:
            kwargs["resume_conversation_id"] = resume_conversation_id
        self.bridge.start(self.settings, **kwargs)

    # --- conversations sidebar ---------------------------------------------

    def _refresh_conversations_sidebar(self) -> None:
        store = ConversationStore()
        try:
            self.sidebar.set_conversations(store.list_conversations())
        finally:
            store.close()

    def _on_resume_requested(self, conversation_id: str) -> None:
        self._restart_session(workspace_name=None, profile_name=None, resume_conversation_id=conversation_id)

    def _on_delete_requested(self, conversation_id: str) -> None:
        store = ConversationStore()
        try:
            records_dir = ensure_records_dir(self.settings.app.records_dir)
            delete_conversation(store, conversation_id, records_dir=records_dir)
        finally:
            store.close()
        self._refresh_conversations_sidebar()

    def _on_cleanup_requested(self, days: int) -> None:
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        store = ConversationStore()
        try:
            records_dir = ensure_records_dir(self.settings.app.records_dir)
            stale = list_conversations_older_than(store, cutoff)
            for summary in stale:
                delete_conversation(store, summary.id, records_dir=records_dir)
        finally:
            store.close()
        self._refresh_conversations_sidebar()

    # --- selectors / panels ------------------------------------------------

    def _refresh_workspace_selector(self) -> None:
        current = self.bridge.session.recorder.workspace_name if self.bridge.session else None
        self.workspace_selector.set_workspaces(list_workspace_names(self.settings), current=current)

    def _refresh_profile_selector(self) -> None:
        current = self.bridge.session.profile_name if self.bridge.session else None
        self.profile_selector.set_profiles(sorted(self.settings.providers.profiles), current=current)

    def _refresh_mcp_panel(self) -> None:
        session = self.bridge.session
        workspace_name = session.recorder.workspace_name if session and session.recorder else None
        group_name = None
        enabled: list[str] = []
        if workspace_name:
            workspace = get_workspace(self.settings, workspace_name)
            if workspace is not None:
                group_name = workspace.mcp_group
                enabled = [s.name for s in resolve_group(self.settings.mcp, workspace.mcp_group)]
        all_server_names = sorted(self.settings.mcp.servers)
        self.mcp_panel.set_servers(all_server_names, enabled=enabled, group_name=group_name)

    def _refresh_folder_display(self) -> None:
        """Loads the active workspace's actual source/target folders into
        ``FolderDisplay`` (previously constructed but never populated — a
        real gap in the "Source/target folder display" task item, caught by
        actually reading this file end-to-end rather than trusting the
        widget's own passing unit tests). No workspace active ->
        ``_current_workspace_config`` is ``None`` and the panel just shows
        empty placeholders; ``Change``/``Save to Workspace`` edit an
        in-memory copy of that ``WorkspaceConfig`` until explicitly saved."""
        session = self.bridge.session
        workspace_name = session.recorder.workspace_name if session and session.recorder else None
        self._current_workspace_config = get_workspace(self.settings, workspace_name) if workspace_name else None
        if self._current_workspace_config is not None:
            self.folder_display.set_folders(
                source_folders=self._current_workspace_config.source_folders,
                target_folder=self._current_workspace_config.target_folder,
                sidecar_folder_name=self._current_workspace_config.sidecar_folder_name,
            )
        else:
            self.folder_display.set_folders(source_folders=[], target_folder=None, sidecar_folder_name="figures")

    def _on_source_folders_changed(self, folders: list[str]) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.source_folders = list(folders)

    def _on_target_folder_changed(self, folder: str) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.target_folder = folder

    def _on_sidecar_folder_name_changed(self, name: str) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.sidecar_folder_name = name

    def _on_save_folders_to_workspace(self) -> None:
        if self._current_workspace_config is None:
            self.statusBar().showMessage("No active workspace to save folders to", 5000)
            return
        save_workspace(self.settings, self._current_workspace_config)
        self.statusBar().showMessage(f"Saved folders to workspace {self._current_workspace_config.name}", 5000)

    # --- settings ------------------------------------------------------------

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.settings.app, self.settings.providers.profiles, self)
        if not dialog.exec():
            return
        self.settings.app = dialog.updated_app_config()
        apply_font_size(QApplication.instance(), self.settings.app)  # takes effect immediately, no restart
        # "Change the debug level so I can help with console report" (bug
        # report): configure_logging is safe to call again — it only
        # adjusts the "aida" logger tree's level, doesn't duplicate
        # handlers (see its docstring) — so a log-level change here takes
        # effect immediately, same as the font size above, instead of only
        # applying on the next launch.
        configure_logging(self.settings.app.log_level)
        save_app_config(self.settings.app)

    # --- shutdown ----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        capture_window_state(self, self.settings.app)
        save_app_config(self.settings.app)
        self.bridge.shutdown()
        super().closeEvent(event)


__all__ = ["MainWindow"]
