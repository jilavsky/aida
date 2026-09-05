# Documents, figures, and what survives a restart

**Status: design discussion, 2026-09-04. Level 1 committed; the rest
open.** Written after the document-budget fix (`COMPLETED.md` §10), which
raised the amount of *text* reaching the model and made the next question
obvious: what about the figures? Answering that surfaced a second,
more urgent question about attachment persistence — §2.

## 1. What happens today: text only

**Images embedded in a PDF, DOCX, XLSX or PPTX are not extracted and never
reach the model.** Every reader in `aida/documents/readers.py` is
text-only:

| Format | Extracted | Dropped |
|---|---|---|
| PDF | `pymupdf` `page.get_text()`, page by page | every figure, plot, scanned page, image-only table, equation rendered as an image |
| DOCX | `python-docx` paragraphs + table cells as pipe rows | every inline and floating image |
| PPTX | `python-pptx` `shape.text_frame.text` per slide | every picture, chart and diagram — on a slide deck, usually *most of the content* |
| XLSX | `openpyxl` cell values per sheet | embedded charts and images |

A standalone `.png`/`.jpg` *is* handled — `_read_image_file` returns an
`ImageArtifact` and, on a `supports_vision` profile, the GUI attach path
sends its pixels. That path works. Nothing routes a document's *internal*
images into it.

**The failure mode worth naming:** a scanned PDF returns empty text with no
warning. The model receives a document that appears to have no content and
cannot tell whether the file was empty, unreadable, or a picture. The user
sees the model behave as though the attachment did not exist.

## 2. What survives a chat restart — half of what you'd assume

Traced through `recorder.py` → `store.py` → `records.py`:

| Thing | Survives resume? | Why |
|---|---|---|
| Extracted document **text** | **Yes** | `_augment_with_attachments` inlines it into the user message's `content`, and every message is persisted to `messages`. The same is true of an agent-initiated `read_file` — its tool result is a persisted message. |
| Attached **image pixels** | **Yes** — *corrected 2026-09-05* | `ConversationRecorder._own_attached_images` adopts each one into the artifact store first, and it is *that* copy's path `append_attached_images` records. An earlier reading of this file missed the `_own_attached_images` call and reported the raw `ref.path`; verified since by deleting the original and resuming. |
| The **original PDF** | **No** | Never copied anywhere. Only its extracted text lives on. |
| Attachments in the **`.md` transcript** | **No** | `render_transcript`/`write_transcript` filter on `kind == "ImageArtifact"`; user attachments are `USER_IMAGE_KIND = "UserImage"`, so they are neither copied into the sidecar folder nor linked in the transcript. |

So the model keeps the document's *words* across a restart, and an attached
image keeps its pixels — both better than expected. The real gap is
narrower than first written here: it is the **attached documents**. A PDF
dropped in from a Downloads folder that later gets cleaned leaves the
conversation discussing a paper nobody can open again, and the transcript
in `~/Documents/Aida/` holds what the model *said* about it but not the
thing itself.

`aida.persistence.cleanup` is already correct about this and should stay
that way: it deletes only files inside `~/.aida/artifacts/`, and
deliberately *skips* recorded paths outside it because those are the
user's own files (`_is_inside` is the guard). Copies AIDA makes for itself
are a different category and may be deleted with the conversation.

**This is a bug independent of figures**, and it decides the storage
question below.

## 3. The design: ingest once, hand over a manifest, let the agent pull

The natural conclusion from §2 plus the "which one is Figure 1?" problem.
Pushing figures at the model blind is not useful — an unlabeled blob the
agent cannot name is worse than a note saying a figure exists. Inverting it
solves both:

**On attach, a document is ingested once into a per-conversation folder in
the records dir:**

```
~/Documents/Aida/
  attachments/<conv8>/
    paper.pdf                 <- the original, copied
    paper.md                  <- extracted text
    paper.assets/
      fig-01.png  fig-02.png  ...
  figures/<conv8>/            <- existing sidecar, unchanged
  my-analysis-<conv8>.md      <- existing transcript
```

`attachments/` is a peer of the existing `figures/` sidecar and reuses its
`conversation_id[:8]` convention, so it nests for per-user switching later
(`planning/multiuser_plan.md`) exactly as the rest of the records dir does,
and `delete_conversation` extends to it in three lines.

**The model gets text plus a figure index, not the figures:**

