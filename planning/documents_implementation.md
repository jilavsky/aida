# Documents, figures and OCR — implementation plan

**Status: accepted, 2026-09-04.** Decisions settled in discussion; this is
the build order. Design rationale and the alternatives that were rejected
are in [`document_images.md`](document_images.md) — read that first if you
want *why*. This file is *how*, in order, with the tests that make each
phase done.

**Ordering note.** Phase A is independent and can ship today. Phases B–D
write files into the records dir, so the folder layout must be settled
first — see [`multiuser_plan.md`](multiuser_plan.md) §0 for why the
organization layer goes before Phase B, and not after.

---

## Phase A — say what was dropped — **DONE 2026-09-04**

No storage, no schema, no new dependency, no decisions left open. This is
a correctness fix: today an image-only PDF arrives as a silently empty
document and the model cannot tell that from an empty file.

**`aida/documents/readers.py`**

- `_read_pdf_file`: count images per page with `page.get_images(full=False)`;
  after the text is assembled, if the total is > 0 append
  `\n[This document contains N embedded images, which were not extracted.]`
- Same shape for `_read_docx_file` (`document.inline_shapes` plus image
  parts in `document.part.related_parts`), `_read_pptx_file`
  (`shape.shape_type == MSO_SHAPE_TYPE.PICTURE`), `_read_xlsx_file`
  (`sheet._images` — private, so guard with `getattr`).
- **Scanned-PDF detection**, the case that actually matters: if the
  extracted text across all read pages is under a small threshold
  (~50 non-whitespace characters per page, averaged) *and* the page count
  is > 0, append
  `[No extractable text — this appears to be a scanned or image-only PDF.
  Its N pages were not converted.]`

The notes go in the returned `TextArtifact`'s text, so every consumer —
`read_file`, GUI attach, RAG ingestion — gets them for free with no call-site
change.

**Tests** (`tests/test_documents.py`): a generated PDF with an embedded
image reports the count; a text-only PDF adds no note; an image-only PDF
trips the scanned branch. Build the fixtures with `pymupdf` at test time
rather than committing binaries.

**Shipped 2026-09-04.** Implemented in `aida/documents/readers.py`:
`_count_embedded_media` (zip-container counting, uniform across the three
Office formats and stdlib-only), `_dropped_images_note` and
`_pdf_content_note`. 12 new tests in `tests/test_document_readers.py`;
full non-GUI suite green (1214 passed), ruff clean.

Two decisions taken during implementation, worth recording:

- **Office image counting reads the zip container** (`word/media/`,
  `xl/media/`, `ppt/media/`) rather than library internals. `openpyxl` is
  loaded `read_only=True` on purpose and does not populate `sheet._images`
  in that mode, so the obvious approach would have silently reported zero
  images for every spreadsheet — worse than not reporting. The zip route is
  cheap (central directory only), uniform, and independent of three
  different libraries' private attributes. It slightly *over*-counts for
  `.pptx`, where `ppt/media/` also holds layout, master and theme images;
  that is the right direction to be wrong in, since over-reporting makes
  the model ask while under-reporting lets it assume a figure-heavy deck
  was read in full.
- **The note is appended after truncation**, not before. A note explaining
  what was dropped is worthless if it is itself what gets dropped. Costs a
  few dozen characters over the budget in the worst case; there is a test.

---

## Phase B — the attachment store

Fixes a real persistence bug on its own merits (`document_images.md` §2:
extracted text survives a resume, the files do not) and lays the ground for
C and D.

### B1. Layout

```
<records_dir>/attachments/<conv8>/
    paper.pdf                 the original, copied
    paper.md                  extracted text
    paper.assets/             figures, once Phase C lands
```

A peer of the existing `figures/` sidecar, same `conversation_id[:8]`
convention. `records_dir` is whatever the organization layer resolved it to
(per-user or not) — this module must never compute it itself.

New in `aida/persistence/records.py`, mirroring `sidecar_dir`:

```python
def attachments_dir(records_dir: Path, conversation_id: str) -> Path:
    return records_dir / "attachments" / conversation_id[:8]
```

### B2. Record where the files actually went — do not recompute it

**This is the one thing to get right, and it is a latent bug today.**
`delete_conversation` computes `sidecar_dir(records_dir, ...)` from the
*current* `records_dir` setting. Change the Records folder in Settings and
every older conversation's sidecar folder becomes an undeletable orphan —
nothing points at where those files really are. For `figures/` that is
clutter. For `attachments/`, holding a copy of a confidential manuscript,
it is a broken promise.

Fix, following the precedent `record_path` already sets: migration 5 adds

