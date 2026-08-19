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

- [x] `ImageArtifact(data: bytes, mime_type, filename=None)`,
      `TextArtifact`, `FileArtifact(path, mime_type)`, `JsonArtifact`,
      `TableArtifact` (columns/rows) — `aida/artifacts/base.py`, 19 tests
      in `tests/test_artifacts.py`
- [x] MCP content → artifact conversion: `ImageContent` base64-decoded immediately;
      never flattened into the text channel — `aida/mcp/results.py`, built and
      tested against real `mcp.types` objects (`TextContent`, `ImageContent`,
      `AudioContent`, `ResourceLink`, `EmbeddedResource`), 10 tests in
      `tests/test_mcp_results.py`
- [x] Artifact store: binaries written under `~/.aida/artifacts/`, metadata kept for
      Phase 4 DB; helper to also save a copy into a target folder —
      `aida/artifacts/store.py`
- [x] What the LLM sees for each artifact type (image → brief text description +
      availability note; table/JSON → structured text within size cap) — explicit,
      tested policy per type — `aida/artifacts/policy.py`

### MCP manager (`aida.mcp`)

- [x] Official `mcp` Python SDK, stdio transport; pyproject.toml now pins
      `mcp>=1.0,<2.0` (was unpinned upper bound from Phase 1). **Not fully
      verified:** confirmed this range installs cleanly and every MCP test
      passes with the currently-resolved `mcp==1.29.0` in this sandbox, but
      actual side-by-side coexistence with `pyirena[mcp]` in one real env
      couldn't be checked here (pyirena isn't installed in this sandbox) —
      please verify on your machine with both installed together.
- [x] Load standard-style `mcp.json` (portable from Claude Desktop configs),
      including per-server `env` vars (e.g. `PYIRENA_DATA_ROOT`) —
      `aida/config/settings.py` (`McpConfig`/`McpServerConfig`, from Phase 1
      scaffold); `test_existing_claude_desktop_mcp_json_loads_unmodified`
      loads a raw Claude-Desktop-shaped `mcp.json` (no `groups`/`skills`
      keys, extra unknown keys) and confirms it loads without modification
- [x] Launch/initialize/stop/restart stdio servers; tool discovery; expose tool
      schemas to the provider layer namespaced as `server.tool` —
      `aida/mcp/server.py` (`McpServerHandle`) + `aida/mcp/manager.py`
      (`McpManager`); 16 tests in `tests/test_mcp_server.py`, 8 in
      `tests/test_mcp_manager.py`, all against a real subprocess
- [x] Execute tool calls; preserve multi-part results (text + image in one result) —
      verified end to end (`test_get_multi_part_preserves_text_and_image_in_order`,
      `test_keystone_mcp_image_round_trip`)
- [x] Capture server stderr/logs per server; timing, payload sizes, MIME types and
      status recorded for every call (diagnostics-as-a-feature) — `StderrCapture`
      (real-fd-backed; the naive write()/flush()-only approach doesn't work —
      `subprocess.Popen(stderr=...)` needs a real fd, see the class docstring)
      and `ToolCallRecord`; `test_stderr_capture_records_real_subprocess_output`
      confirms real FastMCP request-logging is actually captured, not a no-op
- [x] Failure isolation: a crashed/hung server errors that one tool call with a
      clear layer-naming message; agent loop continues — `McpServerError`;
      `test_hang_forever_times_out_and_is_isolated` and
      `test_restart_after_real_process_crash_recovers` use a real hard
      `os._exit()` crash, not a simulated one
- [x] Per-call timeout (config) — `McpServerHandle(call_timeout_seconds=...)`,
      default 60s, via `asyncio.wait_for`

### Groups & skills linkage

- [x] `groups`: named server sets in config; enabling a group starts/exposes exactly
      those servers' tools (rationale: pyIrena's tool list overloads small local
      models when not needed) — `aida/mcp/groups.py` (`resolve_group`), 9 tests
      in `tests/test_mcp_groups.py`
- [x] Per-server `skills: [...]` key: enabling a server auto-includes those skills
      files (from `~/.aida/skills/`) in the system context —
      `McpManager.skills()` (dedup, config order), merged into
      `ChatSession`'s skill list in `aida/cli/chat.py`'s `_async_main`;
      unit-tested (`test_skills_deduplicated_across_servers`) — not yet
      exercised through a full `aida chat` subprocess with a real skill file
      on disk (the flag wiring itself was, see below)
- [x] CLI flags: `aida chat --mcp-group pyirena-analysis` and `--mcp server1,server2` —
      `aida/cli/chat.py` (`resolve_mcp_servers`, `_build_parser`); explicit
      `--mcp` wins over `--mcp-group` if both given. Verified two ways: unit
      tests (`test_chat_cli.py`) and a real subprocess run of
      `python -m aida.cli chat --mcp mock-mcp` against the real mock server
      (printed `[mcp] mock-mcp: 6 tool(s)`, clean exit, no leftover process)
- [x] Only enabled servers are launched (lazy start) — no resource waste —
      `McpManager` only ever constructs handles for the servers passed to it;
      `test_enabled_server_names_reflects_construction_not_start`

### Test infrastructure

