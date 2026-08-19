# Phase 6 — Files, documents, outputs & the safety model

**Goal:** The agent can work with the user's folders and documents: native file
tools under the **allowed-folders safety model** (relaxed mode), reading common
document formats, and writing the default output — **Markdown in Obsidian structure**
with a figures sidecar folder. Drag & drop onto the GUI.

**Prerequisites:** Phase 5 (can start core parts after Phase 4).
**Unblocks:** Phase 9 (needs safety model).
**Use cases advanced:** UC2 end-to-end; UC3/UC4 gain document outputs.

---

## Tasks

### Safety model (`aida.workspace.safety`)

- [x] Allowed-folders registry (global + per-workspace source/target folders are
      implicitly allowed); path normalization incl. network mounts & symlinks —
      every file operation checks containment. `AppConfig.allowed_folders` is the
      global layer (editable via `config.yaml` for v1, no GUI editor yet — same
      pattern as other v1-only-via-config settings). `normalize_path()` uses
      `Path.expanduser().resolve(strict=False)`; verified against a real
      `Path.symlink_to()` escape attempt, not just `..`. 28 unit tests in
      `test_workspace_safety.py`, plus `start_session` wiring tests
      (`test_start_session.py`) covering both the per-workspace and global layers.
- [x] Modes per workspace: `relaxed` (no per-action confirmation inside allowed
      folders) and `confirm` (per write/delete confirmation) — unit-tested, and
      exercised end-to-end via real tool calls in `test_start_session.py` /
      `test_phase6_acceptance.py`.
- [x] One-time clear warning when enabling relaxed mode — `relaxed_mode_warning_if_
      newly_enabled()`, wired into `aida workspace new`/`edit` (only CLI path that
      creates/edits workspaces right now — no GUI "new workspace" form exists yet,
      that's still a Phase 5 gap, not new here). 5 tests in `test_workspace_cmds.py`
      cover new/edit, enabling/already-relaxed/disabling.
- [x] Always-confirm regardless of mode: paths outside allowed folders — tested
      thoroughly (both modes, both under `authorize_read`/`write`/`delete`).
      "Sending local content anywhere except the configured provider" has no
      separate mechanism to check: no tool in this codebase sends content
      anywhere except back through the agent loop to the configured provider, so
      this is vacuously satisfied rather than actively enforced by new code —
      noted here rather than silently checked.
- [x] Delete = move to `_trash/` inside the allowed folder (configurable off) —
      tested, including the hard-delete (`trash_enabled=False`) path.
- [x] Confirmation requests reach the user as a GUI dialog / CLI prompt — **not**
      implemented as a literal new `AgentEvent` variant through the event stream
      (the existing stream is a one-directional async-generator with no
      request/reply protocol; see `aida/workspace/safety.py`'s module docstring
      for the full reasoning). Instead `SafetyGuard` takes a plain `async def
      confirm(ConfirmationRequest) -> bool` callback supplied at session-build
      time — the CLI's default blocks on a real terminal `input()`
      (`aida.cli.chat.cli_confirm`), the GUI's bridges to a real `QMessageBox`
      via `ChatBridge._confirm` + `concurrent.futures.Future` +
      `MainWindow._on_confirmation_requested`. Both are automated end-to-end
      (`test_chat_cli.py`'s `cli_confirm` tests with a mocked `input`;
      `tests/ui/test_bridge.py` and `tests/ui/test_main_window.py`'s
      `test_safety_confirmation_*` tests with a mocked `QMessageBox.question`) —
      what's **not** verified is an actual human typing at a real terminal or
      clicking a real mouse on a real dialog.

### Native workspace tools (`aida.workspace.files`)

- [x] Tools exposed to the LLM: `list_directory`, `find_files`, `search_text`,
      `read_file`, `write_file`, `create_directory`, `copy_file`, `move_file`,
      `delete_file`, `get_file_metadata` — all safety-checked, all with size caps.
      28 tests in `test_workspace_files.py`; wired into `aida.cli.chat.
      start_session` for both `aida chat` and the GUI (shared code path).
- [x] Graceful handling of slow/missing network mounts (timeout + clear error) —
      `_run_blocking()` wraps every bulk scan and file I/O call in
      `asyncio.wait_for`, raising a clear, actionable `TimeoutError` message.
      The timeout mechanism itself is directly unit-tested (a real slow blocking
      call against a short timeout); an actual hung/slow network mount is **not**
      simulated — not practical to create one in this sandbox.
- [x] Tool results are typed (`TableArtifact` for listings/search results,
      `JsonArtifact` for metadata — not prose) — tested.

### Document readers (`aida.documents.readers`, extra `docs`)

- [x] Dispatcher by extension/MIME; structured extraction, not blind flattening —
      tested (18 tests in `test_document_readers.py`).
- [x] Text/code/MD/CSV/JSON (stdlib); PDF (`pymupdf`); DOCX (`python-docx`); XLSX
      (`openpyxl`, sheet→table); PPTX (`python-pptx`, slide text) — all tested
      against real fixture files built with each real library (not checked-in
      binaries). Images → `ImageArtifact` referencing the file's own path (**known
      v1 limitation, documented in the module docstring**: this codebase's
      `Message.content` is plain `str` throughout the provider layer — there is no
      multipart/vision message shape yet, so an attached/read image displays in
      the GUI and the model is *told* it exists, but its pixels are not actually
      sent to a vision-capable model; that needs a provider-layer change out of
      scope for this phase). The `docs` extra is now installed in CI
      (`.github/workflows/ci.yml`) so these run for real there instead of
      silently skipping via `pytest.importorskip`.
