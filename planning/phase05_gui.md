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

- [ ] `_qt.py` shim (pyIrena pattern): all Qt imports go through it; contract test
      fails the build on any direct `PySide6`/`PyQt` import elsewhere
- [ ] Event-stream → Qt bridge: core runs async in a worker thread; events marshaled
      to the GUI thread; **core remains importable and testable without Qt**
- [ ] `aida-gui` entry point; app icon; window state (size/position/font size)
      persisted in config

### Main window & chat panel

- [ ] Conversation view: user/assistant turns, streamed text appended live,
      Markdown rendering (Qt rich text), code blocks monospaced with copy button
- [ ] **Inline images**: `ImageArtifactCreated` → scaled inline pixmap; click →
      full-size viewer; context menu: Save As / copy / Reveal in file manager
- [ ] File artifacts shown as cards with Open / Reveal actions
- [ ] Tool-call indicators: collapsed "server.tool(args…) ✓/✗ (1.2 s)" rows,
      expandable to arguments/results (text form)
- [ ] Input box: multiline, Enter/Shift-Enter, Send + **Stop** button (cancel works
      mid-stream), busy indicator
- [ ] Error display distinguishes layer (provider / MCP / tool / core) per the
      diagnostics rule

### Switchers & panels

- [ ] Workspace selector (toolbar dropdown) — switching mid-session starts a new
      conversation in that workspace after confirmation
- [ ] Profile selector — switching mid-conversation allowed (Phase 2 semantics)
- [ ] Source/target folder display with change buttons (writes back to workspace
      for this session; "save to workspace" option)
- [ ] MCP quick panel: enabled group + per-server on/off checkboxes (management UI
      proper is Phase 7)
- [ ] Conversations sidebar: list (title, date, workspace), open/resume, delete
      with confirmation, cleanup dialog (older-than picker)
- [ ] Settings dialog v1: font size, records folder, log level, provider profiles
      *view* (editing via config file is acceptable this phase)

### Tests & platforms

- [ ] Headless unit tests for the event→widget bridge (Qt offscreen platform)
- [ ] `_qt` contract test + no-Qt-outside-ui contract test extended to new modules
- [ ] Manual smoke checklist run on macOS **and** Windows (Linux best-effort)

---

## Acceptance — phase is done when all are checked

- [ ] **Flagship demo:** select workspace "use-pyirena" (Argo Claude + pyirena-mcp
      group + skills), ask for a plot of a known dataset → streamed reasoning, tool
      indicator, **PNG inline**, saved artifact revealable in Finder/Explorer —
      matching or beating the Claude Desktop experience
- [ ] Same demo with a local Ollama model completes (slower is fine)
- [ ] Stop button interrupts a long generation cleanly; app remains usable
- [ ] Resume yesterday's conversation from the sidebar; images still display
- [ ] Font size change takes effect without restart
- [ ] Works on macOS and Windows from a clean `pip install -e ".[gui]"`
- [ ] CI green (offscreen Qt tests included)

## Out of scope for this phase

Drag & drop (Phase 6); document reading/writing beyond transcripts (Phase 6); MCP
management/permissions UI (Phase 7); RAG panel (Phase 8); code editor (Phase 9).