- [x] `mock-mcp` fixture server (tiny stdio server in tests/): tools returning text,
      a small PNG, JSON, a multi-part result, an error, and a deliberate hang —
      `tests/mock_mcp_server.py`, plus `crash_process` (hard `os._exit()`) added
      for the restart-after-crash test
- [x] Integration tests: discovery, execution, typed conversion for every content
      type, timeout handling, restart-after-crash — all against the real
      subprocess above (44 tests total across `test_mcp_server.py`,
      `test_mcp_manager.py`, `test_mcp_results.py`,
      `test_keystone_image_roundtrip.py`)
- [x] Groups/skills-linkage tests (which tools exposed, which skills in context) —
      `test_mcp_groups.py`, `test_skills_deduplicated_across_servers`

---

## Acceptance — phase is done when all are checked

- [x] **Keystone test (automated, mock-mcp):** agent loop with `MockProvider`
      requests the image tool → PNG arrives as `ImageArtifact`, valid decodable
      bytes, saved to artifacts dir, `ImageArtifactCreated` event emitted, and the
      LLM receives the text-policy representation — all asserted —
      `tests/test_keystone_image_roundtrip.py`, real subprocess MCP server +
      real `ArtifactStore` + real `AgentLoop`/`McpManager`, scripted
      `MockProvider` standing in for the LLM only (per PLAN.md §7)
- [ ] **Keystone test (manual, real):** `aida chat` with a real model + real
      `pyirena-mcp`: "plot dataset X" → PNG saved to disk and path printed; works
      with (a) a local model and (b) Argo Claude — **needs you**: requires a
      real pyirena-mcp install and a real model endpoint, neither available in
      this sandbox. The CLI flags/plumbing this needs (`--mcp`/`--mcp-group`)
      are built and verified against a real subprocess above.
- [ ] bait_mcp connects and lists its tools from AIDA (no instrument needed —
      discovery only) — **needs you**: requires the real bait_mcp package,
      not available in this sandbox. `McpServerHandle`/`McpManager` discovery
      itself is fully tested against a real (mock) MCP server, so this should
      be a config-only check (`aida chat --mcp bait`) once bait_mcp is
      installed on your machine.
- [x] An existing Claude Desktop `mcp.json` entry for pyirena works unmodified —
      `test_existing_claude_desktop_mcp_json_loads_unmodified`
- [ ] Switching MCP groups changes the tool list the model sees (verify via a
      "what tools do you have" question to a real model) — **needs you**:
      requires a real model. Group resolution itself (`resolve_group`) and the
      resulting tool-set change are both unit-tested; only the "ask a real
      model" step needs a live endpoint.
- [ ] CI green — **needs you**: this sandbox can't push to GitHub or observe
      Actions runs (see PLAN.md's standing note on this). `ruff check` and the
      full `pytest` suite (162 tests) are clean here; push and confirm.

## Out of scope for this phase

Rendering images in a GUI (Phase 5); MCP management UI (Phase 7); HTTP/remote MCP
transport (future); native workspace file tools (Phase 6).

## Notes

Built 2026-08-18, same session as Phases 1-2. One real bug was found and fixed
during development, caught by actually running the new code against a real
subprocess (not by the unit tests, which were all passing at the time):

1. **`stdio_client()`'s `errlog` argument needs a real OS file descriptor, not a
   plain Python object with `write()`/`flush()`.** The first version of
   `StderrCapture` implemented only the informal "text sink" protocol
   (`write`/`flush`), which is what `TextIO` usually means — but `stdio_client()`
   hands `errlog` straight to `subprocess.Popen(stderr=...)` (via
   `anyio.open_process`), and `Popen` requires either a real fd, an int, or one
   of `PIPE`/`DEVNULL`/`STDOUT` — not an arbitrary write()-able object. This
   failed immediately and loudly (`AttributeError: 'StderrCapture' object has no
   attribute 'fileno'`) the first time an integration test actually launched a
   subprocess through it — a plain unit test with a fake `ClientSession` would
   never have caught this. Fixed by backing `StderrCapture` with a real
   `tempfile.TemporaryFile` and exposing `fileno()`; `tail()` reads back from the
   file. `test_stderr_capture_records_real_subprocess_output` locks this in by
   asserting real FastMCP request-log lines actually show up in `tail()`.

Also worth recording for later phases: FastMCP's `structuredContent` behavior
turned out to differ by tool return type in ways not worth hard-coding
around — a `str`-returning tool got an auto-wrapped `{"result": ...}`
`structuredContent`, while the exploration notes for a `dict`-returning tool
(from before this file's tests were written) found it went out as `TextContent`
JSON instead. `aida/mcp/results.py` doesn't special-case this: it converts
every content block *and* appends a `JsonArtifact` whenever `structuredContent`
is present, whatever it is. Tests here assert on the artifacts that must be
present rather than pinning FastMCP's SDK-internal choice of how many artifacts
a given return type produces.

The same real-subprocess-integration-test technique that caught both Phase 2
bugs caught this one too — every test file in this phase that touches
`McpServerHandle` (`test_mcp_server.py`, `test_mcp_manager.py`,
`test_keystone_image_roundtrip.py`) launches the actual
`tests/mock_mcp_server.py` subprocess rather than mocking `ClientSession`,
specifically so bugs like this surface here instead of in your hands.
