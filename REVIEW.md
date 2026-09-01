# AIDA repository review

**Reviewed:** 2026-08-31  
**Commit:** `3711bde8fdd48dd9e5690a0b69b286aab60cac5e` (`Add workspace note`)  
**Scope:** architecture, agent/provider loop, MCP integration, workspaces and safety, persistence, RAG, document/file tools, scripting, Qt lifecycle, tests, documentation, and lightweight product opportunities.

I interpret “other Macs” in the review request as **other MCP servers**.

## Bottom line

AIDA is a strong beta, not a prototype held together by a single happy path. The core design is unusually clear for a desktop agent harness: the provider abstraction is small, the agent owns tool execution, MCP results become typed artifacts, the non-UI core does not depend on Qt, state is persisted incrementally, and risky native actions pass through one safety boundary. The dependency set is also appropriately restrained.

The main risks are now at **boundaries and lifecycle transitions**, not in the basic chat/tool path. Before adding much more capability, I would fix four areas:

1. Invalid safety configuration currently fails open.
2. Profile switching and manual compaction can mutate a session while a turn is running.
3. filesystem timeouts can report failure while the underlying operation continues.
4. a temporarily unavailable RAG source can be interpreted as deletion and removed from the index.

Those are more important than Phase 10 or another large integration. After them, AIDA looks ready for the real-machine beta verification already listed in `PLAN.md`.

## Verification performed

- `ruff check .`: **passed**.
- Test collection: **1,450 tests**.
- Non-GUI tests: **1,003 passed**. Six localhost web-tool tests could not bind a socket in the restricted review sandbox; rerunning that seven-test module with local socket access produced **7 passed**.
- GUI tests: **447 passed**.
- GUI tests emitted two warnings worth watching: a third-party `pydantic_settings` incomplete-forward-reference warning and an unraisable `BaseSubprocessTransport.__del__` warning after the event loop was closed in `tests/ui/test_code_editor_dialog.py::test_done_disconnects_bridge_signals`.
- `ruff format --check .`: **128 files would be reformatted**. Formatting is not currently enforced by CI; this is maintenance debt, not a correctness failure.
- No live provider, real pyIrena server, real Playwright login, network-mounted scientific data, or native GUI session was exercised. Those remain necessary end-to-end checks.

## What is working particularly well

- **Good ownership boundaries.** Providers translate and stream; `AgentLoop` executes tools; frontends consume plain events. The Qt-contract tests reinforce this instead of leaving it as an architectural aspiration.
- **Typed MCP artifacts are the right choice.** Decoding image content immediately and persisting it before the event reaches the UI avoids the common “base64 hidden in a text blob” failure.
- **MCP failure isolation and namespacing are thoughtful.** Servers start concurrently, a failed server does not normally prevent a session, server instructions reach the model, and per-tool confirmation is independent of filesystem safety mode.
- **The safety model has a small conceptual surface.** Resolved paths, allowlisted roots, recoverable deletion, command allowlists, and no `shell=True` are all good decisions.
- **Persistence is pragmatic.** Per-message SQLite writes plus a derived Markdown transcript offer useful crash recovery without turning the app into an event-sourcing framework.
- **Context management is visible.** Tool-schema cost, profile context windows, compaction, and user-facing fullness indicators matter especially for smaller local models and large pyIrena schemas.
- **The test suite is broad and fast.** It covers provider translations, a real stdio mock MCP subprocess, persistence repair, safety, GUI bridging, and three operating systems in CI.
- **The project stays relatively light.** Most heavy document/UI dependencies remain extras, and capabilities are composed from MCP servers rather than imported into AIDA.

## Findings

Priority meanings used below:

- **P1:** fix before widening the beta; can weaken a safety guarantee, corrupt live state, or misreport a mutating operation.
- **P2:** fix in the next beta; meaningful correctness or reliability problem with a narrower trigger.
- **P3:** maintenance, documentation, or test-quality issue.

### P1 — Invalid safety modes silently behave like relaxed mode

**Evidence**

