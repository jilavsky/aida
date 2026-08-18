# Phase 7 — MCP management & permissions UI

**Goal:** Everything about MCP servers is manageable from the GUI: add/remove/edit
servers, build groups, link skills, per-tool permissions, live logs and a raw
result inspector for debugging. This is where AIDA becomes self-service for users
who will never edit `mcp.json` by hand.

**Prerequisites:** Phase 5 (uses Phase 3 backend). Can run in parallel with Phase 6.
**Use cases advanced:** UC3, UC4 (robust, debuggable MCP use).

---

## Tasks

### Server management panel

- [ ] List configured servers: status (stopped/starting/running/error), tool count,
      group membership, linked skills
- [ ] Add/edit server dialog: command, args, env vars (values maskable), transport
      (stdio), linked skills (pick from skills folder), group membership
- [ ] Import from existing standard `mcp.json` (paste or file-pick a Claude
      Desktop-style config; merge without clobbering)
- [ ] Start / stop / restart per server; auto-start-on-enable stays lazy
- [ ] Delete server with confirmation

### Groups editor

- [ ] Create/rename/delete groups; drag or checkbox servers into groups
- [ ] Set workspace default group from here; quick "active group" switcher stays in
      the main toolbar (from Phase 5)
- [ ] Show estimated tool count per group (reminder why lean groups matter for
      small local models)

### Skills management

- [ ] Skills browser over `~/.aida/skills/`: list, preview (rendered MD), open in
      external editor, new-from-template
- [ ] Per-server skills linkage editable here and in the server dialog
- [ ] Per-workspace extra skills selection (beyond server-linked ones)

### Per-tool permissions

- [ ] Tool list per server with enable/disable checkbox (disabled tools not exposed
      to the LLM)
- [ ] Optional per-tool "confirm before run" flag (for e.g. bait_mcp instrument
      writes even inside relaxed workspaces)

### Diagnostics

- [ ] Live log panel: per-server stderr + AIDA's per-call records (server, tool,
      args summary, duration, result content types, sizes, status), filterable
- [ ] **Raw result inspector**: click any tool call in the chat or log → exact MCP
      response (JSON, base64 lengths noted), copyable — the debugging feature the
      off-the-shelf apps lack
- [ ] "Test connection" button per server: initialize + list tools, report timing

### Tests

- [ ] Config roundtrip: GUI edits → `mcp.json` (+aida extras) → reload identical
- [ ] Import test with a real-world Claude Desktop config sample
- [ ] Per-tool disable respected in tool schemas sent to provider (unit test)
- [ ] Confirm-flagged tool triggers a confirmation event (MockProvider test)

---

## Acceptance — phase is done when all are checked

- [ ] Add pyirena-mcp from scratch entirely in the GUI (command path, env vars,
      skills link, group) and run a successful plot call — no manual file editing
- [ ] Import an existing Claude Desktop `mcp.json`; servers appear and work
- [ ] Break a server on purpose (bad path): status shows error, log panel shows
      why, chat degrades gracefully
- [ ] Disable one pyirena tool; verify the model no longer sees it
- [ ] Mark a bait_mcp write-tool "confirm before run"; confirmation appears even in
      a relaxed workspace
- [ ] Raw inspector shows the exact ImageContent response for a plot call
- [ ] CI green

## Out of scope for this phase

Remote/HTTP MCP transport (future); an MCP "marketplace" (never); skills *editing*
beyond open-in-external-editor (plain MD files are the editor story).
