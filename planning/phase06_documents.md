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

---

## Post-delivery bug fixes (user report, after installing this phase)

The user installed Phase 6, ran it against real usage (real GUI, real attached
PDF, real workspace), and reported 7 issues. All 7 are addressed below, each
with new/updated automated tests; the root-cause diagnosis for the most
confusing one (#3) is worth reading in full since it explains a fairly subtle
failure chain.

1. **"Can't remove a source_folder except by hand-editing the YAML."** —
   `FolderDisplay` (`aida/ui/qt/selectors.py`) only ever showed a single
   read-only concatenated label for source folders; there was no removal UI
   at all. Redesigned into a per-folder row (`_RemovableFolderRow`) with its
   own "Remove" button, same visual pattern as the attachment chips already
   used in `InputBox`. Tests: `tests/ui/test_selectors.py` (4 new tests:
   remove button removes the right folder, removing the last one restores
   the "(none)" placeholder, removing an unknown path is a no-op, adding the
   same folder twice is idempotent).

2. **"No state saving — last workspace isn't reopened, none is selected on
   start."** — `aida-gui` had no persisted "what was I last using" state at
   all; every launch with no `--workspace`/`--profile` flag hit "No profile
   given". Added `last_workspace_name`/`last_profile_name` to `AppConfig`
   (`aida/config/settings.py`), saved by `MainWindow._save_last_session_
   selection()` every time a session actually starts or a profile is
   switched (immediately, not just on window close, so it survives a
   crash), and read back as the fallback in `aida.ui.qt.app._resolve_start_
   kwargs()` when no CLI flag overrides it. Tests: `tests/test_settings.py`
   (roundtrip + defaults-to-None) and `tests/ui/test_app.py` (5 tests
   covering the fallback-vs-explicit-flag precedence).

3. **"Attached a PDF, asked to review it, got repeated confusing 'allow read
   outside allowed folders' prompts — looks like the agent searched the
   whole filesystem."** This was the most concerning report and the root
   cause was a silent crash, not a safety-model bug. `aida.documents.readers`
   imports each optional format library (`pymupdf`, `python-docx`,
   `openpyxl`, `python-pptx`) *lazily*, inside the function that reads that
   format — so on a machine without the `docs` extra installed, reading an
   attached PDF raises a bare `ModuleNotFoundError` at read time. The old
   `MainWindow._read_attachment_for_model` only caught
   `(UnsupportedDocumentFormatError, OSError)`, so that `ModuleNotFoundError`
   propagated straight up through the Qt "Send" handler, uncaught. The
   augmented message (user text + PDF content) never reached
   `ChatBridge.send` — nothing was sent to the model — but the user's own
   typed text had already been added to the chat panel *before* that crash,
   so it looked exactly like a normal successful send. The model then
   received a plain-text "review this paper" with zero file context, and
   tried to *find* "the paper" itself via its own `read_file`/`find_files`
   tool calls against guessed paths (home directory, `/`, ...) — each one
   gated by `SafetyGuard`'s outside-allowed-folders confirmation, which is
   exactly the "keeps asking, unclear what for" pattern reported. Fix:
   `_read_attachment_for_model` now catches `Exception` broadly (never
   raises), returns `(rendered_text, ok)`; a failed read still gets an
   inline `[could not read: ...]` note appended to the outgoing message (so
   the model and the user both see plainly that it failed, instead of it
   silently vanishing), plus a status-bar notice. An `ImportError`
   specifically gets an actionable hint appended: install the `docs` extra.
   `_on_send_requested` also gained an outer `try/except` as a last-resort
   guard against any *other* unexpected exception in the augmentation path,
   so a send can never again silently disappear. **Action needed on your
   end:** confirm your real "aida" conda environment has the `docs` extra
   installed (`pip install -e ".[docs]"`, or `".[dev,gui,docs]"`) — I
   can't check that environment directly since it's a different machine
   from the one connected to this session.

4. **"May be related to the skill folder not existing?"** — Not actually
   the cause of #3 (that was the PDF `ModuleNotFoundError` above), but a
   real, separate rough edge worth fixing on its own: the skills *directory*
   always exists (`skills_dir()` self-creates it), only the specific skill
   file referenced by a workspace (`review-checklist`, in your case) was
   missing. The warning now spells out exactly where AIDA looked for it —
   `skill file(s) not found (will be skipped): review-checklist (expected
   <skills_dir>/review-checklist.md or <skills_dir>/review-checklist/
   SKILL.md)` — so it's actionable instead of just "not found". Drop a
   `review-checklist.md` (or a `review-checklist/SKILL.md`) into your skills
   folder at that exact path and the warning goes away.

5. **"Can we create the folders if they do not exist?"** — Source/target
   folders previously only ever *warned* when missing; nothing created them.
   `aida.cli.chat._ensure_workspace_folders()` now creates each of a
   workspace's source folders and its target folder (parents included) on
   every session start — used by both `aida chat` and `aida-gui`, since both
   go through the same `start_session()`. A creation failure (permissions, a
   path colliding with an existing file, an unmounted network drive) only
   warns, same "don't crash on a folder problem" policy the rest of
   workspace validation already follows. Tests: `tests/test_start_
   session.py` (4 new tests — creates missing source+target, leaves an
   already-existing folder's contents untouched, warns without raising on a
   genuine OS-level failure, no-op when nothing's configured).

6. **"More console debug — and let me change the level to help with a
   console report?"** — The logging infrastructure
   (`aida.config.logging_setup`) already existed (rotating file handler +
   console handler, log level already exposed in the Settings dialog) but
   nothing in the codebase actually called it — no `logger.debug(...)`
   anywhere. Added real logging at the key decision points a bug report
   like this one needs: `SafetyGuard._authorize` (every read/write/delete
   decision — inside/outside allowed roots, which roots, confirm-callback
   approve/deny outcome), `AgentLoop`'s tool dispatch (every tool call with
   its arguments, unknown-tool warnings, crash tracebacks, and
   success/error outcome), and `start_session`'s workspace resolution
   (validation warnings, folder auto-creation, MCP server start/failure).
   Also fixed a related gap: changing the log level in the Settings dialog
   previously had no effect until the next launch — `open_settings_dialog`
   now calls `configure_logging()` again immediately (it's designed to be
   safe to call repeatedly — see its docstring) so a level change takes
   effect on the spot, the same way the font-size change next to it already
   did. Logs land in the rotating file at `<aida data dir>/logs/aida.log`
   as well as the console, so a "console report" can also just be that
   file. Tests: `tests/test_workspace_safety.py` (2 new caplog-based
   tests), `tests/test_agent_loop.py` (2 new caplog-based tests).

7. **"Can you edit files in place? You have access to the repo folder."** —
   Yes: with your Aida repo connected via the device bridge, all of the
   above fixes were written directly onto your real files at
   `/Users/ilavsky/GitHub/Aida` (not just delivered as a patch) — see the
   commit message in your `git log` / `git status` for exactly what
   changed. I never ran `git commit`/`git push` on your machine; the
   working tree is left with the changes unstaged so you can review and
   commit them yourself.

**Verification:** 520 tests passing (`pytest -q`), `ruff check .` clean, in
the sandbox — a net +8 tests over the count in the completion summary above
(4 from folder-create, 4 from removable source-folder rows, plus the
settings/app/logging tests already counted per-item above).

---

## End-of-Phase-6 code review (before starting Phase 7)

A full read-through of the codebase (not a user report — a deliberate review
pass before opening Phase 7) turned up seven defects. Each was reproduced by
running it first, then fixed with a regression test that fails without the
fix. New tests live in `tests/test_regressions_phase6.py` (17) and
`tests/ui/test_bridge_lifecycle.py` (4), plus 3 in `tests/test_doctor.py`.

1. **Deleting a conversation deleted the user's own files.** *(data loss —
   the one that mattered)* `aida.persistence.cleanup.delete_conversation`
   unlinked every path in the `artifacts` table, but that table holds two
   different kinds of path: copies AIDA made under `~/.aida/artifacts/`, and
   references to files it does not own. `read_file` on a `.png` records an
   `ImageArtifact` pointing at the user's **source** image (the reader
   deliberately doesn't copy the bytes), and `write_file` /
   `write_markdown_report` record a `FileArtifact` pointing at the report
   just written into the user's **target** folder. Both are recorded on the
   live path via `ChatSession.send`. So deleting one conversation hard-
   deleted instrument data and finished reports out of the user's folders —
   with no `_trash` fallback, and in bulk from the GUI's "delete
   conversations older than N days" button. Deletion is now bounded to an
   `artifacts_dir` argument (default `~/.aida/artifacts/`) plus the
   conversation's own sidecar folder and `.md` record; anything else is left
   alone and reported in `DeletionResult.skipped_external_files`, which
   `aida conversations delete` now prints. Containment is checked on
   resolved paths so a symlink can't launder a user file into looking
   AIDA-owned.