- [`validate_workspace`](src/aida/workspace/workspaces.py#L117) records an unknown safety value only as a warning and still returns `ok=True`.
- [`start_session`](src/aida/core/session.py#L748) passes the value straight to `SafetyGuard`.
- In-bounds writes/deletes are confirmed only when [`self.mode == "confirm"`](src/aida/workspace/safety.py#L169), and allowlisted commands use the same exact comparison at [`authorize_execute`](src/aida/workspace/safety.py#L243).

I reproduced this with `mode="confrim"`: an in-bounds write was authorized with **zero confirmation calls**. A typo therefore acts like relaxed mode even though validation warns that it is unknown. The global `default_safety_mode` has the same risk.

**Recommendation:** validate both global and workspace modes at load time and fail closed. As a second line of defense, have `SafetyGuard` normalize any value other than the explicit `"relaxed"` value to `"confirm"`, or raise before it can authorize anything. Add tests for invalid global and workspace modes covering write, delete, script execution, and allowlisted command execution.

### P1 — Profile switching is neither atomic nor serialized with a running turn

**Evidence**

- The profile selector remains connected directly to `bridge.switch_profile` ([`main_window.py`](src/aida/ui/qt/main_window.py#L326)); `_on_turn_started` disables only the input box ([line 412](src/aida/ui/qt/main_window.py#L412)).
- [`ChatBridge.switch_profile`](src/aida/ui/qt/bridge.py#L364) schedules the switch without checking `is_busy`.
- [`ChatSession.switch_profile`](src/aida/core/session.py#L243) changes `self.profile` and `self.profile_name` before `build_provider` succeeds, then closes the old provider after swapping the loop.
- The bridge catches only `UnknownProfileError` ([`bridge.py`](src/aida/ui/qt/bridge.py#L369)). An `UnknownProviderKindError`, client-construction error, or other failure can escape the background future without emitting `profile_switch_failed`.

There are two failure modes:

1. A valid switch during streaming/tool execution can close the provider that the active loop is using.
2. If building the new provider fails, the session advertises the new profile/name while retaining the old provider and loop. The UI failure handler explicitly assumes the session was left untouched, which is not true for this path.

**Recommendation:** build the new provider, completion settings, and loop in local variables first; only then atomically swap them and close the old provider. Reject or queue switching while a turn is active, and disable the selector while busy. Catch all expected provider/configuration construction errors and surface them through `profile_switch_failed`. Test a switch during an active turn and a known profile whose provider kind is invalid.

### P1 — Manual compaction can overwrite messages appended by a live turn

**Evidence**

- The File-menu action is not retained so it can be disabled during a turn ([`main_window.py`](src/aida/ui/qt/main_window.py#L278)).
- [`ChatBridge.compact_context`](src/aida/ui/qt/bridge.py#L379) schedules compaction with no busy check.
- [`compact_now`](src/aida/core/session.py#L401) computes a plan from the current message list. `_apply_trim_plan` then awaits another call to the active provider and finally replaces the entire list with `self.messages[:] = new_messages` ([`session.py`](src/aida/core/session.py#L343)).

If the agent appends an assistant/tool message while summarization is awaited, the final slice assignment is based on the stale plan and can discard that new in-memory history. Compaction also makes a second concurrent request through the same provider instance.

**Recommendation:** treat send, compaction, and profile switching as mutually exclusive session mutations. A single lightweight `asyncio.Lock` or explicit state machine is enough. The GUI should disable compaction while busy; the session should still enforce the invariant because CLI or future callers can bypass the GUI. A generation counter checked before applying the plan would be a useful defensive assertion.

### P1 — Filesystem timeout and result caps do not stop the underlying work

**Evidence**

- [`_run_blocking`](src/aida/workspace/files.py#L66) wraps `asyncio.to_thread` in `wait_for`. Cancelling the await cannot stop the worker thread.
- `find_files` and `search_text` call `sorted()` on recursive generators before inspecting the configured cap ([`files.py`](src/aida/workspace/files.py#L114) and [line 130](src/aida/workspace/files.py#L130)). The entire tree is therefore enumerated and materialized before the first capped result is returned.
- The same timeout wrapper is used by writes, copies, and moves ([`write_file`](src/aida/workspace/files.py#L263), [`copy_file`](src/aida/workspace/files.py#L290), [`move_file`](src/aida/workspace/files.py#L311)).

On a slow or hung network share, AIDA can say “timed out” while a copy/move/write continues in a background thread. An agent may retry based on the error while the first operation is still mutating the target. The recursive limits protect response size but not traversal time or memory.

**Recommendation:**

- Implement bounded traversal that stops discovering candidates when the cap is reached; do not globally sort an unbounded recursive generator.
- Give read-only scans cooperative cancellation that the worker checks between files/directories.
- Do not present thread cancellation as cancellation of a mutation. Either let copies/moves finish with a configurable timeout and visible progress, move them to a cancellable process, or return an explicit “operation may still be running” state and prevent retry until settled.
- Add a regression test in which a deliberately delayed operation times out and verify that no late mutation occurs after the result is returned.

### P1 — A temporarily unavailable RAG source is treated as deleted content

**Evidence**

- `_run_ingest` records missing source folders, but `seen_paths` contains only files successfully rediscovered ([`ingest.py`](src/aida/knowledge/rag/ingest.py#L237)).
- After discovery, every previously indexed path not in `seen_paths` is unconditionally deleted ([line 282](src/aida/knowledge/rag/ingest.py#L282)).

If an external drive, network share, cloud-synced folder, or permission becomes temporarily unavailable, an update can erase that source’s cached chunks from the index. A warning is returned, but the useful offline cache is already gone.

**Recommendation:** prune stale entries only under roots that were successfully enumerated. If any configured root is unavailable, preserve its indexed records and report them as stale/unverified. An explicit “prune confirmed missing files” option would make destructive reconciliation unambiguous. Add a test that indexes a folder, makes the folder unavailable, updates, and verifies the old chunks remain queryable.

### P2 — Quoted boolean and numeric configuration values are unsafe

**Evidence**

- `ProviderProfile.from_dict` uses `bool(value)` for `supports_vision` ([`settings.py`](src/aida/config/settings.py#L380)). Thus YAML `supports_vision: "false"` becomes `True`.
- `WorkspaceConfig.from_dict` passes `scripting_enabled` and `script_timeout_seconds` through without coercion or range validation ([`settings.py`](src/aida/config/settings.py#L601)). YAML `scripting_enabled: "false"` remains a truthy string, so scripting is enabled; `script_timeout_seconds: "30"` remains a string and can later fail during numeric comparison in [`_effective_timeout`](src/aida/coding/tools.py#L49).
- The generic boolean coercer also uses `bool(value)` ([`settings.py`](src/aida/config/settings.py#L46)), which will be wrong if it is reused for string input.

I reproduced all three conversions locally.

**Recommendation:** use one strict boolean parser accepting booleans and perhaps case-insensitive `true/false`, `yes/no`, `1/0`, rejecting everything else. Coerce numeric workspace fields and validate positive ranges. Report the source file and field in every warning. This does not require adding Pydantic to the runtime path; the existing helper approach is sufficient.

### P2 — Script timeout/kill does not terminate descendants and output is unbounded

**Evidence**

- [`run_subprocess`](src/aida/coding/runner.py#L39) launches one process, buffers both pipes through `communicate()`, and calls `proc.kill()` on timeout.
- The GUI Kill action also kills only that process ([`bridge.py`](src/aida/ui/qt/bridge.py#L577)).

A generated script or allowed command can spawn a child that survives Stop/timeout. A noisy process can also fill memory because stdout/stderr have no cap and are returned only after completion.

**Recommendation:** start a new process group/session and terminate the full tree using the platform-appropriate mechanism. Stream or cap captured output (with an explicit truncation marker) while retaining a short tail for diagnostics. Test a parent that spawns a long-lived child and a process that writes more than the capture limit.

### P2 — User-attached images do not survive conversation resume

**Evidence**

- `Message` has an `images` field ([`providers/base.py`](src/aida/providers/base.py#L115)).
- GUI attachments create `ImageRef(path=path)` ([`main_window.py`](src/aida/ui/qt/main_window.py#L729)).
- Message persistence stores content and tool-call fields but not `images`, and `_row_to_message` cannot reconstruct them ([`store.py`](src/aida/persistence/store.py#L152)).

The attachment’s text placeholder persists, but its pixel reference is lost on resume. It also continues to depend on the original user-selected path rather than a conversation-owned copy.

**Recommendation:** copy attached images into the conversation artifact store, persist their path/MIME/sequence, and rebuild `ImageRef`s on resume. Reusing the existing artifact table is preferable to a second storage system.

### P2 — MCP `ResourceLink` conversion discards the resource URI

**Evidence**

- The conversion module says a `ResourceLink` becomes a file artifact with a URI, but the implementation stores only its name and MIME type ([`results.py`](src/aida/mcp/results.py#L50)).
- `FileArtifact` has no URI field ([`artifacts/base.py`](src/aida/artifacts/base.py#L42)).
- The test verifies that `path` is `None` but never verifies preservation of the input URI ([`test_mcp_results.py`](tests/test_mcp_results.py#L63)).

The model receives a description of an unsaved file but not `file:///tmp/data.csv` (or another resource URI), so the useful part of the link is lost.

**Recommendation:** add a `uri` field to `FileArtifact` (or a dedicated resource-link artifact), preserve it, and include it in the model/UI representation. Correct the test so the URI is the central assertion.

### P2 — MCP namespacing does not guarantee provider-valid tool names

**Evidence**

- The code correctly documents the provider name constraint, but [`namespaced_tool_name`](src/aida/mcp/manager.py#L37) simply concatenates the configured server name and server-provided tool name.
- `McpServerConfig.name` and imported MCP configs are not restricted to the documented character set or combined length ([`settings.py`](src/aida/config/settings.py#L712)).

A server named `paper.search`, a Unicode name, or a long server/tool combination can still make the complete provider request invalid. This is especially likely when importing another client’s `mcp.json`.

**Recommendation:** create a stable provider-facing alias by sanitizing and length-limiting both components, retain the original display name, and detect collisions. Validate the final schemas before the first paid/model request so the error identifies the offending server and tool.

### P2 — The vision cap counts messages, not images

**Evidence**

- `MAX_ATTACHED_IMAGES` is described as an image count, but [`images_within_cap`](src/aida/providers/vision.py#L43) selects the most recent four **messages** containing images.
- Provider translators then attach every image in each selected message.

One MCP result or one user message containing many images bypasses the intended cap, increasing request size, latency, and vision-token cost.

**Recommendation:** select individual image references while walking backward through history and stop at the true count (and optionally a total byte/pixel budget). Test several images in one message as well as one image in many messages.

### P2 — Session startup cleanup starts too late

**Evidence**

- The store, active knowledge-base connections/providers, and MCP subprocesses are acquired before the cleanup `try` ([`session.py`](src/aida/core/session.py#L679) and [line 852](src/aida/core/session.py#L852)).
- `ConversationRecorder` construction and `load_history()` also occur before that `try` ([line 892](src/aida/core/session.py#L892)); its constructor can write the initial conversation row.
- The cleanup comment says recorder failure is covered, but the `try` begins only at line 909.

A locked/corrupt database or history-load error at this point can leave MCP subprocesses and knowledge-base clients open. An unexpected exception from `McpManager.start_all()` has a similar partial-start risk because only `McpServerError` is isolated per server.

**Recommendation:** put the whole acquisition sequence under one `try`/`AsyncExitStack`, registering each close operation immediately after acquisition. Transfer ownership to `ChatSession` only after construction succeeds.

### P3 — Documentation and tests have begun to drift from the implementation

Examples:

- [`docs/providers-and-secrets.md`](docs/providers-and-secrets.md#L180) says provider/embedding profiles cannot be created or edited in the GUI, while `ProfilesDialog` supports Add/Edit/Remove/Test for both.
- `PLAN.md` still says no GUI workspace editor exists ([`PLAN.md`](PLAN.md#L137)), while the toolbar and `WorkspaceManagementDialog` implement it. The plan also reports 1,319 tests; current collection is 1,450.
- [`docs/workspaces.md`](docs/workspaces.md#L119) has the heading “no GUI editor yet,” although the text underneath correctly limits the gap to two fields.
- Several UI assertions are vacuous (`condition or True`), including [`test_tool_call_widget.py`](tests/ui/test_tool_call_widget.py#L54) and [`test_artifact_widgets.py`](tests/ui/test_artifact_widgets.py#L30). One MCP helper also contains an immediately true wait condition before the real wait ([`test_mcp_management_dialog.py`](tests/ui/test_mcp_management_dialog.py#L385)).
- `.github/.DS_Store` is tracked.

Long historical “bug report” comments are useful during development, but some have already become false—for example, the startup cleanup comment and the profile-switch failure handler’s claim that the session is untouched. Consider keeping code comments focused on the current invariant and moving the history/rationale into `planning/COMPLETED.md` or short decision records.

**Recommendation:** do one documentation reconciliation before the next release, replace vacuous assertions with inspectable widget state, remove the tracked Finder metadata, and add `ruff format --check .` to CI after a one-time formatting-only change. A wheel-build/install smoke job and `pip check` would also cheaply validate the artifact users actually install.

## Lightweight, high-value feature opportunities

These are ordered by value-to-complexity, with an emphasis on scientific work and avoiding a heavier runtime.

### 1. Reproducibility manifest beside every generated report

Write `report.md.aida.json` (or one manifest per conversation/run) containing:

- conversation/workspace/profile/model identifiers;
- input paths with size and modification time, plus optional SHA-256 on demand for large data;
- MCP server/tool names and versions when available;
- tool arguments and artifact paths;
- generated script/interpreter/environment details;
- timestamps and AIDA version.

Most of this information already exists in the session, tool log, artifacts, and configuration. The first version can be simple JSON without a database migration. For scientific users this is more valuable than many “agentic” features because it answers, “what exactly produced this plot/report?”

### 2. First-class table and JSON artifact cards

AIDA already has `TableArtifact` and `JsonArtifact`, but only image/file artifacts receive frontend creation events. Structured results are rendered into text in the tool-call detail. Add `TableArtifactCreated` and `JsonArtifactCreated` events with compact Qt cards: preview rows/tree, copy, and export CSV/JSON. This makes scientific results easier to inspect without adding a plotting framework or dataframe dependency.

### 3. “Repeat tool call with edited arguments”

The MCP log already records name, arguments, result, timing, and error. Add a button that opens the arguments as editable JSON and submits the call after normal confirmation. A second button could save the edited invocation as a Quick Task. This is a small way to turn successful exploratory analysis into repeatable practice before full workflow automation lands.

### 4. Source freshness indicators

When displaying an artifact/report or resuming a conversation, compare the saved input size/mtime to the current file. Show “source changed since analysis” rather than silently letting a user assume an old result reflects new data. Default to size/mtime so huge HDF5/NXcanSAS files are cheap; offer hashing only when requested.

### 5. MCP tool allowlists, not only denylists

`disabled_tools` is useful, but an `enabled_tools` allowlist/preset is safer and leaner: an upstream server update cannot silently add twenty new schemas or a new mutating tool to a workspace. Show the estimated schema-token budget for the resolved workspace, not only group tool count. This directly helps small local models.

### 6. Local feedback and a diagnostic bundle

Add a local thumbs-up/down plus optional note on an answer/tool run, and an “Export diagnostic bundle” containing sanitized logs, active configuration names (not secrets), tool schemas, version/platform data, and the relevant event trace. This makes beta feedback actionable without telemetry or a service dependency.

### 7. Parallel read-only RAG retrieval

`_retrieve_context` currently awaits knowledge bases sequentially. Independent read-only queries can use `asyncio.gather` with the existing per-KB error isolation, reducing latency for workspaces with several knowledge bases. Keep ingest/build sequential unless measurements justify more complexity.

The planned headless `aida run`, stored workflows, and simple scheduler are still the right next larger product step. I would not expand them into a general workflow engine; the proposed linear steps, placeholders, stop-on-failure behavior, and normal conversation output are enough.

## MCP servers and integrations to consider

The best addition is not a large catalog. It is a **small audited preset system** built from the existing `add-pyirena` pattern: pinned command/package version, narrow default tool set, group, skills, scratch/output directory, keyring references, confirm-before-run defaults, and a Doctor check.

### 1. Playwright MCP — first-class preset, highest priority

This directly matches AIDA’s business-system use case. Microsoft’s [Playwright MCP](https://github.com/microsoft/playwright-mcp) uses structured accessibility snapshots, supports stdio, persistent or isolated browser profiles, storage state, an existing-browser extension, and a configurable output directory. The project explicitly states that it is **not a security boundary**, so AIDA’s confirmation and tool-selection layer remains important.

Recommended AIDA defaults:

- pin a tested package version instead of `@latest`;
- set `--output-dir` to the AIDA scratch folder and cap output size;
- use a dedicated per-workspace user-data directory or explicit storage state;
- default mutating actions (form submission, upload, download, destructive clicks) to confirmation;
- offer a read/navigation-only group for small models and review tasks;
- never store business credentials in `mcp.json`.

The upstream project now notes that CLI + skills can be more token-efficient for coding agents, while MCP remains useful for persistent state and exploratory/iterative browser work. AIDA is much closer to the latter case.

### 2. Jupyter MCP — optional preset for notebook-centered users

The [Datalayer Jupyter MCP Server](https://github.com/datalayer/jupyter-mcp-server) supports local Jupyter/JupyterHub, stdio, notebook/cell inspection and editing, execution feedback, and multimodal outputs. It adds real value when the notebook itself is the scientific record; it is unnecessary for users satisfied with AIDA’s script runner.

Keep it optional and expose a small allowed tool set initially: connect/list/read notebook, insert/update cell, execute selected cell, and retrieve output. Writes/deletes/restart-kernel should confirm. Its own allowed-tool configuration is a good match for AIDA’s lean-group philosophy.

### 3. Crossref DOI/metadata — small read-only scientific integration

Crossref’s [official REST API](https://api.crossref.org/) provides DOI lookup, bibliographic search, references, journals, funders, and related metadata; public access requires no signup. There are community MCP wrappers, but the useful surface is small enough that an audited seven-or-fewer-tool MCP—or even narrow native read-only tools—would be cheap to maintain.

Suggested tools: `lookup_doi`, `search_works`, `get_references`, and perhaps `lookup_funder`. Return compact structured JSON plus a formatted citation. This would materially improve paper review and citation verification without adding a large dependency.

### 4. Zotero — valuable, but choose/audit deliberately

Zotero integration would connect paper review to the user’s actual library, PDFs, annotations, tags, and citation metadata. The MCP ecosystem here is fragmented; examples range from small search/read servers to broad read/write library managers. Start with local/read-only search, item metadata, full text, and annotations. Require confirmation for adding notes, tags, attachments, or modifying records.

Do not advertise a community server as trusted merely because it appears in a registry. The [official MCP Registry terms](https://modelcontextprotocol.io/registry/terms-of-service) explicitly make no guarantee about server safety or accuracy and recommend evaluating each server. A narrow in-house Zotero adapter may be less work than supporting behavioral variation across several community packages.

### 5. GitHub MCP — useful for developers, not a default scientific group

GitHub’s [official MCP server](https://github.com/github/github-mcp-server) is useful for AIDA/pyIrena development: issues, pull requests, actions, releases, and repository search. Put it in a development-only group with minimum token scopes and read-only defaults. It does not belong in ordinary analysis workspaces.

### 6. Narrow MCPs for internal business systems

Where a stable API exists, a small domain MCP is preferable to browser automation for repeated operations. Start read-only: sample/status lookup, proposal/run metadata, inventory, scheduling, or document retrieval. Add narrowly named writes later with confirmation. Keep Playwright as the fallback for screens with no usable API and for exploratory workflows.

### What I would not add

- A generic filesystem MCP: AIDA’s native file tools already have workspace-aware safety and typed results.
- A generic shell/Python MCP: it would duplicate the script runner and weaken one coherent permission model.
- A separate generic memory/RAG MCP: native persistence and folder RAG are already integrated with the UI and workspace model.
- A large bundle of paper-search servers enabled together: overlapping schemas increase cost and ambiguity. Prefer Crossref + Zotero, then add PubMed/arXiv/DataCite only when a real discipline-specific need appears.
- Remote HTTP/SSE-only servers until AIDA intentionally implements remote MCP transport, authentication, and lifecycle semantics.

## Suggested implementation order

1. Fix invalid safety handling, session mutation serialization, filesystem timeout semantics, and RAG pruning.
2. Fix strict configuration parsing, process-tree termination, attachment persistence, resource-link URIs, tool-name validation, and the image cap.
3. Reconcile docs/tests and run the real-machine acceptance checks already listed in `PLAN.md`—especially real pyIrena image round-trip, Playwright login/state, Stop, conda coexistence, and network folders.
4. Add the reproducibility manifest and table/JSON cards.
5. Generalize `add-pyirena` into audited presets, starting with Playwright; add Jupyter and a narrow Crossref integration only for users who need them.
6. Continue with the deliberately small Phase 10 automation design.

That sequence makes AIDA more trustworthy and more useful to scientists without turning it into a heavier general-purpose agent platform.