- [x] Size/token guards: `max_chars`/`max_pdf_pages`/`max_sheets`/
      `max_rows_per_sheet` caps, each with a dedicated truncation test.
- [x] HDF5 deliberately **not** implemented (pyIrena MCP's job) — documented in
      the module docstring.

### Writers (`aida.documents.writers`)

- [x] **`md_obsidian.py` (default):** MD file in target folder; images written to
      user-nameable sidecar folder (workspace `sidecar_folder_name`, default
      `figures`); links relative; safe filename collision handling
      (`unique_destination`) — tested, including the end-to-end acceptance test.
- [x] Agent-facing tools: `write_markdown_report(title, body,
      image_artifact_ids=[...])` and plain `write_file` (the latter already lived
      in `aida.workspace.files` — `aida.documents.tools` is specifically the
      formatted-document writers) — tested.
- [x] DOCX writer (headings, paragraphs, images, tables) for Office needs —
      `write_docx_document()`'s `DocxSection` supports all four kinds, each
      tested via real `python-docx` round-trips (including a table). One
      narrower gap, worth noting: the agent-facing `write_docx_report` tool
      only exposes `title`/`body`/`image_artifact_ids` to the model — it never
      builds a `table` section, so an agent can't currently ask for a DOCX
      table through that tool even though the writer underneath supports one.
      Extending the tool's parameters to accept tabular data is a small,
      isolated follow-up if it turns out to matter in practice.
- [x] Phase 4's transcript exporter refactored onto `md_obsidian.py` — the
      low-level image-copy mechanic (`copy_images_to_sidecar`) is now shared;
      each writer keeps its own distinct text-rendering logic (transcript =
      role-structured dialogue, report = freeform prose). Verified: Phase 4's
      full existing test suite (26 tests) still passes unchanged.

### GUI integration

- [x] Drag & drop files onto the chat → attached to the next message (content
      read via `aida.documents.readers` and appended to the outgoing message,
      shown as removable attachment chips); drag & drop a folder → confirmation
      dialog offering to add it as a source folder. An "Attach…" button is the
      non-drag alternative. Tested at the widget level (`tests/ui/
      test_input_box.py`, using a duck-typed fake `QDropEvent` — `InputBox.
      dropEvent` only calls `.mimeData()`/`.acceptProposedAction()`, so a real Qt
      drag sequence isn't needed to exercise the handling logic) and end-to-end
      through `MainWindow` (`test_send_with_attachment_includes_file_content_in_
      the_message`, `test_folder_drop_with_active_workspace_offers_to_add_source_
      folder`, and related tests). **Not verified:** an actual mouse-driven OS-level
      drag-and-drop gesture — that needs a real display and a human (or a much
      heavier browser/OS automation harness than this project uses).
- [x] Attach button as the non-drag alternative — tested (native
      `QFileDialog.getOpenFileNames`, mocked in tests).
- [x] Target folder + sidecar name visible/editable in workspace bar —
      `FolderDisplay` gained an editable sidecar-name field alongside the
      existing source/target folder pickers; "Save to Workspace" persists all
      three together. Tested at both the widget and `MainWindow` levels.
- [x] Generated documents appear as file cards — this was already satisfied for
      free by Phase 5's existing `FileArtifactCreated` → `FileArtifactCard`
      plumbing (every mutating file/document tool already returns a
      `FileArtifact`), since `ChatPanel.handle_event` doesn't care which tool
      produced the event. Added `test_write_markdown_report_shows_as_file_
      artifact_card` to `test_main_window.py` to pin this down for the
      document-writing tools specifically, rather than just asserting it by
      inference.

### Tests

- [x] Safety: containment (incl. `..`, symlink escape), mode behavior, trash-move,
      always-confirm cases — 28 thorough unit tests in `test_workspace_safety.py`.
- [x] Reader tests with small fixture files per format — 18 tests, real fixture
      files built with each real library.
- [x] Obsidian writer test: MD + sidecar images + relative links roundtrip —
      9 tests in `test_document_writers.py`.
- [x] End-to-end with MockProvider: "read these two files, write a summary MD with
      one image" produces correct on-disk structure —
      `tests/test_phase6_acceptance.py::test_read_two_files_and_write_markdown_
      report_with_image`, driven through the real `start_session`/`ChatSession.
      send()` path (not each module in isolation). A second acceptance test in
      the same file covers the "write outside allowed folders is denied" half.
      One deliberate simplification, documented in that file's module docstring:
      the embedded image is seeded directly into the shared `ArtifactStore`
      rather than produced by a live MCP tool call in *this* test — that MCP ->
      ArtifactStore wiring is already proven end-to-end by
      `test_phase4_acceptance.py`'s own image round-trip.

Total: 501 tests passing (`pytest -q`), `ruff check .` clean, as of this phase's
completion in the sandbox.

---

## Acceptance — phase is done when all are checked

- [ ] **UC2 demo:** drop a PDF + an MD file onto AIDA, ask questions (correct
      answers), then "write a summary document" → new MD in target folder with
      figures in the named sidecar folder, links working when opened in Obsidian.
      **Not run** — this needs a real display, real mouse-driven drag-and-drop,
      a real LLM, and a real install of Obsidian to open the result in. The
      underlying mechanics (PDF reading, attachment-to-message, summary writing,
      sidecar images, relative links) are each covered by automated tests above;
      what's missing is the literal end-to-end human demo.
- [ ] **UC3 full demo:** "find data in <source folder> with Rg 20–50 Å, plot them,
      and write a report listing files + Rg values with the graph" → inline plot AND
      report MD with embedded figure link. **Not run** — needs a real pyIrena MCP
      server and real SAXS data, both outside this phase's/this sandbox's scope.
      The report-writing half (plot + text + figure link) is exactly what
      `test_phase6_acceptance.py` proves; the analysis half (finding Rg 20–50 Å
      data via pyIrena) is Phase 3/pyIrena MCP's territory, not new here.
- [x] Relaxed mode: no confirmation dialogs appeared inside allowed folders during
      the above; attempt to write outside allowed folders was blocked with a clear
      confirmation request — verified by automated equivalents: `test_start_
      session_relaxed_workspace_allows_writes_without_confirmation` (relaxed mode,
      in-bounds write, confirm callback never called) and `test_phase6_
      acceptance.py::test_write_outside_target_folder_is_denied_without_
      confirmation` (outside allowed folders, denied, file never created).
- [x] `_trash/` receives a deleted file instead of hard deletion — tested in
      `test_workspace_safety.py` and `test_workspace_files.py`.
- [ ] CI green — cannot self-verify from the sandbox; this phase's changes add
      the `docs` extra to `.github/workflows/ci.yml` so the PDF/DOCX/XLSX/PPTX
      reader/writer tests actually run in CI instead of skipping. `pytest -q`
      (501 passed) and `ruff check .` are both clean in the sandbox as of this
      commit — pending your real GitHub Actions run to confirm the same across
      macOS/Windows/Linux × Python 3.11/3.13.

## Out of scope for this phase

Shell/python command execution (Phase 9); RAG ingestion of documents (Phase 8 —
this phase reads files into context directly); web fetch/search (Phase 9).