```sql
ALTER TABLE conversations ADD COLUMN attachments_path TEXT;
```

written on first ingest, read verbatim on delete. Add
`attachments_path` to `ConversationSummary`/`_row_to_summary` and a
`set_attachments_path` setter alongside `set_record_path`.

*While in here*, do the same for the sidecar: record the resolved sidecar
path on first write, and have `delete_conversation` prefer the recorded
value and fall back to the computed one for pre-migration rows. Small, and
it closes the same hole for figures.

### B3. Ingest on attach

`aida/ui/qt/main_window.py::_read_attachment_for_model` and the `read_file`
tool path both gain the same step, factored into one helper in
`aida/documents/` so there is a single implementation:

1. Copy the source file into the conversation's attachments dir, via
   `paths.unique_destination` so a second `paper.pdf` becomes `paper (1).pdf`
   rather than clobbering the first.
2. Write the extracted text beside it as `<stem>.md`.
3. Record the copy — for an attached image, `append_attached_images` now
   stores **AIDA's copy path, not the user's original**. That is what makes
   a resume survive the user cleaning out Downloads, and it moves the row
   from "the user's file, never delete" into "AIDA's file, delete with the
   conversation", which is exactly what Jan asked for.
4. Return the same text as today, plus the Phase A notes.

Do **not** ingest on a plain `find_files`/`search_text` hit — only on an
actual attach or `read_file`, or the folder fills with everything the agent
glanced at.

### B4. Deletion — the hard requirement

The rule: **deleting a conversation deletes its documents.** A user who
deletes a chat containing an unpublished manuscript must not find that
manuscript still sitting in their home directory.

`aida/persistence/cleanup.py::delete_conversation`:

- After the sidecar `rmtree`, resolve the attachments dir from the recorded
  `attachments_path` (falling back to the computed path for old rows) and
  `shutil.rmtree` it.
- Guard it with the existing `_is_inside(path, records_dir)` so a corrupted
  or hand-edited row can never point the delete at `/`. This guard is not
  optional; it is the difference between a cleanup and an incident.
- Add `deleted_attachments_dir: bool` to `DeletionResult`, and surface it
  in the CLI's deletion summary and the GUI's confirmation text — the user
  should be *told* the documents went, not left to hope.

`aida/cli/conversations.py` cleanup-older-than and the GUI's bulk cleanup
both route through `delete_conversation`, so they inherit this. Verify that
in a test rather than assuming it.

**An orphan sweeper**, because a promise like this needs a backstop: a new
`aida doctor` check (and a `aida conversations gc` command) that lists
`attachments/*` folders with no matching conversation row and offers to
remove them. Orphans can exist from an interrupted delete, a hand-deleted
DB, or a records_dir moved before B2 landed.

**Tests** — these are the ones that matter most in this whole plan:

- Delete a conversation with attachments → the folder is gone, and the
  user's *original* file is untouched.
- Cleanup-older-than deletes attachments for every conversation it removes.
- A conversation whose `records_dir` setting changed after ingest still has
  its attachments deleted (this is the B2 regression test).
- `_is_inside` rejects an `attachments_path` pointing outside the records
  dir; nothing is deleted and the result says so.
- Resume after the user's original file is deleted → pixels still render.

### B5. Telling the user where it went

Light touch, no dialog:

- Status bar on ingest: `Attached paper.pdf — copied to your Aida
  attachments folder`.
- A **Open Conversation Folder** item in the File menu, next to the
  existing records-folder action.
- One line in the `.md` transcript header linking the attachments folder
  relatively, so the Obsidian view of a conversation reaches its sources.
- `docs/documents.md`: what gets copied, where, and that it is deleted with
  the chat.

---

## Phase C — the figure index and `get_document_figure`

Only after B. Backend-agnostic: it consumes whatever the extractor produced.

- Extractors write figures to `<stem>.assets/fig-NN.png` plus a small
  `<stem>.assets/index.json`: `[{label, caption, file, page, confidence}]`.
- The text handed to the model gains the index block described in
  `document_images.md` §3 — never the images themselves.
- New tool `get_document_figure(document, label)` in
  `aida/documents/tools.py`, returning an `ImageArtifact` from the assets
  folder. It reads only inside the conversation's own attachments dir —
  a path check, not a `SafetyGuard` call, since these are AIDA's files.
- `pymupdf` backend first: `page.get_image_rects()` + nearest text block
  matching `^(Fig(ure)?|Table|Scheme)\s*\.?\s*(\d+)`, with a size/aspect
  filter to drop logos and rules. **Set `confidence: "low"` and say so in
  the index** on a multi-column page — an honest "figures detected, labels
  uncertain" beats a confident wrong mapping.