2. **Anthropic: parallel tool results were split across separate user
   messages.** `to_anthropic_params` emitted one `{"role": "user"}` message
   per `role="tool"` message. The API requires every `tool_result` for one
   assistant turn's `tool_use` blocks in a *single* user message; splitting
   them isn't a hard error (the API merges consecutive same-role messages)
   but it trains the model to stop making parallel tool calls — precisely
   the "plot all of these" fan-out pyIrena MCP work depends on. Consecutive
   tool messages are now coalesced into one user message, order preserved,
   and an empty result content (which the API rejects) becomes
   `"(no output)"`.

3. **Artifact filenames were trusted input.** An MCP server controls
   `filename` (via `ResourceLink.name`, and the `audio.<subtype>` name
   `aida.mcp.results` derives). `filename="../../escaped.txt"` wrote outside
   `~/.aida/artifacts/` entirely; two artifacts sharing a name silently
   overwrote each other, so an earlier image in a conversation rendered as a
   later, unrelated one. Names now reduce to a bare basename
   (`_safe_filename`) and destinations are collision-safe.

4. **GUI: crash and leaked MCP subprocesses when a session restarts during
   startup.** Switching workspace (or resuming, or closing) while
   `start_session` was still launching MCP servers hit a `ChatBridge.shutdown()`
   that short-circuited on `session is None` — the in-flight start then
   completed *unowned*, leaking its MCP subprocesses and SQLite connection
   for the life of the process, and emitted `session_ready` into a window
   that had already replaced that bridge, where `_on_session_ready` read
   `self.bridge.session` (`None`) and raised `AttributeError` out of a Qt
   slot, leaving the window stuck on "Starting session…". `shutdown()` now
   waits for an in-flight start and closes whatever it produced (idempotently),
   `_restart_session` fully unwires the retired bridge in both directions,
   and `_on_session_ready` guards defensively.

