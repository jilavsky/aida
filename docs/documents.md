# Attached documents and figures

When you attach a document in the GUI, AIDA reads it for the current message
and keeps a copy with the conversation. The status bar names the accepted
files, and **File → Open Conversation Folder** opens their location:

```text
<records_dir>/attachments/<first-8-characters-of-conversation-id>/
```

The folder can contain the original file, extracted text such as
`paper.pdf.md`, and a lazily created `paper.assets/` cache for figures. Only
files you explicitly attach are copied; a file the agent reads from a source
folder stays where it already lives.

Deleting a conversation also deletes its attachment and figure-cache
folders. If files were removed or moved outside AIDA and an orphan folder is
left behind, inspect and remove it with:

```bash
aida conversations gc
```

The command lists affected folders and asks before permanent deletion. Use
`--yes` only when you intentionally want to skip that confirmation.

## Asking about figures

Every session includes two tools for documents attached to that conversation:

- `list_document_figures` lists labels, captions, pages, and confidence as
  text.
- `get_document_figure` returns one requested image for visual inspection.

Extraction happens on the first figure request and is cached. The built-in
extractor is dependable for simple pages, but reports lower confidence when
a multi-column layout makes caption pairing ambiguous.

## Optional Mistral OCR

Mistral OCR can improve figure/caption ordering in multi-column PDFs. Install
the optional dependency with `pip install "aida-workbench[ocr]"`, enter the
key under **Settings → Document OCR**, and enable **Use Mistral OCR for
figures in attached documents** in each workspace that should use it. The
key is write-only in the GUI and is stored in the OS keychain. Headless
systems can instead set `AIDA_SECRET_MISTRAL_OCR`.

This feature is off by default. When enabled, documents you ask about leave
your machine and are uploaded to Mistral. AIDA asks before each document is
sent; enabling the workspace option does not pre-approve an upload. For an
unattended run, the upload requires explicit
`--preapprove-tool mistral_ocr_upload` approval.

If OCR is declined, unavailable, misconfigured, or times out, the request
continues with the built-in extractor. The turn does not fail, but AIDA adds
a clear note to what the model reads that OCR was not used and multi-column
figure labels may be less reliable.
