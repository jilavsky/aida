# Phase 5 — PySide6 desktop GUI v1

**Goal:** The first real GUI: chat with streaming text, **inline PNG display**,
tool-call indicators, profile/workspace switching, conversation browser. This is the
milestone where AIDA replaces Claude Desktop / Witsy for the pyIrena use case.

**Prerequisites:** Phase 4.
**Unblocks:** Phases 6, 7 (parallel-capable), 8.
**Use cases advanced:** UC1, UC3 (interactive form).

---

## Tasks

### Qt infrastructure (`aida.ui.qt`)

- [x] `_qt.py` shim (pyIrena pattern): all Qt imports go through it; contract test
      fails the build on any direct `PySide6`/`PyQt` import elsewhere
      — `aida/ui/qt/_qt.py` is the sole importer; `tests/ui/test_qt_contract.py`
      (4 tests, AST-based so it runs even without PySide6 installed) enforces it,
      plus a second test that pins the one sanctioned lazy-import exception
      (`aida.cli.__main__.main_gui`).
- [x] Event-stream → Qt bridge: core runs async in a worker thread; events marshaled
      to the GUI thread; **core remains importable and testable without Qt**
      — `aida/ui/qt/bridge.py` (`AsyncLoopThread` + `ChatBridge`),
      `tests/ui/test_bridge.py` (11 tests: startup success/failure, event
      ordering, profile switch success/failure, shutdown, and cancel
      wiring, all against a real background asyncio loop thread).
- [x] `aida-gui` entry point; app icon; window state (size/position/font size)
      persisted in config
      — entry point: `aida/ui/qt/app.py` + `aida/cli/__main__.py:main_gui`
      (lazy-imports PySide6, prints an install hint and exits 1 if it's
      missing). App icon: a generated placeholder PNG at
      `aida/ui/qt/resources/app_icon.png` (bundled automatically in the
      wheel — verified by inspecting a real `python -m build --wheel`
      output), set via `aida.ui.qt.icon.app_icon()` on both the
      `QApplication` and `MainWindow`; **a real designed icon (vs. this
      placeholder) is still wanted before a public release.** Window state:
      `aida/ui/qt/window_state.py`, `tests/ui/test_window_state.py` (4
      tests) plus an end-to-end resize→close→reload test in
      `test_main_window.py`.

### Main window & chat panel

