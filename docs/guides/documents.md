(documents)=
# Attached documents and figures

When you attach a document in the GUI, AIDA reads it for the current message
and keeps a copy with the conversation. The status bar names the accepted
files, and **File → Open Conversation Folder** opens their location:

```{figure} img/documents/documents-01-attached.png
:alt: The input box with one attached file shown as a chip reading silica-pdms-usaxs.pdf with a small × button above the typed question "What figures are in this paper?", and the Send and Attach… buttons on the right. Circle 1 marks the chip, circle 2 the Attach… button.

A PDF attached before sending: (1) the attachment chip (× removes it), (2) **Attach…**. Dropping a
file on the input box does the same.
```

```text
<records_dir>/attachments/<first-8-characters-of-conversation-id>/
```

The folder can contain the original file, extracted text such as
`paper.pdf.md`, and a lazily created `paper.assets/` cache for figures. Only
files you explicitly attach are copied; a file the agent reads from a source
folder stays where it already lives.

The copy is made before the message is sent, and the message ends with the
path of each copy. The agent can read anything in this folder with
`read_file` without asking, even though it is outside your allowed folders,
so it never has to look for the original. Writing or deleting there, or
running an attached script, is confirmed like anywhere else outside the
allowed folders.

Deleting a conversation also deletes its attachment and figure-cache
folders. If files were removed or moved outside AIDA and an orphan folder is
left behind, inspect and remove it with:

```bash
aida conversations gc
```

The command lists affected folders and asks before permanent deletion. Use
`--yes` only when you intentionally want to skip that confirmation.

(documents-figures)=
## Asking about figures

Every session includes two tools for documents attached to that conversation:

- `list_document_figures` lists labels, captions, pages, and confidence as
  text.
- `get_document_figure` returns one requested image for visual inspection.

Extraction happens on the first figure request and is cached. The built-in
extractor is dependable for simple pages, but reports lower confidence when
a multi-column layout makes caption pairing ambiguous.

Both PDF and Word (`.docx`) documents are supported. A `.docx` is a zip with
its pictures stored whole, so extraction needs no extra dependency: pictures
are read in document order and paired with the caption paragraph immediately
before or after them ("Figure 3.", "Table 1"). A picture with no caption
beside it is listed positionally as *image N* rather than being given a
number it does not have. Word documents have no page numbers until they are
laid out, so those entries list no page. Pasted vector art (EMF/WMF/SVG —
often an Excel chart) cannot be shown as a picture; the listing says how many
were skipped so you can export those as PNG if one matters.

**Nothing is extracted when you attach a document** — only when something
asks about its figures. So a paper you attach and merely summarize costs
nothing, and no `paper.assets/` folder appears. Ask *"what figures are in
that paper?"* to set it off.

On a multi-column PDF the built-in extractor's caption pairing is a guess,
and it says so (`low` confidence). When a model with vision is available
(see below), AIDA converts just those pages and takes each caption from the
page as the model reads it, in reading order. A page with one figure then
gets `high` confidence. Pages whose figures were already paired confidently
are not converted, so a paper usually costs a few page conversions, not
one per page. The listing says which pages were re-captioned and by which
model.

(scanned-pdfs)=
## Scanned PDFs and pages without text

A PDF page that is only an image (a scan, a photographed page) has no text
for AIDA to read. Such pages are rendered as images and sent to a **model
with vision**, which writes them out as Markdown in reading order, with
equations as LaTeX and tables as Markdown tables. This happens when the
agent reads the file with `read_file`; on a PDF that mixes text and scanned
pages, only the scanned pages are converted. The agent can also ask for
every page to be converted (`read_file` with `ocr: true`), for a PDF whose
text layer exists but is garbled.

**Equations drawn as images.** Many typeset papers and proofs place each
display equation as an image or as drawn lines, so the text layer holds
only the equation number, "(3)", and none of the formula. AIDA recognises a
number standing alone on its line, with no text beside it in its column,
and treats that page like a scanned one: `read_file` converts it, so the
formulas come back as LaTeX. The plain text says which equations are
missing, so the agent knows to ask.

For a document you attach in the GUI, the attachment's text tells the agent
which pages need converting and where the file is, and the status bar says
whether a model can convert them.

**Long documents.** An attachment, or one `read_file` call, holds up to
100,000 characters, roughly 15 to 20 dense two-column pages. A longer
document ends with a note giving the page where the text stops and the
`offset` that `read_file` continues from, so the agent reads the rest
instead of stopping there.

Converted pages are marked as such in what the agent reads, and they are
kept in `~/.aida/cache/ocr` (by file content, model and render size), so a
document is converted once. That folder is safe to delete.

### Which model converts the pages

In this order:

1. **An OCR profile, if you set one**: any provider profile, chosen under
   **Settings → Documents → OCR profile** (or `ocr_profile:` in
   `config.yaml`). A small dedicated OCR model on a machine that can run
   one is the best choice: faster and better on full pages than a general
   model. If it does not answer (the server is not running, the laptop is
   off the network), AIDA uses the next option and adds a note.
