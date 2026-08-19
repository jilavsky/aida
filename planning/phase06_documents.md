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

- [ ] Allowed-folders registry (global + per-workspace source/target folders are
      implicitly allowed); path normalization incl. network mounts & symlinks —
      every file operation checks containment
- [ ] Modes per workspace: `relaxed` (no per-action confirmation inside allowed
      folders) and `confirm` (per write/delete confirmation)
- [ ] One-time clear warning when enabling relaxed mode ("agent may modify files
      here without asking; folders should be backed up")
- [ ] Always-confirm regardless of mode: paths outside allowed folders; sending
      local content anywhere except the configured provider
- [ ] Delete = move to `_trash/` inside the allowed folder (configurable off)
- [ ] Confirmation requests flow through the event stream (GUI dialog / CLI prompt)

### Native workspace tools (`aida.workspace.files`)

- [ ] Tools exposed to the LLM: `list_directory`, `find_files`, `search_text`,
      `read_file`, `write_file`, `create_directory`, `copy_file`, `move_file`,
      `delete_file`, `get_file_metadata` — all safety-checked, all with size caps
- [ ] Graceful handling of slow/missing network mounts (timeout + clear error)
- [ ] Tool results are typed (listings as `TableArtifact`/structured, not prose)

### Document readers (`aida.documents.readers`, extra `docs`)

- [ ] Dispatcher by extension/MIME; structured extraction, not blind flattening
- [ ] Text/code/MD/CSV/JSON (stdlib); PDF (`pymupdf`, text + basic layout);
      DOCX (`python-docx`); XLSX (`openpyxl`, sheet→table); PPTX (`python-pptx`,
      slide text); images → `ImageArtifact` passed to vision-capable models
- [ ] Size/token guards: long docs summarized-by-section or chunk-selected rather
      than context-bombed
- [ ] HDF5 deliberately **not** implemented (pyIrena MCP's job) — note in docs

### Writers (`aida.documents.writers`)

- [ ] **`md_obsidian.py` (default):** MD file in target folder; images written to
      user-nameable sidecar folder (workspace `sidecar_folder_name`, default
      `figures`); links relative; safe filename collision handling
- [ ] Agent-facing tools: `write_markdown_report(title, body, images=[artifact refs])`
      and plain `write_file`
- [ ] DOCX writer (basic: headings, paragraphs, images, tables) for Office needs
- [ ] Phase 4's transcript exporter refactored onto `md_obsidian.py` (one writer)

### GUI integration

- [ ] Drag & drop files/folders onto the chat → attached to the next message
      (readers invoked; shown as attachment chips); folder drop offers "add as
      allowed/source folder"
- [ ] Attach button as the non-drag alternative
- [ ] Target folder + sidecar name visible/editable in workspace bar
- [ ] Generated documents appear as file cards (Open / Reveal)

### Tests

- [ ] Safety: containment (incl. `..`, symlink escape), mode behavior, trash-move,
      always-confirm cases — thorough unit tests
- [ ] Reader tests with small fixture files per format
- [ ] Obsidian writer test: MD + sidecar images + relative links roundtrip
- [ ] End-to-end with MockProvider: "read these two files, write a summary MD with
      one image" produces correct on-disk structure

---

## Acceptance — phase is done when all are checked

- [ ] **UC2 demo:** drop a PDF + an MD file onto AIDA, ask questions (correct
      answers), then "write a summary document" → new MD in target folder with
      figures in the named sidecar folder, links working when opened in Obsidian
- [ ] **UC3 full demo:** "find data in <source folder> with Rg 20–50 Å, plot them,
      and write a report listing files + Rg values with the graph" → inline plot AND
      report MD with embedded figure link
- [ ] Relaxed mode: no confirmation dialogs appeared inside allowed folders during
      the above; attempt to write outside allowed folders was blocked with a clear
      confirmation request
- [ ] `_trash/` receives a deleted file instead of hard deletion
- [ ] CI green

## Out of scope for this phase

Shell/python command execution (Phase 9); RAG ingestion of documents (Phase 8 —
this phase reads files into context directly); web fetch/search (Phase 9).
