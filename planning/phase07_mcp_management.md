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

- [x] List configured servers: status (stopped/starting/running/error), tool count,
      group membership, linked skills — `McpManagementDialog`'s server list shows
      name + live status per row; tool count/groups/skills for the selected server
      are shown in the Details tab rather than inline in the list row (a
      simplification — the substance is one click away, not in the row itself).
- [x] Add/edit server dialog: command, args, env vars (values maskable), transport
      (stdio), linked skills (pick from skills folder), group membership —
      `ServerFormDialog`. One documented simplification of "values maskable": env
      vars are a single multi-line `KEY=VALUE` box with a "Hide values" checkbox
      that swaps the whole box between real and `KEY=***` display, not per-row
      masking — `QPlainTextEdit` (needed for multiple vars) has no per-widget echo
      mode the way `QLineEdit` does.
- [x] Import from existing standard `mcp.json` (paste or file-pick a Claude
      Desktop-style config; merge without clobbering) — `aida.mcp.config_io.
      merge_mcp_config` (pure, tested) + a file-picker in the dialog and
      `aida mcp import` on the CLI. A conflicting server name is skipped by
      default; the dialog prompts to overwrite, the CLI takes `--overwrite`.
- [x] Start / stop / restart per server; auto-start-on-enable stays lazy —
      `McpManager.start_server`/`stop_server`/`restart_server`, wired through
      `ChatBridge` so a server added mid-session works without restarting the
      whole chat. Fixed a real bug found while testing this exact path: an
      **anyio cancel-scope task-affinity issue** in `McpServerHandle` — see
      "Bugs found and fixed" below.
- [x] Delete server with confirmation — dialog's Remove button (`QMessageBox`
      confirm) and `aida mcp server remove` (prompts unless `--yes`).

### Groups editor

- [x] Create/rename/delete groups; drag or checkbox servers into groups — a group
      is derived (a server "belongs" by listing it in its own `groups`, no
      separate registry — unchanged design from Phase 3). `aida.mcp.groups.
      rename_group`/`delete_group` (new, pure, tested) rename/remove the name
      across every referencing server; `GroupsDialog` (GUI) and
      `aida mcp group rename/delete/list` (CLI) front them. "Create" happens by
      checking a new group name onto a server in `ServerFormDialog` (checkbox,
      not drag) — there is no separate "create an empty group" action since an
      unreferenced group has no meaning in this model.
- [ ] Set workspace default group from here; quick "active group" switcher stays in
      the main toolbar (from Phase 5) — **not done**: editing a workspace's
      `mcp_group` from this dialog would need a GUI workspace editor, which
      doesn't exist yet (`aida.ui.qt.main_window`'s own existing comments already
      note "no GUI 'new workspace' form exists yet" as a pre-Phase-7 gap). The
      toolbar's quick group switcher from Phase 5 (`McpQuickPanel`) is unchanged
      and still works. `aida workspace edit --mcp-group` remains the way to
      change a workspace's default group.
- [ ] Show estimated tool count per group (reminder why lean groups matter for
      small local models) — **not done**. `GroupsDialog` shows each group's
      member server names but not a tool-count sum (would need the dialog to
      hold a live `McpManager` reference and only have real numbers for
      already-started/tested servers). A small, isolated follow-up, same as
      Phase 6 left its DOCX-table tool-parameter gap for later.

### Skills management

- [x] Skills browser over `~/.aida/skills/`: list, preview (rendered MD), open in
      external editor, new-from-template — `SkillsBrowserDialog`, plus
      `aida.core.context.list_skills()` (new enumeration helper — every other
      skill helper only ever looked up a *named* skill, none listed the
      directory). Preview via `QTextBrowser.setMarkdown`; external editor via
      the already-available `QDesktopServices.openUrl`.
- [x] Per-server skills linkage editable here and in the server dialog —
      `ServerFormDialog`'s skills checklist (same widget/pattern as groups).
- [ ] Per-workspace extra skills selection (beyond server-linked ones) —
      **deliberately stays CLI-only** (`aida workspace edit --skills a,b,c`),
      for the same reason as the workspace-default-group item above: no GUI
      workspace editor exists to attach this to. Not new scope creep — an
      existing, documented gap from before this phase.

