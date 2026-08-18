# Phase 4 — Persistence, conversations & workspaces

**Goal:** Conversations survive restarts and can be resumed, browsed and cleaned up;
**named workspaces** bundle profile + folders + MCP group + skills + system prompt +
safety mode into one switchable environment.

**Prerequisites:** Phase 3.
**Unblocks:** Phase 5.
**Use cases advanced:** all, indirectly (workspaces are the delivery vehicle for
"use pyIrena" / "perform reviews" style environments).

---

## Tasks

### Persistence (`aida.persistence`)

- [ ] SQLite schema in `~/.aida/aida.db`: conversations, messages, tool calls +
      results metadata, artifact metadata (files stay on disk, not blobs),
      workspace/profile used, timestamps; schema version + migration hook
- [ ] Write path: agent events are persisted as they stream (crash-safe enough that
      a killed session leaves a readable partial conversation)
- [ ] Resume: load a conversation and continue it (context rebuilt from stored
      messages, iteration counters reset)
- [ ] Records folder (`~/Documents/Aida/`, configurable): human-readable Markdown
      transcript per conversation, exported on close/update — images linked from a
      sidecar folder (this doubles as the first Obsidian-style writer, matured in
      Phase 6)
- [ ] Cleanup (`aida.persistence.cleanup`): list conversations with age/size;
      delete selected (DB rows + artifacts + record file); optional auto-cleanup
      age threshold in config

### Workspaces (`aida.core` + config)

- [ ] `workspaces.yaml` semantics implemented: profile, source_folders,
      target_folder, sidecar_folder_name, mcp_group, skills, system_prompt,
      safety mode (safety *enforced* in Phase 6; stored/validated now)
- [ ] Workspace validation on selection: folders exist/reachable (network mounts may
      be slow/absent — warn, don't crash), profile valid, group defined, skills found
- [ ] Active workspace injects everything into the agent context; conversation
      records which workspace it used
- [ ] Workspace CRUD from CLI (`aida workspace list/show/new/edit`) — GUI in Phase 5

### CLI

- [ ] `aida chat --workspace use-pyirena` loads the full environment
- [ ] `aida conversations list/resume/delete/export`

### Tests

- [ ] DB roundtrip tests incl. artifacts metadata and tool calls
- [ ] Resume-and-continue test with MockProvider
- [ ] Migration test scaffold (v1 → v1 no-op today, machinery proven)
- [ ] Workspace load/validation tests incl. missing-folder warning path
- [ ] Cleanup removes exactly the selected conversation's DB rows, artifacts, record

---

## Acceptance — phase is done when all are checked

- [ ] Kill `aida chat` mid-answer; `aida conversations resume` continues it sensibly
- [ ] A conversation with a pyirena-mcp plot, resumed next day, still shows/references
      the image artifact correctly
- [ ] `~/Documents/Aida/` contains a readable MD transcript with a working image link
- [ ] Two workspaces ("use-pyirena", "plain-chat") demonstrably load different
      provider/MCP/skills environments
- [ ] Deleting a conversation leaves no orphan artifacts or record files
- [ ] CI green

## Out of scope for this phase

GUI (Phase 5); safety enforcement of allowed folders (Phase 6 — until then the only
file writes are AIDA's own state/records/artifacts).
