# Phase 8 — RAG over documentation

**Goal:** UC1 in full: answers grounded in larger documentation collections
(instrument docs, Obsidian vaults, manuals) via retrieval — with **embedding
providers configured exactly like LLM providers** (local Ollama or cloud/Argo).
Direct-context skills files remain the tool for small, fast-changing docs
(BeamlineAdvisor's two-tier document strategy).

**Prerequisites:** Phase 5 (GUI) + Phase 2 (provider profiles). Independent of 6/7.
**Use cases advanced:** UC1 (full).

---

## Tasks

### Decide the index backend (first task, timeboxed)

- [ ] Benchmark on a real corpus (USAXS instructions + a pyIrena docs folder +
      one Obsidian vault): minimal custom pipeline (chunk → embed → `sqlite-vec`)
      vs ChromaDB vs LlamaIndex-based — not run literally in this sandbox (no real
      embeddings API access, no real USAXS/Obsidian corpus here); same
      manual/real-server, out-of-sandbox-scope limitation every prior phase's
      real-data acceptance items already carry.
- [x] Decision recorded here + in PLAN.md §8 (criteria: dependency weight, index
      rebuild time, retrieval quality on 10 canned questions, incremental updates).
      **Decided without the benchmark, on a reasoned corpus-size analysis
      instead:** plain SQLite (own schema, own file per knowledge base,
      `aida.knowledge.rag.index`) + pure-Python cosine similarity
      (`aida.knowledge.rag.retrieval`) — no vector DB. Realistic corpora here
      (instrument docs, one Obsidian vault, a docs folder) are hundreds to low
      thousands of chunks; brute-force ranking over in-memory vectors is tens of
      milliseconds at that scale, and this avoids a second persistence engine
      alongside AIDA's own SQLite story — the same "no framework, no
      unnecessary weight" reasoning that ruled out LangChain for the agent loop.
      The `chromadb` stub is removed from `pyproject.toml`'s `rag` extra (no
      replacement needed — ingestion reuses the `docs` extra's readers, and
      embeddings reuse the `openai` SDK already a core dependency for
      `OpenAICompatProvider`).

### Embedding providers (`aida.providers.embeddings*`)

- [x] `EmbeddingsProvider` interface (`embeddings_base.py`) + `OpenAICompatEmbeddings`
      (`openai_compat_embeddings.py`, covers Ollama, LM Studio, OpenAI, **Argo cloud
      embeddings** — BeamlineAdvisor precedent: text-embedding-3-small via proxy)
- [x] Embedding profiles (`EmbeddingProfile`) in `providers.yaml` (new
      `embedding_profiles:` key alongside the existing `profiles:`), secrets via
      keyring (`secret_ref`, same as `ProviderProfile`), same switching UX
      (`build_embeddings_provider`/`validate_embedding_profile` mirror
      `build_provider`/`validate_profile` exactly)
- [x] Guard: `aida.knowledge.rag.index`'s `meta` table remembers which embedding
      profile last built the index; `retrieve()` raises
      `EmbeddingProfileMismatchError` on a mismatch — `ChatSession._retrieve_context`
      treats this as "no retrieval this turn" (logged, not a crash); the CLI/GUI
      surface it as an actionable message ("rebuild this knowledge base...")

### Ingestion & retrieval (`aida.knowledge.rag`)