### Per-tool permissions

- [x] Tool list per server with enable/disable checkbox (disabled tools not exposed
      to the LLM) — `_ToolPermissionRow` in the Tools tab;
      `McpServerConfig.disabled_tools` filters at `McpManager._tools_for()` build
      time, so a disabled tool's schema is never sent to the model, not merely
      refused if called. Unit-tested (`test_disabled_tool_is_excluded_from_
      start_all`) and GUI-tested end to end against a real mock-mcp subprocess +
      `MockProvider` (`test_disabled_tool_is_absent_from_the_next_turns_schemas`).
      A permission change on an already-*running* server restarts it
      automatically so the change takes effect immediately, since disabling only
      happens at tool-list-build time.
- [x] Optional per-tool "confirm before run" flag (for e.g. bait_mcp instrument
      writes even inside relaxed workspaces) — `McpServerConfig.confirm_tools` +
      `McpManager._call_tool`'s confirm gate, reusing the *same* human-in-the-loop
      channel `aida.workspace.safety.SafetyGuard` already uses (moved to a new
      shared `aida.core.confirmation` module — see "Also moved" below) — a
      confirm-flagged MCP tool pops the identical GUI modal / CLI prompt, with
      zero new UI plumbing, independent of the workspace's own relaxed/confirm
      mode. **Wiring bug found and fixed**: `aida.cli.chat.start_session` built
      `McpManager` without ever passing it the session's `confirm_callback`, so
      every confirm-flagged MCP tool would have silently always refused in real
      usage (falling back to `McpManager`'s own `deny_all` default) despite
      passing every unit test that constructs `McpManager` directly with an
      explicit callback. Same gap existed in `ChatBridge._ensure_mcp_manager`'s
      lazy manager creation; both fixed. GUI-tested end to end, confirming even
      in a `safety="relaxed"` workspace (`test_confirm_flagged_tool_triggers_
      the_modal_even_in_relaxed_workspace`).

### Diagnostics

- [x] Live log panel: per-server stderr + AIDA's per-call records (server, tool,
      args summary, duration, result content types, sizes, status), filterable —
      the Log tab lists `McpManager.recent_calls()` (session-scoped, in-memory —
      matching `McpServerHandle.calls`'s own existing scope, not persisted to
      SQLite) filtered to the selected server; "filterable" here means "by
      server" (select a different server in the left list) rather than a
      free-text filter box. Stderr specifically: not shown as a running tail in
      the Log tab, but a start failure's stderr tail is included verbatim in
      both the failure dialog and the Details tab's `error:` line (see the next
      item) — "the log panel shows why" is satisfied via the Details pane
      sitting in the same dialog, not literally inside the Log tab's own list.
- [x] **Raw result inspector**: click any tool call in the chat or log → exact MCP
      response (JSON, base64 lengths noted), copyable — **scoped to the Log tab
      only, documented, not silently dropped**: making it reachable from a
      tool-call row inside the live *chat* transcript needs a `call_id` threaded
      through every `NativeTool`'s function signature
      (`aida.core.tools.ToolFunc`, and every module that builds one —
      `workspace/files.py`, `documents/tools.py`, `mcp/manager.py`) — confirmed
      with the user before starting this phase as a much larger, higher-risk
      change than justified for one nice-to-have. `ToolCallRecord` gained
      `arguments`/`content_preview` (built directly from the raw MCP content
      blocks in `McpServerHandle.call_tool`, independent of the artifact/event
      pipeline — no changes needed anywhere else), double-clicking a Log row
      opens `RawResultDialog` (pretty JSON, copy button). Tested against a real
      `get_image` call (`test_raw_inspector_shows_image_content_for_a_plot_call`)
      — confirms an image entry carries `mime_type`/`base64_length` and never
      the raw base64 payload itself.
- [x] "Test connection" button per server: initialize + list tools, report timing
      — `McpManager.test_connection` (reuses an already-running handle instantly
      rather than risking a second concurrent subprocess against a stdio server
      expecting one client); `aida mcp test NAME` on the CLI.

### Tests