```
[Attached: paper.pdf — 14 pages. Full text above.
 Figures available — call get_document_figure(document, label) to view one:
   Figure 1 — "SAXS patterns of PS-b-PMMA, 25–150 °C"
   Figure 2 — "Guinier fits for the three annealing times"
   Table 3  — (image-only table)]
```

A new `get_document_figure` tool returns the named image as an
`ImageArtifact`, which the existing vision path already handles.

Why this shape is right:

- **`MAX_ATTACHED_IMAGES = 4` stops being a limitation and becomes the
  correct budget.** It bounds a *pull* — the agent asks for the two
  figures it needs — instead of truncating a *push* of twelve. No change
  to `providers/vision.py` needed.
- **The user is told what was dropped and what is available**, which is
  the Level-1 warning generalized rather than bolted on.
- **A `supports_vision: false` profile degrades honestly**: the index is
  still text, the agent still knows Figure 1 exists and can say it cannot
  see it.
- **Resume works** because the folder is on disk, owned by AIDA, and
  independent of where the user's original file went.
- **It is browsable and cleanable** — the point of putting it in
  `~/Documents/Aida/` rather than `~/.aida/`.

The whole design rests on the figure index being *correct*. If "Figure 1"
maps to the wrong picture, this is worse than doing nothing.

## 4. Where labels come from — and why this is the hard part

**With `pymupdf` alone: unreliable on real papers.** `page.get_images()`
returns image XObjects with no captions; you get bboxes from
`page.get_image_rects()` and must pair each with the nearest text block
matching `^(Fig(ure)?|Table|Scheme)\s*\.?\s*\d+`. That works on
single-column reports and degrades badly on the two-column layouts most
SAS journals use (*J. Appl. Cryst.*, *J. Chem. Phys.*), where "nearest
below" regularly picks the neighbouring column. It also returns every
image object — logos, rules, background textures, the same journal
ornament on all fourteen pages — so a size/aspect filter is mandatory or
the index fills with 20×20 px junk.

**With an OCR/layout service: the hard part is already solved upstream.**
Mistral OCR returns "ordered interleaved text and images": per-page
`markdown` with inline `![img-0.jpeg](img-0.jpeg)` placeholders in
*reading order*, plus an `images` array carrying the actual bytes, plus
`blocks` with bounding boxes. Pairing an image with its caption becomes
"take the adjacent paragraph in the markdown," which is a few lines and is
right on multi-column layouts because the reading-order problem was solved
before the text reached us.

**This is the real argument for the OCR option** — not nicer text, but the
thing that makes §3's design actually work on the documents Jan attaches.
Without it, the figure index is achievable only unreliably, which by §3's
own standard means not worth building.

Even so, be honest about the ceiling: the label still comes from adjacency
heuristics over the markdown, not ground truth. It will be right most of
the time and occasionally wrong. Design for recoverable wrongness — the
index should carry the caption text, so an agent that pulls the wrong
figure can notice and say so.

## 5. Mistral OCR as an optional backend

### Dependencies — none, if done directly

Two ways in:

- The official `mistralai` SDK. Pulls httpx, pydantic and friends; a
  heavyweight addition for a rarely-used optional feature, and another
  pydantic/httpx pin to keep from fighting the `openai` and `anthropic`
  SDKs.
- **Raw REST. Three calls, zero new packages:** `POST /v1/files`
  (`purpose=ocr`), `GET /v1/files/{id}/url` for a signed URL, then
  `POST /v1/ocr` with `model: "mistral-ocr-latest"` and
  `include_image_base64: true`. Parse `pages[].markdown` and
  `pages[].images[]`.

**Recommendation: raw REST.** It matches `DESIGN.md`'s dependency policy,
the API surface is three endpoints, and it keeps an optional feature from
constraining the two SDKs that actually matter. One caveat: `httpx` is
currently only a *transitive* dependency (via `openai`/`anthropic`).
Using it directly means declaring it in the `ocr` extra — relying on a
transitive dependency is exactly the kind of thing that breaks silently on
someone else's resolver.

Proposed extra: `ocr = ["httpx>=0.27"]`, and the module imports lazily
inside the function like every other optional reader already does.

### Cost

1000 pages per dollar (roughly double the throughput via batch). A 15-page
paper is about 1.5 cents. For occasional use this is effectively free, and
it is cheap enough that a per-call cost warning would be noise.

### Credentials