**Tests:** a single-column PDF labels correctly; a two-column one degrades
to low confidence rather than mislabelling; `get_document_figure` refuses a
path outside the conversation's folder; the vision cap is respected because
the agent pulls one at a time.

---

## Phase D — the Mistral OCR backend

Optional, off by default, and the thing that makes C's labels trustworthy
on real journal papers.

### D1. Dependency and client

- New extra: `ocr = ["httpx>=0.27"]`. `httpx` is present transitively via
  `openai`/`anthropic` today; using it directly means declaring it.
  **No `mistralai` SDK** — three endpoints do not justify it, and it would
  add another pydantic/httpx pin to fight the two SDKs that matter.
- `aida/documents/ocr/mistral.py`, imported lazily like every other
  optional reader: `POST /v1/files` (`purpose=ocr`) → `GET
  /v1/files/{id}/url` → `POST /v1/ocr` with `model: "mistral-ocr-latest"`,
  `include_image_base64: true`. Parse `pages[].markdown` and
  `pages[].images[]`; pair each inline `![img-N.jpeg](img-N.jpeg)`
  placeholder with the adjacent paragraph in reading order to get its
  caption, then write out C's `index.json` with `confidence: "high"`.

### D2. Settings — the key

Same storage as provider keys: `aida.config.secrets.set_secret` under a
fixed ref (`"mistral-ocr"`), keyring with the `AIDA_SECRET_MISTRAL_OCR`
env fallback that headless runs already use. Write-only in the dialog,
never read back, exactly as `profiles_dialog` does it.

New **Document OCR** group in the Settings dialog:

- A short explanatory line and a link: *Get a free API key from
  console.mistral.ai/api-keys. The free tier covers roughly 10 documents /
  50 MB at a time — enough for occasional use.*
- **A plain statement that documents are uploaded to Mistral**, with a link
  to their terms. Not buried.
- API key field (write-only), a **Test** button calling `aida doctor`'s new
  check, and a **Clear key** button.

### D3. Workspace — the switch

`WorkspaceConfig.use_ocr: bool = False`, edited in
`WorkspaceManagementDialog` next to the existing per-workspace fields.
This is the per-workspace decision Jan asked for and the reason it is not a
global setting: a manuals workspace can have it on while a workspace where
unpublished manuscripts are reviewed keeps it off.

### D4. Consent at the document

- Confirmation on upload, through the existing safety layer, naming the
  file and the destination: *"Send `draft-paper.pdf` to Mistral OCR? The
  document is uploaded to a third-party service."* — with **Allow for this
  chat** available, so working through a stack of manuals is not torture.
- **Never in a headless or scheduled run** unless pre-approved via the
  existing `--preapprove-tool` / `preapproved_tools:` mechanism. The
  default headless answer is refuse-with-message, and the message says to
  pre-approve it deliberately. This is what that mechanism exists for.
- `fetch_url` is the precedent for "network egress always asks"; OCR is
  strictly more sensitive because it *sends* the user's content.

### D5. Fallback — non-negotiable

Extra missing, key unset, service unreachable, HTTP error, timeout, or a
file over the size limit → **fall back to `pymupdf` text and say so in the
text handed to the model**: `[OCR unavailable (reason); extracted text
only. N images were not extracted.]` A document must never fail to attach
because a network service was down.
`_read_attachment_for_model` already has the "never raise, always tell the
model what went wrong" pattern; extend it rather than growing a second one.

Add an `aida doctor` check: extra installed, key present, endpoint
reachable — reporting each separately, since "no key" and "no network" want
different fixes.

**Tests:** a stubbed HTTP layer (no network in CI) covering a good
response, a 401, a timeout and a malformed body — the last three must all
land in the `pymupdf` fallback with the reason in the text. Plus: OCR is
not attempted when `use_ocr` is false, and a headless run without
pre-approval refuses rather than uploading.

---

## Summary of schema and config changes

| Change | Where | Phase |
|---|---|---|
| `conversations.attachments_path` (+ recorded sidecar path) | migration 5 | B |
| `DeletionResult.deleted_attachments_dir` | `persistence/cleanup.py` | B |
| `attachments_dir()` | `persistence/records.py` | B |
| `get_document_figure` tool | `documents/tools.py` | C |
| `WorkspaceConfig.use_ocr` | `config/settings.py` | D |
| `ocr` extra, `secret_ref: "mistral-ocr"` | `pyproject.toml`, Settings | D |

Nothing here touches the agent loop, the provider layer, MCP or RAG.
Phase A is a bug fix; B is a bug fix with a feature attached; C and D are
the new capability, and both are optional at runtime.