5. **Two same-named figures collapsed to one image in a report.**
   `ArtifactStore.copy_to_target` overwrote by basename. Fixed content-aware
   rather than by always uniquifying, because the transcript writer re-copies
   the *same* images into the sidecar on every export (after every message) —
   unconditional uniquifying would grow `fig (1).png`, `fig (2).png`, ...
   without bound. Identical content reuses the existing file; different
   content gets a fresh name. `render_transcript` now takes the real
   `sidecar_filenames` mapping so links follow a renamed copy.

6. **`aida doctor` never actually checked providers.** It sent a bare
   `urllib` HEAD at each `base_url` and called any non-2xx "unreachable" —
   which reports a healthy Ollama or LM Studio as broken (they answer 404/405
   to a HEAD on `/v1`) and says nothing about whether the model name or key
   work. `aida.providers.profiles.validate_profile` has done this properly
   through each SDK's own client since Phase 2 and was simply never wired in;
   its docstring still claimed "Phase 1 ships no provider layer". Now wired
   in, with a per-profile timeout (an off-site Argo proxy black-holes rather
   than refusing) and the provider closed afterwards. The `records_dir` check
   also honored the *default* location rather than the configured
   `records_dir` override, so it reported on a directory the user's config
   never touches.

7. **`search_text` dropped its truncation marker** when the match cap was
   filled by the last candidate file, handing the model a silently-capped
   result set that looked complete.

**Also moved:** `unique_destination` now lives in the dependency-free
`aida.config.paths` (re-exported from `aida.workspace.safety` for existing
importers) because `aida.artifacts.store` needs it and
`artifacts -> workspace` is an import cycle — `aida.workspace`'s package
`__init__` reaches `aida.mcp`, which imports `ArtifactStore`.

**Verification:** 566 tests passing (`pytest -q`), `ruff check .` clean, in
this environment — net +24 over the 542 at the start of the review.
