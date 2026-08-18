# Phase 3 — MCP client, typed artifacts, groups & skills linkage

**Goal:** First-class MCP support with **typed results** — the keystone of the whole
project. A model calls pyirena-mcp, a PNG comes back, and AIDA receives it as a real
image object, not garbled text. Plus MCP groups and per-server skills attachment.

**Prerequisites:** Phase 2.
**Unblocks:** Phase 4 (and everything after).
**Use cases advanced:** UC3 (headless form).

---

## Tasks

### Typed artifacts (`aida.artifacts`)

- [ ] `ImageArtifact(data: bytes, mime_type, filename=None)`,
      `TextArtifact`, `FileArtifact(path, mime_type)`, `JsonArtifact`,
      `TableArtifact` (columns/rows)
- [ ] MCP content → artifact conversion: `ImageContent` base64-decoded immediately;
      never flattened into the text channel
- [ ] Artifact store: binaries written under `~/.aida/artifacts/`, metadata kept for
      Phase 4 DB; helper to also save a copy into a target folder
- [ ] What the LLM sees for each artifact type (image → brief text description +
      availability note; table/JSON → structured text within size cap) — explicit,
      tested policy per type

### MCP manager (`aida.mcp`)

- [ ] Official `mcp` Python SDK, stdio transport; pick a version range that
      coexists with `pyirena[mcp]` (`mcp>=1.0,<2.0`) in one env — verify and record
- [ ] Load standard-style `mcp.json` (portable from Claude Desktop configs),
      including per-server `env` vars (e.g. `PYIRENA_DATA_ROOT`)
- [ ] Launch/initialize/stop/restart stdio servers; tool discovery; expose tool
      schemas to the provider layer namespaced as `server.tool`
- [ ] Execute tool calls; preserve multi-part results (text + image in one result)
- [ ] Capture server stderr/logs per server; timing, payload sizes, MIME types and
      status recorded for every call (diagnostics-as-a-feature)
- [ ] Failure isolation: a crashed/hung server errors that one tool call with a
      clear layer-naming message; agent loop continues
- [ ] Per-call timeout (config)

### Groups & skills linkage

- [ ] `groups`: named server sets in config; enabling a group starts/exposes exactly
      those servers' tools (rationale: pyIrena's tool list overloads small local
      models when not needed)
- [ ] Per-server `skills: [...]` key: enabling a server auto-includes those skills
      files (from `~/.aida/skills/`) in the system context
- [ ] CLI flags: `aida chat --mcp-group pyirena-analysis` and `--mcp server1,server2`
- [ ] Only enabled servers are launched (lazy start) — no resource waste

### Test infrastructure

- [ ] `mock-mcp` fixture server (tiny stdio server in tests/): tools returning text,
      a small PNG, JSON, a multi-part result, an error, and a deliberate hang
- [ ] Integration tests: discovery, execution, typed conversion for every content
      type, timeout handling, restart-after-crash
- [ ] Groups/skills-linkage tests (which tools exposed, which skills in context)

---

## Acceptance — phase is done when all are checked

- [ ] **Keystone test (automated, mock-mcp):** agent loop with `MockProvider`
      requests the image tool → PNG arrives as `ImageArtifact`, valid decodable
      bytes, saved to artifacts dir, `ImageArtifactCreated` event emitted, and the
      LLM receives the text-policy representation — all asserted
- [ ] **Keystone test (manual, real):** `aida chat` with a real model + real
      `pyirena-mcp`: "plot dataset X" → PNG saved to disk and path printed; works
      with (a) a local model and (b) Argo Claude
- [ ] bait_mcp connects and lists its tools from AIDA (no instrument needed —
      discovery only)
- [ ] An existing Claude Desktop `mcp.json` entry for pyirena works unmodified
- [ ] Switching MCP groups changes the tool list the model sees (verify via a
      "what tools do you have" question to a real model)
- [ ] CI green

## Out of scope for this phase

Rendering images in a GUI (Phase 5); MCP management UI (Phase 7); HTTP/remote MCP
transport (future); native workspace file tools (Phase 6).