- [x] Config roundtrip: GUI edits → `mcp.json` (+aida extras) → reload identical —
      plus a **real pre-existing gap fixed**: `McpServerConfig` only ever
      round-tripped 5 known keys; any unknown key from a real Claude-Desktop
      export (`disabled`, `autoApprove`, `type`, `cwd`) survived *loading*
      (already tested before this phase) but was silently deleted by the first
      GUI/CLI *save* afterward. New `extra: dict` field fixes this — tested
      (`test_unknown_mcp_server_keys_survive_a_save_and_reload`).
- [x] Import test with a real-world Claude Desktop config sample — `test_mcp_
      config_io.py` and `test_mcp_cmds.py` both import a raw dict/file shaped
      exactly like a Claude Desktop export (including its `disabled` key).
- [x] Per-tool disable respected in tool schemas sent to provider (unit test) —
      `test_disabled_tool_is_excluded_from_start_all` (backend) +
      `test_disabled_tool_is_absent_from_the_next_turns_schemas` (GUI, real
      `MockProvider`).
- [x] Confirm-flagged tool triggers a confirmation event (MockProvider test) —
      `test_confirm_flagged_tool_calls_the_confirm_callback` /
      `test_confirm_denied_produces_an_error_result_not_a_raised_exception`
      (backend) + the relaxed-workspace GUI test above.

---

## Acceptance — phase is done when all are checked

- [x] Add pyirena-mcp from scratch entirely in the GUI (command path, env vars,
      skills link, group) and run a successful plot call — no manual file editing
      — automated equivalent (mock-mcp + `MockProvider` standing in for
      pyirena-mcp + a real model, the same substitution every phase's own tests
      make): covered by the combination of `test_add_server_persists_to_
      settings_and_disk` (add, no manual file editing — `save_mcp_config` is
      the only disk write) and `test_start_and_stop_a_server_updates_status_
      and_tools` (start it live, its tools become callable in a real
      `ChatSession`). **Not run**: an actual pyirena-mcp server and a real plot
      call, same "outside this sandbox's scope" limitation every prior phase's
      real-server acceptance items already carry.
- [x] Import an existing Claude Desktop `mcp.json`; servers appear and work —
      "appear" is directly tested (`test_import_from_file_adds_new_server`);
      "work" follows from the same code path as any other server (an imported
      `McpServerConfig` is byte-for-byte the same shape a manually-added one
      is), which start/tool-call tests already cover.