2. **Otherwise the chat model**, if its profile has **vision** enabled
   (`supports_vision: true`). At the beamline that is usually a cloud model
   through the Argo proxy, and nothing needs to be installed.
3. **Otherwise nothing is converted**, and the agent is told why and what
   to change, for example: *"Converting it needs a model with vision, and
   the current profile 'Splash-Flash' has supports_vision off. Enable
   vision on this profile if the model supports images, or set an OCR
   profile."* A scanned page is never passed off as an empty one.

```{figure} img/documents/documents-02-settings-documents.png
:alt: The Documents group of the Settings dialog: three lines saying scanned PDF pages are read by a model with vision, the OCR profile if set, otherwise the chat model when its profile has vision, and that a separate remote OCR profile asks first; a ticked "Convert scanned PDF pages" checkbox; and an "OCR profile" drop-down showing "(use the chat model)".

**Settings → Documents**. Untick **Convert scanned PDF pages** (`ocr_backend: off`) to never
convert; the agent is then told the pages are unread.
```

```yaml
# config.yaml
ocr_backend: auto        # or: off
ocr_profile: light-ocr   # optional; a profile name from providers.yaml
```

### Where the pages go

Pages go to the same place your chat already goes, so using the chat model
needs no extra permission. An OCR profile on this machine (`localhost`,
`127.0.0.1`) needs none either. Only an OCR profile on a **different remote
server** than the chat asks first, once per document, with **Allow for this
chat** available. An unattended run (`aida run`, the scheduler) refuses
that unless started with `--preapprove-tool ocr_page_upload`.

### Local OCR models, where they can run

If a machine can run models locally (Ollama, LM Studio, llama.cpp), a small
dedicated OCR model set up as its own profile works well as the OCR
profile. We recommend **LightOnOCR-3 1B** (Ollama
`Maternion/LightOnOCR-3:1b`). On a 24-page two-column paper it gave the
text in reading order, every display equation as LaTeX, each figure
followed by its caption, and tables as HTML, at about 12 seconds per page
on a Mac. Check equation labels and subscripts against the original: it
occasionally drops a label or a subscript.

The 4B version (`Maternion/LightOnOCR-3:4b`) is only slightly better and
uses about 12 GB of memory while it runs. Next to a large chat model on the
same machine, that left the computer barely responding, so use it only on a
machine with memory to spare. Other candidates include LightOnOCR-2, GLM-OCR
and PaddleOCR-VL. A profile for the 1B model:

```yaml
# providers.yaml
profiles:
  light-ocr:
    kind: openai_compat
    base_url: http://localhost:11434/v1
    model: Maternion/LightOnOCR-3:1b
    supports_vision: true
```

Then choose `light-ocr` in **Settings → Documents**. LightOnOCR-3 is a
"thinking" model that sometimes writes the whole page into its hidden
reasoning and sends an empty reply; AIDA notices, asks again with reasoning
switched off (`reasoning_effort: none`), and keeps doing so for the rest of
that document. Machines that cannot install a model runtime, which
includes most beamline workstations, use the chat model instead.

% CHECK(Jan): 1B recommended after one real paper (2026-11-21); the chat-model route checked with a cloud model on Windows (2026-11-26).

(documents-checking)=
## Checking that it works

**Which model would convert a page?** `aida doctor` prints it for the
active workspace, for example *"the OCR profile 'light-ocr', falling back
to the chat model (profile 'argo-claude')"*, or why none would.

**Does it actually work?** Convert a real page and read the result. In
the GUI, click **Settings → Documents → Test on a page…**, choose a PDF and
a page number (it suggests the first page a chat would convert, and says
which model the page will be sent to). The page is converted with the
settings shown in the dialog, saved or not, so you can try an OCR profile
before keeping it. The result opens beside the rendered page, as raw
Markdown and as a preview, with the model used and the time it took. The
**Figures** tab shows the figures a chat would extract from that page, with
their labels and captions. When their number matches the image placeholders
in the Markdown (`![fig-1](fig-1)`), they are paired in order, as in a chat,
and the preview shows each one in place; when it does not, the tab says so.
The test always converts afresh rather than reusing the cache.

From the command line:

```bash
aida documents convert scan.pdf                 # pages without text or with missing equations
aida documents convert paper.pdf --pages 2,5-6  # chosen pages
aida documents convert paper.pdf --all --profile argo-claude
```

It prints the route, the model that was used and each page's Markdown, and
exits non-zero if a page could not be converted.

**How were a document's figures labelled?** Run the same extraction a chat
runs:

```bash
aida documents figures paper.pdf
```

It prints `builtin` or `vision`, each figure with its confidence, and the
reason when vision re-captioning was not used. `--no-ocr` skips it and
`--json` gives machine-readable output. In a conversation, the answer from
`list_document_figures` says the same, and the cached
`<document>.assets/index.json` records it as `"backend"`.