- [x] Conversation view: user/assistant turns, streamed text appended live,
      Markdown rendering (Qt rich text), code blocks monospaced with copy button
      — `MessageBubble` (`aida/ui/qt/chat_panel.py`) uses `QTextBrowser.setMarkdown`
      (code fences render monospaced via Qt's own Markdown engine) plus a
      whole-message **Copy** button. Note: this is whole-message copy, not a
      separate button embedded per individual code block — Qt's rich-text
      engine doesn't expose per-block child widgets without hand-rolled
      text-object embedding, judged out of scope for v1. Tested in
      `tests/ui/test_chat_panel.py` (11 tests) including streaming order and
      the copy button's clipboard content.
- [x] **Inline images**: `ImageArtifactCreated` → scaled inline pixmap; click →
      full-size viewer; context menu: Save As / copy / Reveal in file manager
      — `InlineImageWidget` (`aida/ui/qt/artifact_widgets.py`), every action
      also a plain callable method (not just a simulated click), 10 tests in
      `tests/ui/test_artifact_widgets.py`; end-to-end with a real mock-mcp
      subprocess PNG in `test_main_window.py`.
- [x] File artifacts shown as cards with Open / Reveal actions
      — `FileArtifactCard`, same file/tests as above.
- [x] Tool-call indicators: collapsed "server.tool(args…) ✓/✗ (1.2 s)" rows,
      expandable to arguments/results (text form)
      — `ToolCallRow` (`aida/ui/qt/tool_call_widget.py`), 5 tests.
- [x] Input box: multiline, Enter/Shift-Enter, Send + **Stop** button (cancel works
      mid-stream), busy indicator
      — `aida/ui/qt/input_box.py`, 8 tests including Shift+Enter vs Enter and
      the Send/Stop relabel while busy. What's verified: the button relabels
      and emits `cancel_requested`, which `MainWindow` wires to
      `ChatBridge.cancel`, which forwards to `ChatSession.cancel`
      (`test_cancel_forwards_to_the_session`, new). What's **not**
      independently re-verified here: the actual mid-stream interruption
      behavior inside `aida.core.agent.AgentLoop` — that's core logic from
      an earlier phase, out of Phase 5's scope to retest, and MockProvider
      has no artificial delay to reliably race a real cancel against in a
      GUI integration test.
- [x] Error display distinguishes layer (provider / MCP / tool / core) per the
      diagnostics rule
      — `ErrorBanner`, tested in `test_chat_panel.py` and exercised via
      `MainWindow._on_turn_failed`/`_on_startup_failed`.

### Switchers & panels

- [x] Workspace selector (toolbar dropdown) — switching mid-session starts a new
      conversation in that workspace after confirmation
      — confirmed and declined paths both covered end-to-end in
      `test_main_window.py` (real new conversation id, cleared chat panel).
- [x] Profile selector — switching mid-conversation allowed (Phase 2 semantics)
      — wired to `ChatBridge.switch_profile`; success/failure covered in
      `test_bridge.py`.
- [x] Source/target folder display with change buttons (writes back to workspace
      for this session; "save to workspace" option)
      — **found and fixed a real gap while verifying this**: `FolderDisplay`
      was built and placed in the layout but `MainWindow` never called
      `set_folders()` or connected any of its three signals, so it always
      showed "(none)"/"(none)" and "Save to Workspace" silently did
      nothing regardless of the active workspace. Fixed:
      `MainWindow._refresh_folder_display()` now populates it from the real
      `WorkspaceConfig` on every session start/switch, and its signals now
      edit an in-memory copy that `save_workspace()` persists to
      `workspaces.yaml` on "Save to Workspace". Covered by a new end-to-end
      test in `test_main_window.py` that changes the target folder, saves,
      and reloads `workspaces.yaml` from disk to confirm persistence.
- [x] MCP quick panel: enabled group + per-server on/off checkboxes (management UI
      proper is Phase 7)
      — `McpQuickPanel` correctly displays the active workspace's group and
      enabled servers (`MainWindow._refresh_mcp_panel`, 13 selector tests).
      Per its own docstring this is read-mostly v1: toggling a checkbox
      emits `enabled_servers_changed`, but `MainWindow` doesn't yet act on
      it (no live server enable/disable, no "applies next session start")
      — full management is explicitly deferred to Phase 7 per the task
      description, so this is scoped as intended rather than a gap.
- [x] Conversations sidebar: list (title, date, workspace), open/resume, delete
      with confirmation, cleanup dialog (older-than picker)
      — `aida/ui/qt/conversations_sidebar.py`, 12 tests; resume and delete
      also covered end-to-end in `test_main_window.py` against a real
      SQLite-backed `ConversationStore`.
- [x] Settings dialog v1: font size, records folder, log level, provider profiles
      *view* (editing via config file is acceptable this phase)
      — `aida/ui/qt/settings_dialog.py`, 8 tests; font-size-takes-effect-
      without-restart also covered end-to-end in `test_main_window.py`.

### Tests & platforms

- [x] Headless unit tests for the event→widget bridge (Qt offscreen platform)
      — `tests/ui/` (95 tests total), `QT_QPA_PLATFORM=offscreen` set at
      `conftest.py` import time; runs fully headless, confirmed in this
      Linux sandbox (no real display).
- [x] `_qt` contract test + no-Qt-outside-ui contract test extended to new modules
      — `tests/ui/test_qt_contract.py`, 4 tests (see above).
- [ ] Manual smoke checklist run on macOS **and** Windows (Linux best-effort)
      — **not done**. What WAS done instead: (1) this Linux sandbox's full
      offscreen automated suite (368 tests, `pytest -q` + `ruff check .`,
      both clean, re-run 3x to rule out flakiness); (2) one ad-hoc manual
      smoke run of the real `aida-gui` process against a live SQLite DB
      earlier in this phase's development, which is what caught the
      concurrent-first-open SQLite race documented in `aida/persistence/db.py`.
      No macOS or Windows machine is available in this sandbox — a real
      visual smoke pass on both is still needed before calling this done.

---

## Acceptance — phase is done when all are checked

- [ ] **Flagship demo:** select workspace "use-pyirena" (Argo Claude + pyirena-mcp
      group + skills), ask for a plot of a known dataset → streamed reasoning, tool
      indicator, **PNG inline**, saved artifact revealable in Finder/Explorer —
      matching or beating the Claude Desktop experience
      — the *mechanics* are automated and verified end-to-end
      (`test_flagship_demo_tool_call_produces_inline_image`: a real
      mock-mcp subprocess, a real on-disk PNG, a real `InlineImageWidget`
      with valid pixel data) with only the LLM itself scripted
      (`MockProvider`). Not verified: a real `pyirena-mcp` server, a real
      Argo/Claude profile, or the subjective "matching or beating Claude
      Desktop" judgment — all require a human running the real app.
- [ ] Same demo with a local Ollama model completes (slower is fine)
      — not verified; no Ollama instance in this sandbox.
- [ ] Stop button interrupts a long generation cleanly; app remains usable
      — GUI-side wiring verified (see Input box note above); real
      mid-stream interruption is core `AgentLoop` behavior from an earlier
      phase, not independently re-verified here.
- [x] Resume yesterday's conversation from the sidebar; images still display
      — `test_resume_conversation_loads_prior_history` and
      `test_resume_conversation_redisplays_prior_image_artifact` (new): a
      real SQLite-backed resume, both text history and a real persisted
      image artifact re-displayed. (Text-only resume was already working;
      the image-redisplay half was a real gap — `ChatPanel.load_history`
      only replays text — found and fixed via
      `MainWindow._load_resumed_artifacts`, which re-derives widgets from
      the `artifacts` table.)
- [x] Font size change takes effect without restart
      — `test_settings_dialog_font_size_applies_without_restart`: opens the
      real settings dialog flow, changes font size, asserts
      `QApplication.font()` changed immediately with no restart.
- [ ] Works on macOS and Windows from a clean `pip install -e ".[gui]"`
      — not verified; no macOS/Windows machine available in this sandbox.
      `pip install -e ".[gui]"` (PySide6>=6.6) does install cleanly on
      Linux/Python 3.11 here, which is a necessary but not sufficient check.
- [ ] CI green (offscreen Qt tests included)
      — `.github/workflows/ci.yml` updated to `pip install -e ".[dev,gui]"`
      so `tests/ui/*` actually run in CI instead of silently skipping via
      `pytest.importorskip("PySide6")`; not yet confirmed against a real
      GitHub Actions run — needs the user's own CI to go green on this
      branch before checking this off.

## Out of scope for this phase

Drag & drop (Phase 6); document reading/writing beyond transcripts (Phase 6); MCP
management/permissions UI (Phase 7); RAG panel (Phase 8); code editor (Phase 9).
