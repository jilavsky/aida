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

from aida import __version__ as AIDA_VERSION
from aida.coding.runner import DEFAULT_RUN_TIMEOUT_SECONDS
from aida.config.logging_setup import configure_logging, get_logger
from aida.config.paths import config_dir, ensure_records_dir, ensure_scratch_dir, skills_dir
from aida.config.settings import QuickTask, Settings, save_app_config
from aida.core.cost import estimate_cost_usd
from aida.core.events import ContextTrimmed
from aida.persistence.cleanup import delete_conversation, list_conversations_older_than
from aida.persistence.store import ArtifactRecord, ConversationStore
from aida.providers.base import ImageRef
from aida.ui.qt._qt import (
    QAction,
    QApplication,
    QDesktopServices,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    Qt,
    QToolBar,
    QUrl,
    QVBoxLayout,
    QWidget,
)
from aida.ui.qt.bridge import AsyncLoopThread, ChatBridge
from aida.ui.qt.chat_panel import ChatPanel
from aida.ui.qt.code_editor_dialog import CodeEditorDialog
from aida.ui.qt.conversations_sidebar import ConversationsSidebar
from aida.ui.qt.icon import app_icon
from aida.ui.qt.input_box import InputBox
from aida.ui.qt.knowledge_management_dialog import KnowledgeManagementDialog
from aida.ui.qt.mcp_management_dialog import McpManagementDialog
from aida.ui.qt.onboarding_dialog import OnboardingDialog
from aida.ui.qt.profiles_dialog import ProfilesDialog
from aida.ui.qt.quick_tasks_panel import QuickTaskData, QuickTasksPanel
from aida.ui.qt.selectors import FolderDisplay, McpQuickPanel, ProfileSelector, WorkspaceSelector
from aida.ui.qt.settings_dialog import SettingsDialog
from aida.ui.qt.window_state import apply_font_size, apply_window_state, capture_window_state
from aida.ui.qt.workspace_management_dialog import WorkspaceManagementDialog
from aida.workspace.safety import relaxed_mode_warning_if_newly_enabled
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
        self._build_menu_bar()

        toolbar = QToolBar("Session", self)
        self.addToolBar(toolbar)
        self.workspace_selector = WorkspaceSelector(self)
        toolbar.addWidget(self.workspace_selector)
        self.profile_selector = ProfileSelector(self)
        toolbar.addWidget(self.profile_selector)

        # Bug report: "How do I create a new chat within same Workspace?
        # ... something which will not contain the history from prior
        # chat." Previously the only way to reset history was switching
        # workspaces (or profiles, which explicitly keep history) —
        # _on_new_chat_requested reuses the same _restart_session machinery
        # pinned to the workspace/profile already active.
        new_chat_action = QAction("New Chat", self)
        new_chat_action.triggered.connect(self._on_new_chat_requested)
        toolbar.addAction(new_chat_action)

        # Phase 9: code templates/editor/execution.
        code_editor_action = QAction("Code Editor…", self)
        code_editor_action.triggered.connect(self.open_code_editor_dialog)
        toolbar.addAction(code_editor_action)

        mcp_action = QAction("MCP Servers…", self)
        mcp_action.triggered.connect(self.open_mcp_management_dialog)
        toolbar.addAction(mcp_action)

        knowledge_action = QAction("Knowledge Bases…", self)
        knowledge_action.triggered.connect(self.open_knowledge_management_dialog)
        toolbar.addAction(knowledge_action)

        # U2/U1: provider/embedding profiles and workspaces were previously
        # only editable by hand-editing providers.yaml/workspaces.yaml — the
        # two config objects everything else (a session, a workspace, a
        # knowledge base) ultimately depends on.
        providers_action = QAction("Providers…", self)
        providers_action.triggered.connect(self.open_profiles_dialog)
        toolbar.addAction(providers_action)

        workspaces_action = QAction("Workspaces…", self)
        workspaces_action.triggered.connect(self.open_workspace_management_dialog)
        toolbar.addAction(workspaces_action)

        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        toolbar.addAction(settings_action)

        self.sidebar = ConversationsSidebar(self)
        self.chat_panel = ChatPanel(self)
        self.input_box = InputBox(self)
        self.folder_display = FolderDisplay(self)
        self.mcp_panel = McpQuickPanel(self)
        self.quick_tasks_panel = QuickTasksPanel(self)

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
        session_layout.addWidget(self.quick_tasks_panel)
        session_layout.addStretch(1)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(chat_column)
        splitter.addWidget(session_column)
        splitter.setStretchFactor(1, 1)  # chat column gets the extra space
        self.setCentralWidget(splitter)

        self.statusBar().showMessage("Starting session…")
        # Bug report: "Can we get cost estimate... at this moment it is a
        # black box." A permanent status-bar label (not a transient
        # showMessage(), which this session-start message would clobber)
        # so it stays visible turn over turn — see _update_usage_label.
        self._usage_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._usage_label)

    def _build_menu_bar(self) -> None:
        """U7 paper cut: "A menu bar (File/Help) with 'Open config folder',
        'Open records folder', 'Documentation', 'About' — cheap
        discoverability for exactly the folders users otherwise have to
        find by hand." The app previously had no menu bar at all — every
        action lived on the toolbar."""
        file_menu = self.menuBar().addMenu("&File")
        open_config_action = QAction("Open Config Folder", self)
        open_config_action.triggered.connect(self._on_open_config_folder)
        file_menu.addAction(open_config_action)

        open_records_action = QAction("Open Records Folder", self)
        open_records_action.triggered.connect(self._on_open_records_folder)
        file_menu.addAction(open_records_action)

        # Bug report: "Agents seem to be saving temporary files ... in
        # random places" — this is the one well-known scratch folder every
        # MCP server subprocess now gets launched in (aida.core.session,
        # aida.mcp.server), surfaced the same "cheap discoverability" way as
        # the config/records folders above so the user can find and clean
        # it out without hunting for it.
        open_scratch_action = QAction("Open Scratch Folder", self)
        open_scratch_action.triggered.connect(self._on_open_scratch_folder)
        file_menu.addAction(open_scratch_action)

        help_menu = self.menuBar().addMenu("&Help")
        docs_action = QAction("Documentation", self)
        docs_action.triggered.connect(self._on_open_documentation)
        help_menu.addAction(docs_action)

        about_action = QAction("About AIDA", self)
        about_action.triggered.connect(self._on_show_about)
        help_menu.addAction(about_action)

    def _on_open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir())))

    def _on_open_records_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_records_dir(self.settings.app.records_dir))))

    def _on_open_scratch_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_scratch_dir(self.settings.app.scratch_dir))))

    def _on_open_documentation(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/jilavsky/aida"))

    def _on_show_about(self) -> None:
        QMessageBox.about(
            self,
            "About AIDA",
            f"AIDA — AI Data Assistant\nVersion {AIDA_VERSION}\n\n"
            "A local scientific agent workbench.\nhttps://github.com/jilavsky/aida",
        )

    def _wire_ui_signals(self) -> None:
        self.input_box.send_requested.connect(self._on_send_requested)
        self.input_box.folder_dropped.connect(self._on_folder_dropped)
        self.sidebar.resume_requested.connect(self._on_resume_requested)
        self.sidebar.delete_requested.connect(self._on_delete_requested)
        self.sidebar.delete_many_requested.connect(self._on_delete_many_requested)
        self.sidebar.cleanup_requested.connect(self._on_cleanup_requested)
        self.sidebar.rename_requested.connect(self._on_rename_requested)
        self.chat_panel.code_editor_requested.connect(self._on_code_editor_requested)
        self.chat_panel.open_in_code_editor_requested.connect(self._on_open_in_code_editor_requested)
        self.workspace_selector.workspace_changed.connect(self._on_workspace_changed)
        self.profile_selector.profile_changed.connect(self._on_profile_changed)
        self.folder_display.source_folders_changed.connect(self._on_source_folders_changed)
        self.folder_display.target_folder_changed.connect(self._on_target_folder_changed)
        self.folder_display.sidecar_folder_name_changed.connect(self._on_sidecar_folder_name_changed)
        self.folder_display.command_allowlist_changed.connect(self._on_command_allowlist_changed)
        self.folder_display.python_interpreter_changed.connect(self._on_python_interpreter_changed)
        self.folder_display.save_to_workspace_requested.connect(self._on_save_folders_to_workspace)
        self.mcp_panel.manage_requested.connect(self.open_mcp_management_dialog)
        self.mcp_panel.server_start_requested.connect(self._on_mcp_server_start_requested)
        self.mcp_panel.server_stop_requested.connect(self._on_mcp_server_stop_requested)
        self.quick_tasks_panel.task_selected.connect(self._on_quick_task_selected)
        self.quick_tasks_panel.tasks_changed.connect(self._on_quick_tasks_changed)

    def _wire_bridge_signals(self) -> None:
        # Every connection here must have *this window* as the receiver, so
        # that _unwire_bridge_signals' single `bridge.disconnect(self)` can
        # actually undo all of them. Two shapes used to slip through: a
        # connection whose receiver is a child widget
        # (`event_received -> self.chat_panel.handle_event` — receiver is
        # the chat panel, not the window) and a bare lambda (no receiver at
        # all). Both survived "retiring" a bridge, so a superseded session's
        # remaining events rendered into the *new* chat panel and flipped
        # the new input box's busy state. Plain bound methods on self keep
        # the receiver correct by construction.
        self.bridge.session_ready.connect(self._on_session_ready)
        self.bridge.startup_failed.connect(self._on_startup_failed)
        self.bridge.event_received.connect(self._on_event_received)
        self.bridge.turn_started.connect(self._on_turn_started)
        self.bridge.turn_finished.connect(self._on_turn_finished)
        self.bridge.turn_finished.connect(self._update_usage_label)
        self.bridge.turn_failed.connect(self._on_turn_failed)
        self.bridge.confirmation_requested.connect(self._on_confirmation_requested)
        self.bridge.profile_switched.connect(self._on_profile_switched)
        self.bridge.profile_switch_failed.connect(self._on_profile_switch_failed)
        self.bridge.mcp_server_status_changed.connect(self._on_mcp_server_status_changed)
        self.bridge.mcp_server_action_failed.connect(self._on_mcp_server_action_failed)
        self.input_box.cancel_requested.connect(self.bridge.cancel)
        self.profile_selector.profile_changed.connect(self.bridge.switch_profile)

    def _on_event_received(self, event: object) -> None:
        """Forward one ``AgentEvent`` to the chat panel.

        Deliberately a method on the window rather than
        ``bridge.event_received.connect(self.chat_panel.handle_event)``:
        that connection's receiver is the *chat panel*, so
        ``bridge.disconnect(self)`` never dropped it and a retired bridge
        kept painting into the live panel. See ``_wire_bridge_signals``."""
        if isinstance(event, ContextTrimmed):
            # B7: trimming used to be invisible in the GUI entirely (a log
            # line only) — the status bar is the same low-key channel
            # already used for "Ready — profile" / "Saved folders to
            # workspace X", so this doesn't interrupt the chat transcript
            # the way an inline notice would.
            turn_word = "turn" if event.dropped_turns == 1 else "turns"
            self.statusBar().showMessage(
                f"Context trimmed: dropped {event.dropped_turns} old {turn_word} "
                f"(~{event.estimated_tokens} tokens now)",
                8000,
            )
        self.chat_panel.handle_event(event)

    def _on_turn_started(self) -> None:
        self.input_box.set_busy(True)

    def _on_turn_finished(self) -> None:
        self.input_box.set_busy(False)

    def _unwire_bridge_signals(self, bridge: ChatBridge) -> None:
        """Undo ``_wire_bridge_signals`` for a bridge being retired, in both
        directions: ``bridge.disconnect(self)`` drops the bridge's own
        signals into this window — which now covers every one of them, see
        ``_wire_bridge_signals`` — and the two explicit calls drop the
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
            self._load_resumed_history(session.recorder.conversation_id)
        self._refresh_mcp_panel()
        self._refresh_folder_display()
        self._refresh_quick_tasks_panel()
        self._refresh_conversations_sidebar()
        # Bug report: "I restored prior session and have selected local AI
        # ... I suspect it must be using cloud (Argo)." Root cause:
        # _refresh_profile_selector() was previously only ever called once,
        # synchronously in __init__ *before* bridge.start()'s async session
        # construction had even resolved a profile — self.bridge.session
        # was still None at that point, so the dropdown fell back to
        # whichever profile sorts first alphabetically and was never
        # corrected once the real session became ready. The dropdown could
        # therefore show a profile that was never actually in use, for
        # every session (not just resumed ones).
        self._refresh_profile_selector()
        # Same bug, same fix, workspace side: __init__'s one-time
        # _refresh_workspace_selector() call (right after bridge.start())
        # also runs before start_session()'s async resolution of the actual
        # workspace has completed, so it always reads self.bridge.session
        # as None and the toolbar dropdown falls back to "(no workspace)" —
        # even though the session that finishes starting a moment later is
        # correctly using the real (e.g. last-used) workspace the whole
        # time. Unlike the profile selector, nothing ever refreshed this
        # dropdown again afterward for a normal startup/resume, so it was
        # stuck on "(no workspace)" for the rest of the session. Refreshing
        # here, once the session is actually ready, fixes it the same way.
        self._refresh_workspace_selector()
        self._save_last_session_selection()
        self._update_usage_label()

    def _update_usage_label(self) -> None:
        """Bug report: "Can we get cost estimate as I got to other tool?
        Or token use may be better... at this moment it is a black box."
        Reads ChatSession's own running totals (aida.cli.chat.ChatSession
        accumulates them from UsageInfo events in send()) rather than
        needing a dedicated ChatBridge signal — session is already reachable
        from the Qt thread. A fresh/history-only session with no usage yet
        just shows zeros rather than being left blank, so the label doesn't
        look broken."""
        session = self.bridge.session
        if session is None:
            self._usage_label.setText("")
            return
        # B2: priced at the active profile's own rate when it has one, same
        # fallback-to-default behavior as the CLI's session-total line.
        cost = estimate_cost_usd(
            session.total_input_tokens,
            session.total_output_tokens,
            input_usd_per_million=session.profile.usd_per_m_input,
            output_usd_per_million=session.profile.usd_per_m_output,
        )
        self._usage_label.setText(
            f"Tokens: {session.total_input_tokens:,} in / {session.total_output_tokens:,} out (~${cost:.3f} est.)"
        )

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

    def _load_resumed_history(self, conversation_id: str) -> None:
        """Acceptance criterion "resume yesterday's conversation... images
        still display": ``ChatPanel.load_history`` only knows about
        ``Message``s (the provider-facing wire type), not artifacts, so
        this queries the DB directly for both — the messages paired with
        their ``seq`` and the conversation's ``ArtifactRecord``s grouped by
        the ``seq`` they were recorded at (U6(b)) — and hands both to
        ``ChatPanel.load_history`` to interleave. Artifacts with no seq
        (recorded before U6(b) added the column, or any future caller that
        doesn't know it) are appended after the whole transcript instead,
        same as v1's behavior."""
        store = ConversationStore()
        try:
            rows = store.load_messages_with_seq(conversation_id)
            records: list[ArtifactRecord] = store.load_artifacts(conversation_id)
        finally:
            store.close()

        artifacts_by_seq: dict[int, list[ArtifactRecord]] = {}
        undated_records: list[ArtifactRecord] = []
        for record in records:
            if record.seq is None:
                undated_records.append(record)
            else:
                artifacts_by_seq.setdefault(record.seq, []).append(record)

        non_system = [(seq, message) for seq, message in rows if message.role != "system"]
        if non_system:
            seqs = [seq for seq, _ in non_system]
            messages = [message for _, message in non_system]
            self.chat_panel.load_history(messages, seqs=seqs, artifacts_by_seq=artifacts_by_seq)

        for record in undated_records:
            widget = self.chat_panel.artifact_widget_for(record)
            if widget is not None:
                self.chat_panel.add_artifact_widget(widget)

    def _on_startup_failed(self, message: str) -> None:
        self.statusBar().showMessage("Startup failed", 5000)
        # U4: the single most common startup failure — a genuine first run,
        # nothing configured yet — used to land on a bare "No profile
        # given" critical dialog with no path forward short of a text
        # editor. Any *other* startup failure (unknown workspace, a typo'd
        # --mcp name, a broken resume) still gets the plain critical dialog
        # — those aren't "you haven't set anything up yet", they're "you
        # configured something and it's wrong", which the onboarding panel
        # doesn't help with.
        if not self.settings.providers.profiles:
            self._show_onboarding()
            return
        QMessageBox.critical(self, "Could Not Start Session", message)

    def _show_onboarding(self) -> None:
        dialog = OnboardingDialog(self.settings, self.bridge, skills_dir(), self)
        dialog.exec()
        self._refresh_profile_selector()
        self._refresh_workspace_selector()
        if self.settings.providers.profiles:
            # At least one profile now exists (added during onboarding) —
            # retry starting a session instead of leaving the user staring
            # at a window with no active chat and no obvious next step.
            last_profile = self.settings.app.last_profile_name
            profile_name = (
                last_profile if last_profile in self.settings.providers.profiles else sorted(self.settings.providers.profiles)[0]
            )
            last_workspace = self.settings.app.last_workspace_name
            workspace_name = last_workspace if last_workspace in self.settings.workspaces.workspaces else None
            self._restart_session(workspace_name=workspace_name, profile_name=profile_name, resume_conversation_id=None)

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
            outgoing, failures, images = self._augment_with_attachments(text, attachments)
        except Exception as exc:  # noqa: BLE001 - belt-and-suspenders: see _read_attachment_for_model's
            # docstring for the real bug this whole two-layer defense is
            # guarding against — a send must never silently vanish.
            self._logger.error("unexpected error augmenting message with attachments %r: %s", attachments, exc)
            QMessageBox.warning(self, "Attachment Not Sent", f"Could not prepare the message to send:\n\n{exc}")
            return
        if failures:
            names = ", ".join(Path(p).name for p in failures)
            self.statusBar().showMessage(f"Could not read attachment(s): {names} — see chat for details", 8000)
        self.bridge.send(outgoing, images=images)

    def _augment_with_attachments(
        self, text: str, attachments: list[str]
    ) -> tuple[str, list[str], list[ImageRef]]:
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

        An attached image (B1) gets both: the same text placeholder every
        artifact type gets (so it's referenced in the message even when
        vision isn't active for the current profile), *and* an ``ImageRef``
        in the returned list so ``ChatSession.send`` can attach its actual
        pixels — whether that happens at all still depends on the active
        profile's ``supports_vision``, decided at translation time
        (``aida.providers.vision``), not here.

        Returns ``(message_text, failed_paths, images)`` — a failed read
        still gets an inline "could not read" note in the message (so both
        the human and the model see it plainly) rather than being silently
        dropped or aborting the whole send; ``failed_paths`` is just so the
        caller can also flag it in the status bar without re-parsing the
        text."""
        if not attachments:
            return text, [], []
        from aida.documents.readers import is_image_path

        sections = [text] if text else []
        failures: list[str] = []
        images: list[ImageRef] = []
        for path in attachments:
            rendered, ok = self._read_attachment_for_model(path)
            sections.append(rendered)
            if not ok:
                failures.append(path)
            elif is_image_path(path):
                images.append(ImageRef(path=path))
        return "\n\n".join(sections), failures, images

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
            # Windows CI regression: `{folder!r}` calls repr() on a path, which
            # *escapes* backslashes — a Windows path like "C:\Users\...\extra"
            # rendered as "'C:\\Users\\...\\extra'", doubled backslashes shown
            # right in the dialog text. A path never needs Python's repr()
            # quoting; a plain manual single-quote wrap shows the real path.
            f"Add '{folder}' as a source folder for workspace {self._current_workspace_config.name!r}?",
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

    def _on_new_chat_requested(self) -> None:
        """Bug report: "How do I create a new chat within same Workspace?
        Do not see 'New Chat' button, something which will not contain the
        history from prior chat." Reuses _restart_session exactly as
        workspace-switching already does — the only difference is pinning
        workspace/profile to whatever is already active, instead of
        switching to a different one."""
        session = self.bridge.session
        workspace_name = self._current_workspace_config.name if self._current_workspace_config else None
        profile_name = session.profile_name if session is not None else None
        answer = QMessageBox.question(
            self,
            "New Chat",
            "Start a new conversation? Current chat history stays saved and reachable from the sidebar.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._restart_session(workspace_name=workspace_name, profile_name=profile_name, resume_conversation_id=None)

    def _on_profile_changed(self, name: str) -> None:
        # aida.ui.qt.bridge.ChatBridge.switch_profile already does the
        # actual work (connected directly in _wire_bridge_signals); nothing
        # else to do here — kept as its own handler for symmetry/future use
        # (e.g. updating a "current profile" status label).
        pass

    def _on_profile_switched(self, _name: str) -> None:
        self._save_last_session_selection()

    def _on_profile_switch_failed(self, message: str) -> None:
        """Bug report class: "I selected local AI but it used Argo" — a
        mid-session ``/profile``-equivalent switch that fails left the
        toolbar dropdown showing a profile that was never actually put into
        use, with no indication anything went wrong
        (``ChatBridge.profile_switch_failed`` was emitted but nothing ever
        connected to it). Tell the user the switch didn't happen, and reset
        the dropdown back to the profile that's actually active — the
        session's provider/loop are untouched by a failed
        ``ChatSession.switch_profile`` (see its docstring), so
        ``_refresh_profile_selector`` reading ``bridge.session.profile_name``
        is exactly the still-active profile."""
        QMessageBox.warning(self, "Profile Switch Failed", message)
        self._refresh_profile_selector()

    def _restart_session(
        self, *, workspace_name: str | None, profile_name: str | None, resume_conversation_id: str | None
    ) -> None:
        old_bridge = self.bridge
        # Waits for an in-flight start *and* cancels/awaits an in-flight
        # turn before closing — see ChatBridge.shutdown. Without the latter,
        # hitting "New Chat" while a tool call was running left the old
        # turn streaming into the freshly-created panel.
        #
        # U7: this can block the Qt thread up to 5s, with nothing to show
        # for it — the window just froze. A busy cursor + an explicit
        # status message make that pause read as intentional instead of a
        # hang.
        self.statusBar().showMessage("Closing previous session…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            old_bridge.shutdown()
        finally:
            QApplication.restoreOverrideCursor()
        # Bug report: "Let's not add ... conversations which have no
        # messages in them ... or remove automatically when new
        # conversation is created." ChatSession's recorder creates its
        # conversation row up front, at session-start time, before any
        # message exists — every workspace switch / New Chat / Resume left
        # the *previous* one behind as a permanent "(untitled)" row if the
        # user never actually sent anything in it. Captured before
        # deleteLater() below, straight off the now-closed session — its
        # recorder/store aren't touched again, just read from.
        self._delete_conversation_if_empty(self._active_conversation_id(old_bridge))
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

    @staticmethod
    def _active_conversation_id(bridge: ChatBridge) -> str | None:
        """``bridge.session``/``session.recorder`` are both optional (no
        session yet, or a recorder-less session — see ``ChatSession.
        __init__``'s default) — this is the one guarded read shared by
        every ``_delete_conversation_if_empty`` call site."""
        session = bridge.session
        if session is None or session.recorder is None:
            return None
        return session.recorder.conversation_id

    def _delete_conversation_if_empty(self, conversation_id: str | None) -> None:
        """See the call site in ``_restart_session``/``closeEvent`` — a
        conversation row with zero messages was never actually used, so it
        is deleted outright rather than left behind. A fresh
        ``ConversationStore`` (the old session's own is already closed by
        this point) mirrors every other sidebar action's own open/use/close
        pattern (``_on_delete_requested`` etc.)."""
        if conversation_id is None:
            return
        store = ConversationStore()
        try:
            summary = store.get_conversation(conversation_id)
            if summary is not None and summary.message_count == 0:
                records_dir = ensure_records_dir(self.settings.app.records_dir)
                delete_conversation(store, conversation_id, records_dir=records_dir)
        finally:
            store.close()

    def _on_resume_requested(self, conversation_id: str) -> None:
        # Bug report: "I restored prior session and have selected local AI
        # ... I suspect it must be using cloud (Argo) because no local AI
        # server started." Resuming with profile_name=None used to fall
        # all the way through to start_session's own fallback (the
        # conversation's *originally recorded* profile,
        # chat.py's effective_profile_name) — silently overriding whatever
        # the user currently has picked in the toolbar dropdown, with
        # nothing but the dropdown's own (easy to miss) redraw to notice
        # by. Reading the dropdown here makes Resume respect it, the same
        # way workspace-switching/New Chat already treat their own
        # selectors as authoritative for what they do.
        current_profile = self.profile_selector.current_profile()
        self._restart_session(
            workspace_name=None, profile_name=current_profile or None, resume_conversation_id=conversation_id
        )

    def _on_delete_requested(self, conversation_id: str) -> None:
        store = ConversationStore()
        try:
            records_dir = ensure_records_dir(self.settings.app.records_dir)
            delete_conversation(store, conversation_id, records_dir=records_dir)
        finally:
            store.close()
        self._refresh_conversations_sidebar()

    def _on_delete_many_requested(self, conversation_ids: list[str]) -> None:
        """Bulk counterpart of ``_on_delete_requested`` — the sidebar's own
        multi-select Delete already confirmed once for the whole batch, so
        this just deletes each and refreshes once at the end, same
        "loop then refresh once" shape as ``_on_cleanup_requested`` below."""
        store = ConversationStore()
        try:
            records_dir = ensure_records_dir(self.settings.app.records_dir)
            for conversation_id in conversation_ids:
                delete_conversation(store, conversation_id, records_dir=records_dir)
        finally:
            store.close()
        self._refresh_conversations_sidebar()

    def _on_rename_requested(self, conversation_id: str, title: str) -> None:
        """Bug report: "Can we have the chat list in the history column
        have some kind of names? ... these date/times are not very
        convenient to use." set_title already exists (ConversationRecorder
        auto-titles from the first message via it) — this is just the
        missing "change it again later" entry point."""
        from datetime import UTC, datetime

        store = ConversationStore()
        try:
            store.set_title(conversation_id, title, timestamp=datetime.now(UTC).isoformat())
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
        # U7: capability_notes was stored but shown nowhere — surfaced here
        # as each combo entry's tooltip so the "small local model — prefer
        # lean MCP groups" hints the config format was designed for are
        # actually visible (also shown in the Settings dialog's read-only
        # profile list — see settings_dialog._profile_rows).
        capability_notes = {name: p.capability_notes for name, p in self.settings.providers.profiles.items()}
        self.profile_selector.set_profiles(
            sorted(self.settings.providers.profiles), current=current, capability_notes=capability_notes
        )

    def _refresh_mcp_panel(self) -> None:
        """``enabled`` (McpQuickPanel's checked-state input) is now which
        servers are *actually running* (``McpManager.running_server_names``)
        — the checkboxes became live start/stop controls, so their checked
        state has to mean "running", not "would this workspace's mcp_group
        start it" (the old, config-only meaning). ``group_name`` is still
        shown as a label purely for context: it's what the active
        workspace would auto-start on its own next session, independent of
        whatever the user has manually toggled on top of that this
        session."""
        session = self.bridge.session
        workspace_name = session.recorder.workspace_name if session and session.recorder else None
        group_name = None
        if workspace_name:
            workspace = get_workspace(self.settings, workspace_name)
            if workspace is not None:
                group_name = workspace.mcp_group
        all_server_names = sorted(self.settings.mcp.servers)
        manager = self.bridge.mcp_manager
        running = list(manager.running_server_names) if manager is not None else []
        self.mcp_panel.set_servers(all_server_names, enabled=running, group_name=group_name)

    def _on_mcp_server_start_requested(self, name: str) -> None:
        # The quick panel lists every server in settings.mcp.servers, but
        # bridge.mcp_manager only actually knows about the ones the
        # session's active workspace/mcp_group started at launch (or None
        # at all, if none did — see ChatBridge._ensure_mcp_manager). Ticking
        # a box for a server outside that set used to fail with "not
        # configured" even though it's a perfectly real, configured server —
        # register it with the manager first (a no-op if it's already
        # known) so "Start" from the quick panel works for any configured
        # server, not just ones the session happened to start with.
        config = self.settings.mcp.servers.get(name)
        if config is not None:
            self.bridge.register_mcp_server(config)
        self.bridge.start_mcp_server(name)

    def _on_mcp_server_stop_requested(self, name: str) -> None:
        self.bridge.stop_mcp_server(name)

    def _on_mcp_server_status_changed(self, _name: str) -> None:
        self._refresh_mcp_panel()

    def _on_mcp_server_action_failed(self, name: str, message: str) -> None:
        """A start/stop/restart from the quick panel (or the full
        McpManagementDialog, still running the same ChatBridge) failed —
        refreshing re-reads the real running state, so a checkbox the user
        just ticked snaps back to unchecked instead of lying about a
        server that never actually started."""
        QMessageBox.warning(self, "MCP Server", f"{name}: {message}")
        self._refresh_mcp_panel()

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
            self.folder_display.set_commands(
                patterns=self._current_workspace_config.command_allowlist,
                interpreter=self._current_workspace_config.python_interpreter,
            )
        else:
            self.folder_display.set_folders(source_folders=[], target_folder=None, sidecar_folder_name="figures")
            self.folder_display.set_commands(patterns=[], interpreter=None)

    def _on_source_folders_changed(self, folders: list[str]) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.source_folders = list(folders)

    def _on_target_folder_changed(self, folder: str) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.target_folder = folder

    def _on_sidecar_folder_name_changed(self, name: str) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.sidecar_folder_name = name

    def _on_command_allowlist_changed(self, patterns: list[str]) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.command_allowlist = list(patterns)

    def _on_python_interpreter_changed(self, interpreter: str) -> None:
        if self._current_workspace_config is not None:
            self._current_workspace_config.python_interpreter = interpreter or None

    def _on_save_folders_to_workspace(self) -> None:
        if self._current_workspace_config is None:
            self.statusBar().showMessage("No active workspace to save folders to", 5000)
            return
        save_workspace(self.settings, self._current_workspace_config)
        self.statusBar().showMessage(f"Saved folders to workspace {self._current_workspace_config.name}", 5000)

    # --- quick tasks (B14) ---------------------------------------------------

    def _refresh_quick_tasks_panel(self) -> None:
        """Loads the active workspace's saved quick tasks — same "populate
        from ``_current_workspace_config``, empty/disabled with no active
        workspace" shape as ``_refresh_folder_display``. Quick tasks are
        workspace-scoped data (a routine-task list only makes sense tied to
        the source/target folders and skills that go with it), so there's
        nowhere to save a new one without an active workspace."""
        if self._current_workspace_config is not None:
            self.quick_tasks_panel.set_tasks(
                [QuickTaskData(name=t.name, text=t.text) for t in self._current_workspace_config.quick_tasks]
            )
        else:
            self.quick_tasks_panel.set_tasks([])
        self.quick_tasks_panel.setEnabled(self._current_workspace_config is not None)

    def _on_quick_task_selected(self, text: str) -> None:
        """Double-click in the Quick Tasks panel — drops the template into
        the input box for the user to review/fill in details (a sample
        name, a scan number) before sending; never sent automatically. If
        the input box already has unsent text, confirm before discarding it
        rather than silently clobbering something the user was mid-typing."""
        if self.input_box.text().strip():
            answer = QMessageBox.question(
                self,
                "Replace Draft Message?",
                "The input box already has unsent text. Replace it with this quick task?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.input_box.set_text(text)

    def _on_quick_tasks_changed(self, tasks: list[QuickTaskData]) -> None:
        """Add/Edit/Delete in the panel already asked the user to confirm
        (or filled out the dialog) — this just persists the resulting full
        list straight back to the active workspace's own config, same
        "auto-save on every structured-record edit" shape as
        ``ConversationsSidebar``'s rename/delete (unlike the folder fields,
        which require an explicit "Save to Workspace" click since those are
        free-typed, easy to fat-finger)."""
        if self._current_workspace_config is None:
            return
        self._current_workspace_config.quick_tasks = [QuickTask(name=t.name, text=t.text) for t in tasks]
        save_workspace(self.settings, self._current_workspace_config)

    # --- MCP management (Phase 7) -------------------------------------------

    def open_mcp_management_dialog(self) -> None:
        """Opens with ``self.bridge`` even if it's still mid-startup or has
        zero MCP servers configured yet — the dialog handles a ``None``
        ``bridge.mcp_manager`` gracefully (every server shows "stopped",
        every live action is a no-op until a manager exists), and
        ``ChatBridge._ensure_mcp_manager`` creates one lazily the moment
        "Add Server" + "Start" is actually used."""
        dialog = McpManagementDialog(self.settings, self.bridge, skills_dir(), self)
        dialog.exec()
        self._refresh_mcp_panel()

    # --- Knowledge base management (Phase 8) --------------------------------

    def open_knowledge_management_dialog(self) -> None:
        dialog = KnowledgeManagementDialog(self.settings, self.bridge, self)
        dialog.exec()

    # --- provider/embedding profiles (U2) -------------------------------------

    def open_profiles_dialog(self) -> None:
        """Opens with ``self.bridge`` even mid-startup, same reasoning as
        ``open_mcp_management_dialog`` — "Test" is a no-op until a bridge
        exists, but Add/Edit/Remove never depended on one."""
        dialog = ProfilesDialog(self.settings, self.bridge, self)
        dialog.exec()
        # A profile may have been added/removed/renamed — every selector
        # and dependent dialog that lists profile names needs to see it.
        self._refresh_profile_selector()

    # --- workspaces (U1) ------------------------------------------------------

    def open_workspace_management_dialog(self) -> None:
        dialog = WorkspaceManagementDialog(self.settings, skills_dir(), self)
        dialog.exec()
        self._refresh_workspace_selector()

    # --- code editor (Phase 9) -------------------------------------------------

    def open_code_editor_dialog(self, *, initial_text: str = "", initial_path: str | None = None) -> None:
        """Opens blank from the toolbar action, pre-filled with a message's
        first code block via ``_on_code_editor_requested`` (a copy of its
        text, no file behind it), or — bug report: "code editor has no way
        in" — opened *at* a real file via ``initial_path`` (a chat file
        artifact's own "Open in Code Editor" button, see
        ``_on_open_in_code_editor_requested``), so Save/Run act on that
        file directly rather than a disconnected copy. Saved-scripts
        location and interpreter come from the active workspace, if any —
        both are optional (``CodeEditorDialog`` falls back to the user's
        home folder / ``sys.executable``)."""
        workspace = self._current_workspace_config
        dialog = CodeEditorDialog(
            initial_text=initial_text,
            initial_path=initial_path,
            saved_scripts_dir=workspace.resolved_saved_scripts_dir() if workspace else None,
            python_interpreter=workspace.python_interpreter if workspace else None,
            script_timeout_seconds=workspace.script_timeout_seconds if workspace else DEFAULT_RUN_TIMEOUT_SECONDS,
            bridge=self.bridge,
            parent=self,
        )
        dialog.exec()

    def _on_code_editor_requested(self, code: str) -> None:
        self.open_code_editor_dialog(initial_text=code)

    def _on_open_in_code_editor_requested(self, path: str) -> None:
        self.open_code_editor_dialog(initial_path=path)

    # --- settings ------------------------------------------------------------

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.settings.app, self.settings.providers.profiles, self)
        if not dialog.exec():
            return
        previous_safety_mode = self.settings.app.default_safety_mode
        self.settings.app = dialog.updated_app_config()
        # U3: the global default gets the same one-time relaxed-mode
        # warning a workspace's own safety field already shows on the
        # CLI/GUI workspace editor (relaxed_mode_warning_if_newly_enabled)
        # — flipping the *default* every new workspace inherits deserves
        # the same heads-up as flipping one workspace's own setting.
        warning = relaxed_mode_warning_if_newly_enabled(previous_safety_mode, self.settings.app.default_safety_mode)
        if warning:
            QMessageBox.warning(self, "Relaxed Mode", warning)
        apply_font_size(QApplication.instance(), self.settings.app)  # takes effect immediately, no restart
        # "Change the debug level so I can help with console report" (bug
        # report): configure_logging is safe to call again — it only
        # adjusts the "aida" logger tree's level, doesn't duplicate
        # handlers (see its docstring) — so a log-level change here takes
        # effect immediately, same as the font size above, instead of only
        # applying on the next launch.
        configure_logging(self.settings.app.log_level)
        # Bug report: "Give user control on number of iterations." Patched
        # directly onto the *running* AgentLoop too — not just saved for the
        # next session start — same "takes effect immediately" treatment as
        # font size/log level above.
        if self.bridge.session is not None:
            self.bridge.session.loop.max_iterations = self.settings.app.max_agent_iterations
        save_app_config(self.settings.app)

    # --- shutdown ----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        capture_window_state(self, self.settings.app)
        save_app_config(self.settings.app)
        conversation_id = self._active_conversation_id(self.bridge)
        self.bridge.shutdown()
        # Same cleanup as _restart_session — quitting on a conversation
        # nothing was ever typed into shouldn't leave it behind either.
        self._delete_conversation_if_empty(conversation_id)
        super().closeEvent(event)


__all__ = ["MainWindow"]
