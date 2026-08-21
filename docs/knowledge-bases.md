# Knowledge bases (RAG)

> **Status: pre-alpha.** Config formats and CLI commands may change without
> notice until Phase 5. See [`PLAN.md`](../PLAN.md) for the full roadmap.

**Related:** [providers-and-secrets.md](providers-and-secrets.md) · [workspaces.md](workspaces.md)

A **knowledge base** is a named set of folders/files that AIDA chunks,
embeds, and stores in a local SQLite index so the agent can retrieve
relevant passages during a chat turn instead of relying only on what's in
its context window. Knowledge bases live in `~/.aida/knowledge.yaml`; a
workspace opts into using specific ones by name.

## Prerequisite: an embedding profile

Before you can create a knowledge base you need at least one **embedding
profile** configured in `providers.yaml` (`embedding_profiles:`) — see
[providers-and-secrets.md](providers-and-secrets.md#embedding-profiles). The
GUI's Add dialog refuses to open without one ("Configure an embedding
profile in providers.yaml (embedding_profiles:) first."), and the CLI's
`build`/`update`/`query` commands all fail with a clear message if the
knowledge base's `embedding_profile` is unset or names a profile that
doesn't exist.

## Creating a knowledge base

### CLI

```bash
aida kb add usaxs-notes \
    --source-folders "~/Documents/USAXS_notes,~/Documents/USAXS_notes/glossary.md" \
    --embedding-profile ollama-embed \
    --chunk-size 1000 \
    --chunk-overlap 150
```

- `--source-folders` — comma-separated list of folders and/or individual
  files (see [What gets indexed](#what-gets-indexed) below).
- `--embedding-profile` — name of an embedding profile from
  `providers.yaml`.
- `--chunk-size` (default `1000`) — max characters per chunk.
- `--chunk-overlap` (default `150`) — characters of trailing context carried
  into the next chunk. It must be **smaller than** `--chunk-size`: chunking
  advances by `chunk_size - chunk_overlap` characters at a time, so an equal
  or larger overlap would never make progress. `add`/`edit` refuse such a
  pair, and the GUI's overlap field is capped at the chunk size it's next
  to.

Other CLI subcommands:

```bash
aida kb list                 # names, folder count, embedding profile, chunk count
aida kb show <name>          # full field dump for one knowledge base
aida kb edit <name> [flags]  # same flags as add; an unset flag leaves that field unchanged
aida kb remove <name> [--yes] [--delete-index]
```

`remove` only deletes the entry from `knowledge.yaml` by default — its
SQLite index file is left on disk. Pass `--delete-index` to also delete
that file; without it, `--yes` skips the confirmation prompt but still
leaves the index behind.

### GUI

Open **Knowledge Bases…** from the toolbar to launch the knowledge
management dialog, then click **Add…**. The form has the same fields as the
CLI: name, source folders (one folder or file per line), an embedding
profile dropdown, chunk size, and chunk overlap. As with the CLI, Add is
blocked with a warning dialog if no embedding profile is configured yet.

A source folder pasted as a `file://` URI (e.g. Obsidian's "Copy as URI"
action) is automatically normalized to a plain path, both in the CLI's
`--source-folders` and the GUI's source-folders field — this used to fail
silently (the folder just didn't resolve, with no error), so both entry
points now fix it up before saving.

## What gets indexed

Each entry in `source_folders` can be either a whole folder (walked
recursively) or the path to one individual file — indexing "just this one
file" doesn't require making a folder for it. An Obsidian vault is just a
folder of `.md` files here; there's no separate "vault" source type.

The file extensions actually walked and indexed (`INGESTIBLE_SUFFIXES` in
`aida.knowledge.rag.ingest`) are:

```
.md  .markdown  .txt  .rst  .py  .pdf  .docx  .pptx
```

`.md`/`.markdown` files get heading-aware Markdown chunking; everything else
is chunked as plain text. Other formats `read_document` supports elsewhere
in AIDA (images, spreadsheets) are deliberately excluded here — there's
nothing for an embedding to act on in them.

## Building the index

Two ways to (re-)index a knowledge base, both in the CLI and the GUI:

- **Full rebuild** — `aida kb build <name>` (CLI) or the **Rebuild** button
  (GUI): re-chunks and re-embeds every discovered file regardless of
  whether it changed, and prunes anything indexed that's no longer
  discovered.
- **Incremental update** — `aida kb update <name>` (CLI) or the **Update**
  button (GUI): skips any file whose modification time already matches
  what's indexed, so unchanged files aren't re-embedded; new/changed files
  are (re-)ingested and files no longer found are pruned.

Both report the same counts — files added, updated, removed, and skipped
(unreadable files, recorded rather than aborting the whole pass), plus the
total chunk count written that pass. Both also flag any configured source
folder/file that doesn't currently resolve to a real, readable path (a
typo, a deleted folder, a cloud-synced placeholder mount, or an
un-normalized URI) — CLI as a `WARNING` line, GUI as a popup — instead of
silently indexing zero files with no explanation.

```bash
aida kb build usaxs-notes
aida kb update usaxs-notes
```

## Debugging retrieval

To see what passages a question would retrieve without spending a real
chat turn:

```bash
aida kb query usaxs-notes "how do I subtract dark current?" --top-k 5
```

This embeds the question, scores it against every stored chunk by cosine
similarity, and prints each result's source path, heading (if any),
similarity score, and text — the same retrieval path the agent uses during
a chat turn, run standalone.

## Attaching a knowledge base to a workspace

A workspace searches only the knowledge bases named in its
`knowledge_bases` list (see [workspaces.md](workspaces.md)). This link is
**CLI/config-file only today** — the knowledge management dialog manages
knowledge bases themselves (add/edit/remove/build/update) but has no
control for assigning one to a workspace, and the workspace GUI's editable
fields don't include `knowledge_bases` either. Set it with:

```bash
aida workspace edit usaxs-review --knowledge-bases usaxs-notes,another-kb
```

or by hand-editing the workspace's `knowledge_bases:` entry in
`workspaces.yaml`.

## What you see in chat

When a workspace's knowledge base(s) contribute passages to a turn, the
chat view shows a collapsed row: **"📚 Retrieved N passage(s) from M
knowledge base(s)"**. Clicking its **Details** button expands it to show,
per passage, the source file name, heading (if any), similarity score, and
the retrieved text — grouped by which knowledge base it came from.

## Caveat: embedding profile mismatches aren't comparable

An index is tied to the embedding profile it was built with. If you query a
knowledge base whose `embedding_profile` in `knowledge.yaml` no longer
matches the profile actually used to build its stored vectors, AIDA refuses
to compare them (`EmbeddingProfileMismatchError`) rather than returning a
meaningless similarity score — two different embedding models produce
vector spaces that aren't comparable to each other. In chat this is treated
as "no retrieval this turn" rather than a hard failure; the CLI/GUI report
it explicitly and the fix is the same either way: rebuild the knowledge
base (`aida kb build <name>`) so its index matches the profile currently
configured.
