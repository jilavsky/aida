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
      vs ChromaDB vs LlamaIndex-based
- [ ] Decision recorded here + in PLAN.md §8 (criteria: dependency weight, index
      rebuild time, retrieval quality on 10 canned questions, incremental updates)

### Embedding providers (`aida.providers.embeddings*`)

- [ ] `EmbeddingsProvider` interface + `OpenAICompatEmbeddings` (covers Ollama,
      LM Studio, OpenAI, **Argo cloud embeddings** — BeamlineAdvisor precedent:
      text-embedding-3-small via proxy)
- [ ] Embedding profiles in `providers.yaml`, secrets via keyring, same switching UX
- [ ] Guard: an index remembers which embedding profile built it; querying with a
      different profile warns/offers rebuild

### Ingestion & retrieval (`aida.knowledge.rag`)

- [ ] Source folders list per knowledge base (MD, PDF, TXT, RST, PY, DOCX — reuse
      Phase 6 readers); Obsidian vault as a first-class source type
- [ ] Chunking with heading-aware splitting for MD; metadata: source path, heading,
      mtime
- [ ] Incremental reindex by mtime; full rebuild command; index stored under
      `~/.aida/` per knowledge base
- [ ] Retrieval: top-k with score threshold → injected into system context with
      source attributions; per-workspace `knowledge_bases: [...]` key
- [ ] Answers can cite sources (path + heading) — event carries retrieval info so
      the GUI can show "used these passages"

### GUI

- [ ] Knowledge panel: list knowledge bases, add/remove source folders, embedding
      profile picker, rebuild/update buttons with progress + file counts,
      last-indexed status
- [ ] Retrieval transparency: expandable "retrieved context" row per answer
- [ ] Workspace editor gains knowledge-base selection

### CLI & tests

- [ ] `aida kb list/build/update/query` (query = retrieval-only, for debugging)
- [ ] Tests with a tiny fixture corpus + deterministic fake embedder: chunking,
      incremental update (touch a file), retrieval ranking sanity, profile-mismatch
      guard
- [ ] Manual eval: the 10 canned USAXS questions answered with correct citations

---

## Acceptance — phase is done when all are checked

- [ ] **UC1 demo:** workspace "beamline-help" with USAXS instructions indexed; ask a
      question answerable only from deep documentation → correct, cited answer;
      same question without the knowledge base visibly fails/generalizes
- [ ] Rebuild of the full corpus from the GUI with progress display; incremental
      update after editing one file takes seconds, not a rebuild
- [ ] Same knowledge base works with local embeddings and with Argo embeddings
      (rebuilt per profile; mismatch guard fires when crossed)
- [ ] Retrieval row in the GUI shows the actual passages used
- [ ] CI green (fixture corpus only, no network)

## Out of scope for this phase

RAG over the *conversation history* (future idea); automatic knowledge-base
refresh daemons; reranking models (only if the benchmark demands it).