- [x] Break a server on purpose (bad path): status shows error, log panel shows
      why, chat degrades gracefully — `test_breaking_a_server_on_purpose_
      shows_error_status_and_why` (GUI, real broken command). "Chat degrades
      gracefully" for an MCP server that's simply never enabled/started is the
      pre-existing Phase 3 failure-isolation behavior (`start_errors`, one bad
      server doesn't abort the session) — unchanged, still covered by
      `test_mcp_manager.py::test_failing_server_is_isolated_not_fatal`.
- [x] Disable one pyirena tool; verify the model no longer sees it —
      `test_disabled_tool_is_absent_from_the_next_turns_schemas`.
- [x] Mark a bait_mcp write-tool "confirm before run"; confirmation appears even in
      a relaxed workspace — `test_confirm_flagged_tool_triggers_the_modal_
      even_in_relaxed_workspace`.
- [x] Raw inspector shows the exact ImageContent response for a plot call —
      `test_raw_inspector_shows_image_content_for_a_plot_call`.
- [ ] CI green — cannot self-verify from this sandbox (no GitHub Actions access);
      `pytest -q` (649 passed) and `ruff check .` are both clean here as of this
      commit, same as every prior phase's note.

## Out of scope for this phase

Remote/HTTP MCP transport (future); an MCP "marketplace" (never); skills *editing*
beyond open-in-external-editor (plain MD files are the editor story).

---

## Implementation notes (backend → CLI → GUI, in build order)

Built full-stack per the approved plan, each layer tested before the next:

- **Backend** (Qt-free, unit-tested): `aida.core.confirmation` (new — see "Also
  moved" below); `McpServerConfig.disabled_tools`/`confirm_tools`/`extra`;
  `aida.mcp.groups.rename_group`/`delete_group`; `aida.mcp.config_io.
  merge_mcp_config` (new module); `ToolCallRecord.arguments`/`content_preview`/
  `recorded_at`; `McpManager.start_server`/`stop_server`/`restart_server`/
  `add_server_config`/`remove_server_config`/`test_connection`/`recent_calls`/
  `tool_names`, plus its new `confirm_callback` constructor param.
- **CLI**: `aida mcp server {list,show,add,edit,remove,disable-tool,enable-tool,
  confirm-tool,unconfirm-tool}`, `aida mcp group {list,rename,delete}`,
  `aida mcp import`, `aida mcp test` — `aida/cli/mcp_cmds.py`, mirroring
  `workspace_cmds.py`'s exact pattern (own argparse sub-parser, print+return-1
  for domain errors, no exceptions escape `main()`).
- **GUI**: `McpManagementDialog` (+ `ServerFormDialog`, `GroupsDialog`,
  `SkillsBrowserDialog`, `RawResultDialog`, `_ToolPermissionRow`) in
  `aida/ui/qt/mcp_management_dialog.py`, opened from a new "MCP Servers…"
  toolbar action in `MainWindow`. `ChatBridge` gained `register_mcp_server`/
  `unregister_mcp_server`/`start_mcp_server`/`stop_mcp_server`/
  `restart_mcp_server`/`test_mcp_connection`, each scheduled on the background
  loop like the existing `switch_profile`. `_qt.py` gained one new re-export,
  `QTabWidget`.

## Bugs found and fixed while building this phase

1. **anyio cancel-scope task-affinity in `McpServerHandle`** (real, and would
   have made live per-server stop/restart silently fail in the shipped app,
   not just in a test). `stdio_client()`/`ClientSession()`'s cancel scopes must
   be entered and exited by the *same* asyncio Task. Every pre-Phase-7 caller
   (`aida chat`'s `asyncio.run`, `aida.cli.conversations`, ...) happens to run
   a whole session in one Task, so this was invisible before now — but
   `aida.ui.qt.bridge.ChatBridge` schedules *every* action, including the new
   `start_mcp_server`/`stop_mcp_server`, as its own independent
   `run_coroutine_threadsafe` call, i.e. its own Task, which is exactly what
   Phase 7's live start/stop needs and exactly what broke
   (`RuntimeError: Attempted to exit a cancel scope that isn't the current
   task's current cancel scope`). Fixed by giving each handle one dedicated
   background task (`_serve`) that owns `stdio_client`/`ClientSession` for its
   whole running lifetime; `start()`/`stop()` from any external task now just
   signal it (`asyncio.Event`) and await its completion, which has no
   task-affinity requirement of its own. This also fixes an equivalent latent
   bug in `McpManager.aclose()` when two real servers are running at once —
   verified directly (see this phase's dev notes) rather than only inferred.
2. **`confirm_callback` never reached a real `McpManager`.**
   `aida.cli.chat.start_session` and `ChatBridge._ensure_mcp_manager`'s lazy
   manager creation both built `McpManager` without passing the session's
   `confirm_callback` through, so a confirm-flagged tool would always have
   silently refused in real usage regardless of what the user answered —
   every unit test that constructs `McpManager` directly with an explicit
   callback passed fine, which is exactly why this class of gap is easy to
   miss without an end-to-end test. Fixed in both places; regression test
   `test_start_session_passes_confirm_callback_to_mcp_manager` added.
3. **`mcp.json` wasn't actually round-trip-safe** (see the Tests section above)
   — `McpServerConfig.extra` fixes it.

## Also moved

`ConfirmationRequest`/`ConfirmCallback`/`ConfirmationDenied`/`deny_all` moved
from `aida.workspace.safety` to a new `aida.core.confirmation` (re-exported
from `aida.workspace.safety` for every existing importer). Required, not just
tidier: `aida.mcp.manager`'s new per-tool confirm gate needs the same
callback type `SafetyGuard` uses, but `aida.workspace.workspaces` already
imports `aida.mcp.groups`, so `aida.mcp.manager` importing anything from
`aida.workspace.safety` directly would recreate the import cycle that the
`unique_destination` move (`aida.workspace.safety` → `aida.config.paths`, the
prior review pass) fixed the same way.

**Verification:** 649 tests passing (`pytest -q`, up from 566 at the start of
this phase), `ruff check .` clean, in this environment.
