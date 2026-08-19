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

- [x] SQLite schema in `~/.aida/aida.db`: conversations, messages, tool calls +
      results metadata, artifact metadata (files stay on disk, not blobs),
      workspace/profile used, timestamps; schema version + migration hook
      (`aida.persistence.db`, `PRAGMA user_version` + linear `_MIGRATIONS`;
      6/6 tests incl. a v1→v1 no-op reopen test)
- [x] Write path: agent events are persisted as they stream (crash-safe enough that
      a killed session leaves a readable partial conversation). **Note:** building
      the real end-to-end acceptance test below caught a genuine bug — a turn's
      final assistant message (the one that ends it with no further tool call) was
      never persisted, because the incremental catch-up loop only ran in response
      to a *subsequent* yielded event, and a naturally-ending turn has none. Fixed
      in `aida.cli.chat.ChatSession.send()` by flushing any remaining
      un-persisted messages once the turn's event stream drains normally; a
      caller that stops consuming early (or a real process killed mid-stream)
      never reaches that flush, preserving the intended crash-safety boundary.
      Re-verified after the fix.
- [x] Resume: load a conversation and continue it (context rebuilt from stored
      messages, iteration counters reset) — `start_session(resume_conversation_id=...)`,
      unit tests in `test_start_session.py` and a full real-subprocess round trip
      in `test_phase4_acceptance.py`
- [x] Records folder (`~/Documents/Aida/`, configurable): human-readable Markdown
      transcript per conversation, exported on close/update — images linked from a
      sidecar folder (this doubles as the first Obsidian-style writer, matured in
      Phase 6)
- [x] Cleanup (`aida.persistence.cleanup`): list conversations with age/size;
      delete selected (DB rows + artifacts + record file); optional auto-cleanup
      age threshold in config (`list_conversations_older_than` — no CLI flag wires
      an actual scheduled/automatic run of it yet, only the on-demand
      `aida conversations delete`)

### Workspaces (`aida.core` + config)

- [x] `workspaces.yaml` semantics implemented: profile, source_folders,
      target_folder, sidecar_folder_name, mcp_group, skills, system_prompt,
      safety mode (safety *enforced* in Phase 6; stored/validated now)
- [x] Workspace validation on selection: folders exist/reachable (network mounts may
      be slow/absent — warn, don't crash), profile valid, group defined, skills found
- [x] Active workspace injects everything into the agent context; conversation
      records which workspace it used
- [x] Workspace CRUD from CLI (`aida workspace list/show/new/edit`) — GUI in Phase 5

### CLI

- [x] `aida chat --workspace use-pyirena` loads the full environment
- [x] `aida conversations list/resume/delete/export`

### Tests

- [x] DB roundtrip tests incl. artifacts metadata and tool calls
- [x] Resume-and-continue test with MockProvider
- [x] Migration test scaffold (v1 → v1 no-op today, machinery proven)
- [x] Workspace load/validation tests incl. missing-folder warning path
- [x] Cleanup removes exactly the selected conversation's DB rows, artifacts, record

---

## Acceptance — phase is done when all are checked

- [x] Kill `aida chat` mid-answer; `aida conversations resume` continues it sensibly
      — automated in `tests/test_phase4_acceptance.py`: a real `ChatSession` (real
      mock-mcp subprocess, real SQLite, real Markdown export) is driven through a
      tool call + image artifact, then abandoned mid-way through a second turn's
      streamed text (never draining the generator further — no `aclose()`, exactly
      what a `kill -9` gives you); `start_session(resume_conversation_id=...)` on a
      brand-new `MockProvider` then continues it, with the interrupted turn's
      in-flight text correctly absent and everything before it intact. Not a
      literal subprocess `kill -9` of a real `aida chat` process (that would only
      prove the OS can kill a process) — this exercises the actual persistence
      boundary instead, and is what caught the bug noted above.
- [x] A conversation with a pyirena-mcp plot, resumed next day, still shows/references
      the image artifact correctly — same test: after resume, the artifact row is
      still in the DB, its PNG file still exists on disk with correct bytes, and
      the tool-result message referencing it is in history. ("Next day" specifically
      — a real time gap — isn't simulated; nothing in the write path is time-sensitive.)
- [x] `~/Documents/Aida/` contains a readable MD transcript with a working image link
      — same test: the exported `.md` file's Markdown image link is resolved
      relative to the file's own location and checked against the real PNG bytes
      on disk, not just asserted to contain link-shaped text.
- [x] Two workspaces ("use-pyirena", "plain-chat") demonstrably load different
      provider/MCP/skills environments — `test_two_workspaces_load_different_provider_mcp_skills_environments`
      drives two real `start_session()` calls (one with a real mock-mcp subprocess
      enabled via its workspace's `mcp_group`, one with none) and asserts the
      resulting profiles, tool sets, and system-prompt content differ.
- [x] Deleting a conversation leaves no orphan artifacts or record files —
      `tests/test_persistence_cleanup.py`, real files on a real filesystem.
- [x] CI green — not run on GitHub Actions itself from this sandbox; the workflow
      (`.github/workflows/ci.yml`) runs exactly `pip install -e ".[dev]"`,
      `ruff check .`, `pytest -v` on ubuntu/macos/windows × Python 3.11/3.13. What
      *is* verified here: the full suite (270 tests) and `ruff check .` both pass
      cleanly in this sandbox on Python 3.11. Leaving this unchecked until the
      user's own push shows a green run, per this project's standing practice.

## Out of scope for this phase

GUI (Phase 5); safety enforcement of allowed folders (Phase 6 — until then the only
file writes are AIDA's own state/records/artifacts).