- [x] Source folders list per knowledge base (`KnowledgeBaseConfig.source_folders`;
      MD, PDF, TXT, RST, PY, DOCX/PPTX — reuses Phase 6's `read_document`, see
      `ingest.py`'s `INGESTIBLE_SUFFIXES`); an Obsidian vault is just a folder of
      `.md` files, no separate "vault" source type needed — heading-aware MD
      chunking already handles that structure
- [x] Chunking with heading-aware splitting for MD (`chunking.chunk_markdown`,
      falls back to `chunk_plain_text` when no headings are found); metadata per
      chunk: source path, heading, chunk index, mtime
- [x] Incremental reindex by mtime (`ingest.update`, skips a file whose mtime
      matches what's indexed); full rebuild command (`ingest.rebuild`, force
      re-ingest + prune anything no longer discovered); index stored under
      `~/.aida/knowledge/<kb_name>.db`, one file per knowledge base
- [x] Retrieval: top-k with score threshold → injected into context per turn
      (`ChatSession.send()`'s ephemeral context-message pattern — see the
      "Per-turn retrieval injection" design note below); per-workspace
      `knowledge_bases: [...]` key (`WorkspaceConfig.knowledge_bases`, resolved
      into `ActiveKnowledgeBase`s once at `start_session`)
- [x] Answers can cite sources (path + heading) — `RetrievalPerformed` event
      (`aida.core.events`) carries `passages_by_kb` (plain dicts: text, source_path,
      heading, score) so the GUI can show "used these passages"

### GUI

- [x] Knowledge panel (`KnowledgeManagementDialog`): list knowledge bases +
      chunk counts, add/edit/remove (`KnowledgeBaseFormDialog`: source folders,
      embedding profile picker, chunk size/overlap), Rebuild/Update buttons
      driven through `ChatBridge` (background asyncio loop, never blocks the Qt
      thread) with a status label showing added/updated/removed/skipped counts
- [x] Retrieval transparency: `RetrievalRow` (`retrieval_widget.py`) — collapsed
      "📚 Retrieved N passage(s) from M knowledge base(s)", expandable to every
      passage's source/heading/score/text — same interaction idiom as
      `ToolCallRow`; wired into `ChatPanel.handle_event`
- [x] Workspace editor gains knowledge-base selection — CLI-only v1 surface
      (`aida workspace new/edit --knowledge-bases a,b`, mirrors the existing
      `--skills` flag exactly), consistent with the documented pre-existing gap
      ("no GUI 'new workspace' form exists yet") flagged and deliberately left
      alone earlier in this project

### CLI & tests

- [x] `aida kb list/show/add/edit/remove/build/update/query` (query =
      retrieval-only, for debugging — embeds a question, prints top-k passages +
      scores, no LLM call) — `aida/cli/kb_cmds.py`, registered in `__main__.py`
- [x] Tests with a tiny fixture corpus + deterministic fake embedder
      (`MockEmbeddings`, a SHA256 hashing-trick bag-of-words embedder — no
      ML/network): chunking, incremental update (touch a file), retrieval
      ranking sanity, profile-mismatch guard — `test_chunking.py`,
      `test_knowledge_index.py`, `test_knowledge_ingest.py`,
      `test_knowledge_retrieval.py`, `test_kb_cmds.py`, plus retrieval-injection
      tests in `test_chat_cli.py`/`test_start_session.py` and GUI tests in
      `tests/ui/test_knowledge_management_dialog.py`/`test_chat_panel.py`
- [ ] Manual eval: the 10 canned USAXS questions answered with correct citations
      — needs a real embeddings endpoint (Ollama or Argo) and the real USAXS
      corpus; out of this sandbox's scope, same as every prior phase's
      real-model acceptance items

---

## Acceptance — phase is done when all are checked

- [ ] **UC1 demo:** workspace "beamline-help" with USAXS instructions indexed; ask a
      question answerable only from deep documentation → correct, cited answer;
      same question without the knowledge base visibly fails/generalizes — needs
      a real corpus + real embeddings endpoint; out of sandbox scope (see above).
      The mechanism itself is proven end-to-end with a fixture corpus + the fake
      embedder (`tests/ui/test_knowledge_management_dialog.py::
      test_full_workflow_rebuild_then_chat_turn_shows_retrieval_row`: rebuild via
      the GUI, send a chat turn, `RetrievalRow` appears with the retrieved
      passage).
- [x] Rebuild of the full corpus from the GUI with progress display; incremental
      update after editing one file takes seconds, not a rebuild (`ingest.update`
      skips unchanged files by mtime — verified in `test_knowledge_ingest.py`)
- [ ] Same knowledge base works with local embeddings and with Argo embeddings
      (rebuilt per profile; mismatch guard fires when crossed) — the mismatch
      guard itself is unit-tested (`test_retrieve_raises_on_embedding_profile_
      mismatch`); running the *same* knowledge base against two real embedding
      backends needs real network access, out of sandbox scope
- [x] Retrieval row in the GUI shows the actual passages used (`RetrievalRow`,
      `test_retrieval_performed_renders_a_retrieval_row`)
- [ ] CI green (fixture corpus only, no network) — this environment has no CI
      pipeline to report on directly; the fixture-corpus/fake-embedder test suite
      itself is green (`pytest -q`: 769 passed, `ruff check .` clean at delivery)

## Out of scope for this phase

RAG over the *conversation history* (future idea); automatic knowledge-base
refresh daemons; reranking models (only if the benchmark demands it).

---

## Implementation notes (backend → CLI → GUI, in build order)

Built full-stack per the approved plan, each layer tested before the next:

- **Backend** (Qt-free, unit-tested): `EmbeddingProfile`/`KnowledgeBaseConfig`/
  `KnowledgeConfig` + `load_knowledge_config`/`save_knowledge_config`
  (`aida/config/settings.py`, new `~/.aida/knowledge.yaml`);
  `EmbeddingsProvider`/`OpenAICompatEmbeddings`/`MockEmbeddings`
  (`aida/providers/`); `build_embeddings_provider`/`validate_embedding_profile`
  (`aida/providers/profiles.py`); the whole `aida/knowledge/rag/` subpackage
  (`chunking.py`, `index.py`, `ingest.py`, `retrieval.py`); the new
  `RetrievalPerformed` event (`aida/core/events.py`).
- **Session wiring** (the architecturally trickiest part — see below):
  `aida.cli.chat.ChatSession` gained `active_knowledge_bases`,
  `_retrieve_context`, `_persist_new_messages`, and a rewritten `send()`;
  `start_session` resolves a workspace's `knowledge_bases` names into
  `ActiveKnowledgeBase`s (warn-and-skip on any misconfiguration — unknown KB
  name, missing/unknown embedding profile, unbuildable provider kind).
- **CLI**: `aida kb {list,show,add,edit,remove,build,update,query}`
  (`aida/cli/kb_cmds.py`, mirrors `mcp_cmds.py`'s exact pattern); `aida
  workspace new/edit` gained `--knowledge-bases` (mirrors `--skills`).
- **GUI**: `KnowledgeManagementDialog` + `KnowledgeBaseFormDialog`
  (`aida/ui/qt/knowledge_management_dialog.py`), opened from a new "Knowledge
  Bases…" toolbar action in `MainWindow`; `RetrievalRow`
  (`aida/ui/qt/retrieval_widget.py`) wired into `ChatPanel.handle_event`;
  `ChatBridge` gained `rebuild_knowledge_base`/`update_knowledge_base` +
  `kb_ingest_finished`/`kb_ingest_failed` signals, scheduled on the background
  loop exactly like the existing MCP live-control actions — a real embedding
  pass never blocks the Qt thread.

### Per-turn retrieval injection into chat (design note)

The one genuinely new architectural wrinkle: retrieval is query-dependent
(needs the user's actual question), but `start_session`'s existing
`extra_context_texts` mechanism (built for Phase 7's folder-facts/MCP-
instructions injection) runs once, at session construction, before any user
message exists — it can't be reused for retrieval. Resolved by keeping
`ChatSession.messages` as the one canonical mutable list `AgentLoop.run()`
mutates in place and the recorder watches grow (an existing, load-bearing
invariant) and treating the retrieved-context message as strictly ephemeral:
appended to `self.messages` for exactly one turn (so the model sees it),
excluded from persistence by identity check, and removed again in a `finally`
block once the turn ends — so it never reaches the DB and never accumulates
into a later turn with stale passages.

**Verification:** 769 tests passing (`pytest -q`, up from 721 at the start of
this phase), `ruff check .` clean, in this environment.

---

## Post-delivery bug fixes (real Obsidian vault + LM Studio usage)

The user configured a real knowledge base — an Obsidian vault synced via
iCloud Drive, LM Studio as a local embedding server — and hit "Rebuild"
against it. Result: "added 0, updated 0, removed 0 (0 chunks written this
pass)", no error anywhere, no server activity at all (confirmed against both
the local LM Studio profile and an Argo embedding profile — ruling out a
provider-specific problem). Diagnosed by reading the user's actual
`~/.aida/knowledge.yaml` rather than guessing.

1. **Root cause: a `file://` URI in `source_folders`, not a plain path.**
   `knowledge.yaml` held
   `file:///Users/.../iCloud~md~obsidian/Documents/.../USAXS notes/` — Obsidian's
   (and many file managers') "Copy as URI"/"Copy Path" action produces a URI,
   not a filesystem path, and `Path("file:///Users/...")` doesn't raise or
   warn — it just isn't a directory, so `_discover_files` silently skipped
   the whole folder. Every code path (GUI rebuild, `aida kb build`) discovers
   files the same way, so this affected both. Fixed with a new
   `aida.knowledge.rag.ingest.normalize_source_folder` (strips a `file://`
   scheme, percent-decodes) applied: (a) at ingest time in `_discover_files`,
   so an *already-saved* `file://` path starts working on the very next
   rebuild with no re-entry needed; (b) at save time in
   `KnowledgeBaseFormDialog.result_config()` and `aida kb add/edit`, so newly
   saved configs store a clean path instead of a URI going forward.
2. **Silent failure was the bigger problem than the URI parsing itself.** A
   folder that's missing, mistyped, or (found while verifying the fix)
   *unreadable* — a cloud-synced folder (iCloud Drive/OneDrive placeholder
   mounts are the common case) can pass `Path.is_dir()` while still raising
   `PermissionError` the moment something actually tries to list it — used to
   produce the exact same "added 0" with zero indication why, regardless of
   the URI bug. `IngestResult` gained `missing_folders: list[str]`, populated
   by a new `_folder_is_usable()` check (exists *and* actually listable, not
   just `is_dir()`) run before every build/update pass and surfaced as: a
   `WARNING —` block in `aida kb build/update`'s output, and a
   `QMessageBox.warning` in `KnowledgeManagementDialog` alongside the
   existing added/updated/removed counts in the status line.
3. Regression tests added at every layer this touched:
   `test_knowledge_ingest.py` (URI stripping, percent-decoding, a
   `file://`-configured folder that now ingests correctly, a genuinely
   missing folder reported via `missing_folders`, and a chmod-simulated
   unreadable folder), `test_kb_cmds.py` (`add` normalizes a `file://` folder,
   `build` prints the warning), `tests/ui/test_knowledge_management_dialog.py`
   (the form normalizes a pasted `file://` folder, rebuild against a missing
   folder pops the warning dialog).