Fits the existing secrets layer with no new machinery: keyring via
`aida.config.secrets` with the `AIDA_SECRET_<NAME>` env fallback that
`aida run` and scheduled fires already authenticate through, plus an
`aida doctor` check. On a shared beamline machine one OS login means one
keychain, so this is a machine-level key in practice — same conclusion
`multiuser_plan.md` §4 reaches for provider secrets, and worth saying out
loud rather than implying per-user keys.

### Availability and fallback

Non-negotiable, and it is the same shape as the `docs` extra's existing
lazy-import handling: if the extra is missing, the key is unset, the
service is unreachable, or the call fails or times out, **fall back to
`pymupdf` text extraction and say so in the text handed to the model** —
`[OCR unavailable, extracted text only; N images not extracted]`. A
document must never fail to attach because a network service was down.
`aida/ui/qt/main_window.py::_read_attachment_for_model` already has the
belt-and-braces "never raise, always tell the model what went wrong"
pattern this needs; extend it rather than inventing a second one.

### The part that needs a real decision: consent

Jan's framing — "if the user creates the key and provides it, they accept
the limitations" — is right for the *user*, and I'd accept it as the rule
for interactive use. Two places it is not sufficient:

1. **The agent can attach documents on its own**, and in a workflow or a
   scheduled fire there is no human at the keyboard. "The user consented
   once by pasting a key in Settings" is thin cover for a scheduled job
   uploading whatever landed in a watched folder. Mistral states it does
   not train on La Plateforme prompts or outputs, which helps; it does not
   settle retention, and it does not settle whether an unpublished
   manuscript, an unreleased vendor datasheet or a proposal under review
   may leave the lab. That is an Argonne policy question, not a technical
   one, and AIDA should not quietly answer it.
2. **The consent moment belongs at the document, not only at the key.**

Concretely, and entirely within the existing safety model:

- Off by default. Enabled per *workspace*, not just per install — so a
  `usaxs-user` workspace can have it and an instrument workspace need not.
- Confirmation on upload, naming the file and the destination
  ("Send `draft-paper.pdf` to Mistral OCR? This uploads the document to a
  third-party service."), with "Allow for this chat" available so working
  through a stack of papers is not torture.
- **Never in a headless or scheduled run** unless explicitly pre-approved
  via the existing `--preapprove-tool`/`preapproved_tools:` mechanism.
  This is precisely what that mechanism is for.
- A one-line note in the docs that documents leave the machine, with a
  pointer to Mistral's terms — stated plainly, not buried.

`fetch_url` is the precedent for "network egress always asks." OCR upload
is strictly more sensitive than fetching a public URL, since it *sends*
user content rather than retrieving public content.

## 6. Recommended order

1. **Level 1 — the warning. Do it now, unconditionally.** Count embedded
   images per format and append a note; detect the near-empty-text PDF and
   say it looks scanned. ~20 lines, no new dependency (`page.get_images()`,
   `document.inline_shapes`, `shape.shape_type == PICTURE` are all in the
   `docs` extra already), no decisions. Converts a silent wrong answer into
   an accurate one, and it is the fallback message every later level needs
   anyway. **In `PLAN.md` §1.5.**
2. **The attachment folder (§2/§3 storage), independent of figures.**
   Copy the original and the extracted text into
   `~/Documents/Aida/attachments/<conv8>/`, record AIDA's copy rather than
   the user's path, extend `delete_conversation`, and link it from the
   transcript. This fixes a real persistence bug on its own and is worth
   doing whether or not any figure work follows.
3. **The figure index and `get_document_figure`**, on the `pymupdf`
   backend first — accepting that labels will be shaky on two-column
   papers, and saying so in the index when confidence is low.
4. **The OCR backend**, which is what makes step 3 trustworthy. Ship it
   behind the extra, the per-workspace switch and the upload confirmation.
5. **Never in-tree**: `marker`/`docling`/`nougat`-class local conversion.
   PyTorch and model weights would wreck the `pip install and it works`
   property §1.1 is protecting. If wanted, it is an MCP server — the
   audited-preset pattern `PLAN.md` §2.7 describes — or a Phase 9 script in
   the user's own environment, which works today with no AIDA change.

The through-line is unchanged: text extraction is AIDA's job because it is
cheap, dependency-light and deterministic. Document *understanding* is a
different product, and AIDA should borrow one over a network or over MCP
rather than absorb it.
