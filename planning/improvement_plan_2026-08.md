# AIDA — Review findings & improvement plan (2026-08-22)

Scope of this review: full read of `src/aida/` (core, providers, mcp, workspace,
documents, knowledge, persistence, coding, cli, ui/qt), the planning docs, and a
full test run. **State of the project is good**: the layering rules from PLAN.md
are actually enforced, the async/Qt bridge is carefully done, the test suite is
green (1011 passed; `test_rebuild_reports_a_folder_it_cannot_actually_list` fails
only when run as root, where chmod 000 is ignored — an environment artifact, not
a bug), and most historical bug reports have been fixed with tests attached.

The findings below are ordered by what will help most over the next 1–2 weeks.
The consistent theme: **the engine is solid; the configuration surface is the
weak spot** — several config objects (provider profiles, embedding profiles,
workspaces) still have no GUI editor at all, which is exactly the "configuration
is cumbersome" experience.

---

## 1. Verified bugs (small, fix first — roughly a day total)

- [x] **Dead MCP quick panel checkboxes.** `McpQuickPanel` (ui/qt/selectors.py)
      emits `enabled_servers_changed`, but nothing anywhere connects to it —
      grep confirms the signal has zero receivers. Ticking/unticking a server
      checkbox in the right-hand panel silently does nothing, which reads as
      "AIDA ignored my setting". Either wire it (update the workspace's
      `mcp_group`/explicit server list + prompt to restart the session, like
      FolderDisplay's "Save to Workspace" flow) or replace the checkboxes with a
      read-only status list plus an "MCP Servers…" button. A misleading control
      is worse than no control.
      **Done (2026-08-22):** there's no per-workspace explicit server list to
      wire the checkboxes to — `mcp_group` is the only actual mechanism, and
      inventing a new config concept for this was out of scope for a bug fix.
      Went with the second option: checkboxes are now disabled (read-only
      status, still reflecting the resolved group), and a new "MCP Servers…"
      button (`manage_requested` signal) opens the real `McpManagementDialog`.
      Tests: `test_mcp_quick_panel_checkboxes_are_read_only`,
      `test_mcp_quick_panel_manage_button_emits_manage_requested`,
      `test_mcp_quick_panel_manage_button_opens_management_dialog`.

- [x] **Failed profile switch is silent in the GUI.** `ChatBridge.
      profile_switch_failed` is emitted (bridge.py:298) but never connected in
      `MainWindow._wire_bridge_signals`. If a mid-session switch fails, the user
      sees nothing and the toolbar dropdown is left showing a profile that is
      not actually in use — the exact class of confusion the earlier
      "I selected local AI but it used Argo" bug was about. Connect it to a
      warning dialog + `_refresh_profile_selector()`.
      **Done (2026-08-22):** connected to a new `_on_profile_switch_failed`
      handler — warning dialog + `_refresh_profile_selector()`, exactly as
      suggested. Test: `test_failed_profile_switch_warns_and_resets_the_selector`.

- [x] **`aida doctor` recommends a command that doesn't exist.** doctor.py:114
      says "add one with `aida config profile add`" — `aida config` only has the
      `secret` subcommand. Point it at editing `providers.yaml` (or implement
      `aida config profile add`, see §3).
      **Done (2026-08-22):** message now names the actual `providers.yaml`
      path (via `paths.config_dir()`) instead of the nonexistent command.

- [x] **Scalar-vs-list footgun in hand-edited YAML.** `WorkspaceConfig.from_dict`
      does `list(data.get("source_folders", []))` — a hand-edited
      `source_folders: /some/path` (scalar instead of list) silently becomes a
      list of single characters (`['/', 's', 'o', …]`), producing nonsense
      allowed-roots with no warning. Same pattern for `skills`,
      `command_allowlist`, `knowledge_bases`, and `McpServerConfig.args`.
      `AppConfig` already has the `_coerce`/`_coerced_fields` machinery for
      exactly this; extend it (or a light variant: "if str, wrap in [str] and
      warn") to WorkspaceConfig / McpServerConfig / ProviderProfile.
      **Done (2026-08-22):** added the "light variant" — a new
      `_coerce_str_list` helper (wrap a bare string in a one-item list with a
      warning; fall back to the default, also warned, for anything else that
      isn't a list). Applied to `WorkspaceConfig` (`source_folders`, `skills`,
      `knowledge_bases`, `command_allowlist`), `McpServerConfig` (`args`,
      `groups`, `skills`, `disabled_tools`, `confirm_tools`), and
      `KnowledgeBaseConfig.source_folders` (same footgun, not explicitly
      named in the review but identical pattern in the same file).
      `ProviderProfile` has no list-typed fields today, so nothing to change
      there. Tests in `tests/test_settings.py`
      (`test_workspace_config_wraps_a_hand_edited_scalar_list_field` and
      siblings).

- [x] **Truncated replies are silent.** `MessageFinished(stop_reason="length")`
      is deliberately ignored by both `print_event` and `ChatPanel.handle_event`
      — and `AnthropicProvider` defaults `max_tokens` to **4096**, which a long
      generated report will hit. The user just gets a reply that stops
      mid-sentence with no indication why. Show a small notice ("reply hit the
      max-tokens limit — raise it in the profile settings") when stop_reason is
      "length". Pairs with the per-profile `max_tokens` setting in §2.
      **Done (2026-08-22):** `print_event` now prints a `[notice]` line on
      `stop_reason == "length"`; the GUI shows a new `TruncationNotice` widget
      (amber, distinct from `ErrorBanner`'s red since the turn didn't fail) in
      `ChatPanel.handle_event`. B2's per-profile `max_tokens` field itself is
      not yet done — still open, see §2.

- [x] **CLI `/max-iterations` is quietly reset by `/profile`.**
      `ChatSession.switch_profile` rebuilds the AgentLoop from
      `settings.app.max_agent_iterations`, discarding a value set moments
      earlier via `/max-iterations`. Carry `self.loop.max_iterations` over when
      building the new loop.
      **Done (2026-08-22):** `switch_profile` now reads `self.loop.
      max_iterations` before rebuilding and passes it straight through.

- [x] **`ErrorBanner` is unreadable in dark mode.** chat_panel.py:334 hardcodes
      `background-color: #ffe5e5` but leaves the text color to the palette —
      dark themes render near-white text on pale pink. Set an explicit dark text
      color alongside the fixed background (or derive both from the palette).
      **Done (2026-08-22):** stylesheet now sets an explicit `color: #7a1f1f`
      alongside the fixed pink background, inherited by the child label.

- [x] **Non-atomic config writes.** `_write_yaml`/`_write_json` (config/
      settings.py) write in place; a crash or power loss mid-write truncates
      `config.yaml`/`workspaces.yaml`/`mcp.json`. Write to a temp file in the
      same directory and `os.replace()`. Cheap, and it protects the files the
      whole app depends on.
      **Done (2026-08-22):** both now go through a new `_atomic_write_text`
      helper — write to a `tempfile.mkstemp` file in the same directory,
      `fsync`, then `os.replace()`; the temp file is cleaned up on any error.

- [x] **KB ingest blocks the shared asyncio loop.** `_ingest_file`
      (knowledge/rag/ingest.py) calls `_extract_text` → `read_document`
      synchronously inside the async ingest — a large PDF parse freezes the one
      background loop that also runs chat turns and MCP calls. Wrap the
      extraction in `asyncio.to_thread(...)` (embedding calls are already async).
      **Done (2026-08-22):** `_extract_text` is now called via
      `asyncio.to_thread(...)` inside `_ingest_file`.

- [x] **Workspace selector shows "(no workspace)" on startup even though
      the last workspace actually loaded.** User report: on launch the
      session clearly comes up in the right workspace (folder display, MCP
      panel, etc. all correct), but the toolbar's workspace dropdown shows
      "(no workspace)" regardless. Same root cause and same shape as an
      earlier fixed bug ("I restored prior session and have selected local
      AI ... I suspect it must be using cloud (Argo)", documented in the
      code comment above `MainWindow._on_session_ready`'s
      `_refresh_profile_selector()` call): `MainWindow.__init__` calls
      `_refresh_workspace_selector()` exactly once, synchronously, right
      after `bridge.start()` — but `bridge.start()` only *kicks off*
      `start_session(...)` on the background asyncio loop and returns
      immediately, so `self.bridge.session` is still `None` at that point
      and the dropdown falls back to its "(no workspace)" default. Once
      the session actually finishes starting (a moment later, on
      `session_ready`), the profile selector *does* get refreshed a second
      time in `_on_session_ready` — the earlier fix for the analogous
      profile bug — but the workspace selector never did, so it stayed
      wrong for the entire session.
      **Done (2026-08-23):** added `self._refresh_workspace_selector()` to
      `_on_session_ready`, alongside the existing profile-selector
      refresh. Test: `tests/ui/test_main_window.py::
      test_workspace_selector_shows_the_actual_active_workspace_once_ready`
      (verified it actually catches the regression — fails with the fix
      reverted, passes with it in place).

- [x] **Windows CI flake:
      `test_shutdown_cancels_and_waits_for_an_in_flight_turn`.** CI report
      (`ubuntu`/`macos` green, `windows-latest` × Python 3.11 red):
      `assert len(events) == events_before, "a closing bridge kept emitting
      events"` failed — one extra `ToolCallFinished` event showed up after
      `bridge.shutdown()`. Root cause traced to the test's own
      instrumentation, not `ChatBridge`: the test does
      `loop_thread.loop.call_soon_threadsafe(release.set)` (unblocks an
      in-flight tool call, on the background loop thread) immediately
      followed by `bridge.shutdown(timeout=10.0)` (whose very first line,
      `self._closing = True`, runs synchronously on *this* — the test/Qt —
      thread). The test's assertion implicitly depends on that
      `self._closing = True` write landing before the background loop
      thread finishes processing `release.set`, resuming the blocked tool
      call, and emitting its `ToolCallFinished` event — but nothing
      actually synchronizes those two independent, concurrently-scheduled
      actions on two different OS threads; it only worked "by default" on
      Linux/macOS because completing that whole chain of loop-thread
      scheduling hops reliably took longer than this thread's single next
      bytecode instruction. Windows CI's thread/loop scheduling closed that
      margin often enough to occasionally lose the race. `ChatBridge`
      itself behaves correctly here — `_drain`'s `if not self._closing:
      emit(...)` gate is exactly the intended mechanism — this was purely
      an unsynchronized-race bug in how the test triggered the scenario.
      **Done (2026-08-23):** the test now sets `bridge._closing = True`
      directly, *before* scheduling `release.set()`, making the ordering
      the test actually cares about (turn in flight *and already closing*
      → no further events) true by construction instead of by scheduling
      luck; `bridge.shutdown()` immediately after just re-sets the same
      flag, a harmless no-op, then does its real async cleanup exactly as
      before. Verified: 20/20 repeated runs pass locally; full suite
      (1216 tests, CI's combined `pytest -v` invocation) clean.

---

## 2. Backend improvements (highest value first)

- [x] **(B1) Vision input — let the model actually see the plots.** Today an
      `ImageArtifact` from pyIrena MCP displays in the GUI, but its pixels never
      reach the model (`Message.content` is plain `str` everywhere; readers.py
      documents this as the known v1 limitation). For a tool whose flagship use
      case is "plot it and look at it", this is the single most valuable
      structural upgrade: the agent could judge fit quality from the residuals
      plot, notice a bad dataset, describe a figure it just made.
      Plan of attack:
      1. Extend `Message` with an optional `images: list[ImageRef]` (path +
         mime), keeping `content: str` — additive, so nothing existing breaks.
      2. Translate in both providers: Anthropic `image` content blocks
         (base64), OpenAI-compat `image_url` data-URLs (Ollama/LM Studio
         support this shape for vision models; harmless to gate behind a
         per-profile `supports_vision` flag).
      3. Attach images at two points: tool-result messages whose artifacts
         include an `ImageArtifact` (cap: N most recent, downscale to ~1024px
         to bound tokens), and GUI attachments of image files.
      4. Persist nothing new: images are already on disk with paths recorded.
      This is a few days of work and each step is testable with MockProvider.
      **Done (2026-08-22):** implemented per the plan above, plus a new
      `ImageRef`/`aida.providers.vision` module shared by both providers
      (`images_within_cap` for the recency cap — default 4 — and
      `read_image_b64` for read+downscale+encode, using Pillow when
      installed and falling back to original-size bytes otherwise). Pillow
      added as a lazy import in the `docs` extra, same pattern as
      pymupdf/python-docx. `supports_vision` defaults to `False` on both
      `ProviderProfile` and `CompletionSettings` — opt-in per profile, since
      a default of `True` would risk breaking existing small local models
      the moment a tool result carries an `ImageArtifact`. One deliberate
      scope decision: OpenAI chat-completions rejects multi-part content on
      `role="tool"` messages (a hard API constraint), so tool-result images
      are only attached for Anthropic; GUI image attachments (user-role
      messages) work on both. Wired end to end: agent.py attaches
      `ImageRef`s from `ImageArtifact` tool results, and the GUI's
      Attach/drag-and-drop flow (`MainWindow._augment_with_attachments`)
      builds one per attached image file, through `ChatBridge`/`ChatSession`
      to the outgoing `Message`. Tests: `test_agent_loop.py` (tool-result
      image attachment, and the no-image-artifacts case), a dozen new cases
      in `test_provider_translation.py` (Anthropic tool-result/user-message
      image blocks, the recency cap, an unreadable-path skip, OpenAI-compat
      user-message-only attachment, `providers/vision.py` unit tests),
      `test_provider_lifecycle.py` (settings.supports_vision reaching the
      real SDK call kwargs for both providers), and two GUI integration
      tests in `test_main_window.py` (image vs. non-image attachment).

- [x] **(B2) Per-profile sampling settings + honest cost.** PLAN.md §4 promised
      "a profile = … sampling defaults", but `ProviderProfile` has no
      `max_tokens`/`temperature`, and `ChatSession` builds
      `CompletionSettings(model=…)` with hardcoded temperature 0.7 and
      max_tokens None (→ Anthropic's 4096 default). Add optional
      `max_tokens`, `temperature`, and `usd_per_m_input`/`usd_per_m_output`
      fields to `ProviderProfile`; thread them into `CompletionSettings` and
      `estimate_cost_usd` (which currently uses one fixed rate for every
      provider — misleading when the active profile is a free local model).
      Small change, removes two real annoyances at once.
      **Done (2026-08-22):** added `max_tokens`, `temperature`,
      `usd_per_m_input`, `usd_per_m_output` (all optional, `None` = fall back
      to the previous fixed defaults) plus `supports_vision` (B1) to
      `ProviderProfile`, coerced/validated in `from_dict` the same way as
      phase-1's `_coerce_str_list` fix. A new
      `_completion_settings_for_profile` helper (used by both
      `ChatSession.__init__` and `switch_profile`, so the two can't drift)
      builds `CompletionSettings` from a profile's overrides.
      `estimate_cost_usd` gained optional `input_usd_per_million`/
      `output_usd_per_million` keyword args (`None` keeps today's fixed
      rate), threaded through at all three call sites: the CLI's
      session-total cost line and the GUI's live usage label both now pass
      `session.profile.usd_per_m_input`/`usd_per_m_output`. Tests:
      `test_settings.py` (roundtrip, defaulting, a badly-typed field
      rejected) and `test_cost.py` (override applied, override replaces only
      the given rate, `None` falls back to default), plus
      `test_chat_cli.py` covering `_completion_settings_for_profile`'s
      fallback and override behavior.

- [x] **(B3) Anthropic prompt caching.** Every turn resends the whole system
      block — workspace context + skills + pyirena-mcp's long `instructions` —
      plus the full tool schema list (100+ tools namespaced). Adding
      `cache_control: {"type": "ephemeral"}` markers on the system prompt and
      the tools array in `to_anthropic_params`/`complete()` is ~20 lines and
      typically cuts input-token cost by 5–10× for multi-turn tool-heavy
      sessions (exactly the UC3/UC4 pattern). Report `cache_read_input_tokens`
      in `UsageInfo` so the savings are visible.
      **Done (2026-08-22):** added `to_cached_system_param`/
      `to_cached_tools_param` in `anthropic_.py` — an ephemeral
      `cache_control` marker on the system prompt's single text block, and
      on the last entry of the tools array (Anthropic caches everything up
      to and including a marker, so one marker per array covers it all).
      `UsageInfo` gained `cache_creation_input_tokens`/
      `cache_read_input_tokens`, populated from the SDK's `message_start`
      usage object (the only event that carries them) via `_StreamState`.
      Both the CLI's `[usage]` line and the GUI's assistant-bubble meta line
      now append a ", N cached" note whenever a turn actually hit the cache.
      Tests: new cases in `test_provider_translation.py` (both cache-param
      helpers, including the empty/None passthrough and that the input list
      isn't mutated in place) and `test_provider_lifecycle.py` (cache_control
      actually reaching the real `messages.create` kwargs); CLI cache-note
      display covered in `test_chat_cli.py`.

- [x] **(B4) Parallel MCP server startup.** `McpManager.start_all` launches
      servers sequentially; with 2–3 servers at up to 30 s startup timeout each,
      worst-case session start is additive. `asyncio.gather` over handles keeps
      the same failure isolation (each start already catches `McpServerError`)
      and makes the common case as fast as the slowest server.

      **Done (2026-08-22):** `start_all` now launches every configured server
      concurrently via `asyncio.gather` on a per-server coroutine that catches
      `McpServerError` and returns `None`, merged back in original config
      order so tool-name-collision resolution stays deterministic regardless
      of which server actually finishes first. Test: new
      `test_start_all_launches_servers_concurrently` in `test_mcp_manager.py`.

- [x] **(B5) Configurable script/command timeout.** `run_python_script`/
      `run_command` are pinned to `DEFAULT_RUN_TIMEOUT_SECONDS = 30` — a real
      reduction/analysis script will blow through that. Add
      `WorkspaceConfig.script_timeout_seconds` (default 30) and an optional
      `timeout` argument in the two tool schemas (capped by the workspace
      value), so the model can ask for more when the task warrants it.

      **Done (2026-08-22):** `WorkspaceConfig.script_timeout_seconds` (default
      30.0) added; `run_python_script`/`run_command` gained an optional
      `timeout` tool argument, resolved by a new `_effective_timeout` helper
      that caps a model-requested value at the workspace ceiling (falling
      back to the workspace default for a missing/invalid/non-positive
      request). The GUI's Code Editor "Run" button (previously hardcoded to
      the 30 s default) now uses the workspace's configured timeout too, and
      the Workspace management dialog gained a spin box to set it. Tests:
      `test_coding_tools.py`, `test_settings.py`,
      `tests/ui/test_workspace_management_dialog.py`,
      `tests/ui/test_code_editor_dialog.py`.

- [x] **(B6) Secrets in `mcp.json` env blocks.** MCP server `env` values (e.g. a
      Tavily/Brave API key for the search server) are stored in plaintext
      `mcp.json` — the one remaining hole in the "secrets never touch
      YAML/JSON" rule. Support a `keyring:NAME` (or `secret:NAME`) value syntax
      resolved at launch time in `McpServerHandle.start()` via
      `aida.config.secrets`, and let the GUI's env editor offer "store value in
      keychain" instead of masking-only.

      **Done (2026-08-22):** `McpServerHandle.start()` now resolves any
      `keyring:NAME`/`secret:NAME` env value into `self._resolved_env` via a
      new `resolve_env_secrets` function (using the existing
      `aida.config.secrets.get_secret`) before spawning the subprocess, so a
      missing/misspelled secret reference fails fast and is isolated
      per-server the same way every other `start()` failure already is. The
      MCP management dialog's server form gained a "Store Value in
      Keychain…" button that writes through `set_secret` and rewrites the env
      text to reference it, independent of the existing "hide values"
      masking toggle. Tests: `test_mcp_server.py` (7 new cases),
      `tests/ui/test_mcp_management_dialog.py` (2 new cases).

- [x] **(B7) Context-budget visibility.** Trimming works but is invisible
      (logged only), and `estimate_tokens` ignores `tool_calls` arguments, so
      the estimate skews low in tool-heavy sessions. Two small steps: count
      tool-call argument JSON in `estimate_tokens`, and emit a
      `ContextTrimmed(dropped_turns, estimated_tokens)` event that the CLI
      prints and the GUI shows in the status bar. This continues the
      "no black boxes" thread from the cost work.

      **Done (2026-08-22):** new `estimate_message_tokens` wraps
      `estimate_tokens` plus the JSON size of each tool call's
      `{name, arguments}`, used by `trim_history`'s internal budget
      calculation instead of undercounting tool-heavy messages. A new
      `ContextTrimmed(dropped_turns, estimated_tokens)` event (added to the
      `AgentEvent` union) is yielded by `ChatSession.send()` whenever
      `_trim_context` actually drops turns; the CLI's `print_event` prints a
      `[context] trimmed ...` line, and the GUI's `MainWindow` shows the same
      information in the status bar for 8 seconds.

- [x] **(B8) Move `ChatSession`/`start_session` out of `aida.cli`.** The Qt
      bridge importing `aida.cli.chat` is the one place the layering reads
      wrong (`ui → cli`). Mechanically extract the session engine
      (`ChatSession`, `start_session`, the error types) into
      `aida.core.session` (or `aida.session`) and have `aida.cli.chat` and
      `aida.ui.qt.bridge` both import from there; keep re-exports in
      `aida.cli.chat` so nothing external breaks. Not urgent, but it makes the
      contract test honest and helps every future frontend (Phase 10's
      `aida run` will want the same engine, too).

      **Done (2026-08-22):** the session engine (`ChatSession`,
      `start_session`, `cli_confirm`, `resolve_mcp_servers`,
      `resolve_profile`, and the three `Unknown*Error` types, plus their
      private helpers) moved to a new `aida.core.session` module.
      `aida.ui.qt.bridge` now imports directly from there instead of from
      `aida.cli.chat`; `aida.cli.chat` re-exports every one of those names so
      `aida.cli.conversations` and every existing test keep working
      unchanged, and keeps only genuinely CLI-frontend code (`print_event`,
      the REPL loop, Ctrl-C handling, `argparse` wiring, `main`). The ~90
      `monkeypatch.setattr("aida.cli.chat.NAME", ...)` string patches across
      10 test files that targeted names now defined in `aida.core.session`
      (`build_provider`, `McpManager`, `build_embeddings_provider`,
      `skills_dir`) were updated to the new module path — Python resolves a
      moved function's internal name lookups against its *new* enclosing
      module's globals, so the old string patches would otherwise have
      silently stopped intercepting anything.

- [x] **(B9) MCP tool calls had no argument-type coercion against the
      tool's own schema.** A user hit `browser_snapshot`/
      `browser_take_screenshot` (Playwright's MCP server) failing with Zod
      validation errors (`expected number, received string → at depth`,
      `expected boolean, received string → at fullPage`) — the model had
      sent `{"depth": "3"}`/`{"fullPage": "true"}` (quoted) where the
      schema wants a bare number/boolean. Traced the call path
      (`anthropic_.py`/`openai_compat.py`'s `json.loads(builder["arguments"])`
      → `ToolCallStarted.arguments` → `AgentLoop` → `McpManager._call_tool`
      → `McpServerHandle.call_tool`) and confirmed AIDA did no
      transformation of argument values anywhere in it — whatever Python
      types `json.loads` produced from the model's own generated JSON text
      were passed straight through to the MCP server unmodified. So the
      failure originates with the model (a known characteristic of some
      models' function-calling output — quoting every leaf value as a
      string, even for typed parameters), not a bug in AIDA's dispatch
      code — but it cost several failed turns of the model re-guessing the
      same call before the user gave up, and AIDA already has the tool's
      real JSON schema on hand at the exact point it dispatches the call.

      **Done (2026-08-23):** new `aida.mcp.argument_coercion.coerce_arguments`
      — walks a tool call's arguments against its `inputSchema`/
      `ToolSchema.parameters` and repairs a value that fails to match its
      declared type by trying `json.loads` on it (recovers exactly the
      "quoted number/boolean/array/object" case) plus, only when the
      schema unambiguously wants `"boolean"`, a case-insensitive
      `"true"`/`"false"` text fallback for JSON-invalid capitalizations
      like Python's `"True"`. Recurses into already-correctly-typed
      objects/arrays so a nested mis-typed value (e.g. one bad entry in an
      otherwise-valid list) still gets fixed. Deliberately conservative: a
      value that already matches its schema's type is never touched (a
      string that happens to look numeric stays a string if the schema
      says `"string"`), and a value with no confidently-resolvable
      declared type (`$ref`, `allOf`, missing `type`, ...) is left alone
      rather than guessed at — this repairs one specific, common,
      mechanical failure mode, not a general validator. Wired into
      `McpManager._build_native_tool`'s call wrapper, immediately before
      dispatch; when it corrects anything, a `WARNING`-level
      `aida.mcp`-logger line names exactly what was changed, so a
      malformed-argument model isn't a silent black box — console-visible
      the same way the existing `system_prompt`-file-not-found warning is.
      Tests: `tests/test_mcp_argument_coercion.py` (17 cases covering the
      pure coercion logic — scalars, unions, `anyOf`, arrays, nested
      objects, and every "must NOT touch this" guard), 4 new cases in
      `tests/test_mcp_manager.py` (the wrapper actually applies it before
      calling `_call_tool`, well-typed arguments pass through unchanged,
      and the warning log fires only when something was actually
      corrected).

- [x] **(B10) No well-known scratch/temp folder for agents and MCP
      servers.** User report: "Agents seem to be saving temporary files
      (python scripts, web page descriptions, etc) in random places. /tmp,
      repo home folder, .aida folder - quite randomly." Traced to two
      independent root causes, both confirmed by reading the actual `mcp`
      SDK source (`inspect.getsource`), not assumed: (1)
      `McpServerHandle._serve()` built `StdioServerParameters(...)` with no
      `cwd` at all, so every MCP server subprocess inherited *AIDA's own*
      process cwd — whatever directory the user happened to launch
      `aida-gui`/`aida` from in their shell (explains the "repo home
      folder" symptom for a user who runs `aida-gui` from inside their
      repo). (2) the `mcp` package's own `get_default_environment()` only
      ever inherits `HOME`/`LOGNAME`/`PATH`/`SHELL`/`TERM`/`USER` from
      AIDA's process — **never** `TMPDIR`/`TEMP`/`TMP`, regardless of what
      AIDA itself has set — so a tool that does the OS-default thing
      (`tempfile.mkdtemp()`) had nowhere predictable to land (explains the
      `/tmp` scattering). Also confirmed it's always safe to pass a
      populated `env` dict to `StdioServerParameters`: the SDK merges it on
      top of its own safe defaults, never replaces `PATH`/`HOME`/etc.

      **Design chosen** (mirrors the existing `records_dir` override
      pattern exactly): one new well-known folder, `~/.aida/tmp` by
      default (`paths.default_scratch_dir()`/`ensure_scratch_dir()`),
      overridable via a new `AppConfig.scratch_dir` field. Deliberately
      *not* defaulted under `~/Documents/Aida/` (the existing
      human-readable-records root) despite it being the more discoverable
      location — that folder may be inside a cloud-synced directory
      (iCloud Drive/OneDrive), and a scratch folder churns (many small
      files written and deleted in quick succession), which is exactly the
      failure mode already diagnosed once in this codebase (a cloud-synced
      Obsidian vault raising `PermissionError` under
      `test_knowledge_ingest.py`). Discoverability is instead solved
      directly, per the user's own suggestion, with a one-click "Open
      Scratch Folder" File-menu action (mirroring "Open Records Folder").

      **Done (2026-08-23):**
      - `aida.config.paths`: new `default_scratch_dir()`/
        `ensure_scratch_dir()`, mirroring `default_records_dir()`/
        `ensure_records_dir()`.
      - `AppConfig.scratch_dir: str | None = None` (settings.py's
        dataclass field / `to_dict()` / `_APP_FIELD_KINDS`, kept in sync
        per the existing consistency test).
      - `aida.core.session.start_session`: computes the effective scratch
        dir once per session, adds it to `SafetyGuard`'s
        `global_allowed_folders` (same always-allowed treatment as
        `artifacts_dir()` — writes there never need confirmation), passes
        it to `McpManager(...)`, and tells the model about it via a new
        `scratch_dir` paragraph in `build_workspace_context_block`
        (its own labeled section: "not backed up, may be cleared
        periodically").
      - `aida.mcp.manager.McpManager`: new `scratch_dir` constructor
        param, wired through the existing `_handle_kwargs()` helper so
        all three `McpServerHandle` construction sites (`start_all`,
        `start_server`/`restart_server`, `test_connection`) pick it up
        automatically as `cwd=`.
      - `aida.mcp.server.McpServerHandle`: new `cwd` param; passed to
        `StdioServerParameters(cwd=...)` (root cause 1) and used to seed
        `TMPDIR`/`TEMP`/`TMP` in the resolved env, via a new
        `_scratch_env_defaults()` helper — merged in *before*
        `resolve_env_secrets(self.config.env)` so an explicit
        server-config env value always wins (root cause 2).
      - `aida.ui.qt.bridge.ChatBridge`: captures the resolved scratch dir
        in `start()` (a new `_scratch_dir` attribute) so
        `_ensure_mcp_manager()`'s live-add-server path (which builds its
        own `McpManager` directly, not through `start_session`) gets the
        same wiring.
      - `aida.cli.mcp_cmds.cmd_test`: passes `scratch_dir=` through too,
        for parity with the other two `McpManager(` construction sites.
      - `aida.ui.qt.settings_dialog.SettingsDialog`: new "Scratchpad
        folder:" row (line edit + Browse…), mirroring "Records folder:"
        exactly, including in `updated_app_config()`.
      - `aida.ui.qt.main_window.MainWindow`: new File-menu "Open Scratch
        Folder" action, mirroring "Open Records Folder" exactly — the
        user-requested one-click way to find and periodically clean the
        folder out.
      - Tests: `tests/test_paths.py` (3 new), `tests/test_settings.py` (1
        new), `tests/test_context.py` (2 new), `tests/test_mcp_server.py`
        (4 new, including a new `get_cwd` tool on the real mock MCP
        subprocess proving `cwd=` actually reaches the spawned process,
        not just the constructor), `tests/test_mcp_manager.py` (3 new,
        proving the wiring survives the manager layer end to end),
        `tests/ui/test_settings_dialog.py` (6 new), `tests/ui/
        test_main_window.py` (1 new). Full suite: 1237 passed (up from
        1216), same one known pre-existing chmod-as-root failure in
        `test_knowledge_ingest.py`, `ruff check .` clean.

- [x] **(B11) Code Editor had no way in from a generated file.** User
      report: "we have added code editor, but when I ask agent to write a
      code, agent writes correctly py file into target folder - perfect.
      But then when I try to open, it opens in system (text) editor. And
      there is no way to open the generated code in the Aida code editor
      and run from there or test it... Code editor has save and save as
      buttons, no open button." Two separate gaps, both real: (1)
      ``FileArtifactCard`` (the card a ``write_file`` tool call already
      shows in the chat transcript — this part was already working) only
      offered "Open" (``QDesktopServices`` → whatever the OS's own default
      app is for ``.py``, a plain-text editor on most systems) and
      "Reveal" — no path from there into AIDA's own ``CodeEditorDialog``
      at all. (2) ``CodeEditorDialog`` itself only had Save/Save As/Run/
      Kill — no way to *load* an existing file into it either, agent-
      written or otherwise, short of retyping/pasting its contents.

      **Done (2026-08-23):** both gaps closed.
      - ``FileArtifactCard`` (``artifact_widgets.py``) gained a third
        button, "Open in Code Editor", shown only for ``.py`` files (the
        dialog itself is Python-specific — syntax highlighting, Run/Kill
        via ``run_python_script``) — a new ``CODE_EDITOR_SUFFIXES``
        constant, so widening it to other extensions later is a one-line
        change. Clicking it emits a new ``open_in_code_editor_requested``
        signal carrying the file's path, relayed up through ``ChatPanel``
        (mirroring the existing ``code_editor_requested`` relay a chat
        message's own fenced-code-block "Open in Editor" button already
        used) at both places a card is built — the live
        ``FileArtifactCreated`` event path and ``artifact_widget_for``
        (resumed-conversation history), so a card opened from a past
        session works identically to one from the live turn that just
        wrote it.
      - ``CodeEditorDialog`` gained an ``initial_path`` constructor
        parameter (opens the *real* file — reads it from disk, sets
        ``current_path``, so Save/Run act on that file directly rather
        than a disconnected copy of its text, unlike ``initial_text``\'s
        existing blank-editor/paste-a-code-block cases) and a new "Open…"
        button (``QFileDialog.getOpenFileName``, mirroring "Save As…"\'s
        own file-dialog pattern) so a file can be loaded into the dialog
        directly, independent of any chat card, addressing "no open
        button" literally.
      - ``MainWindow.open_code_editor_dialog`` gained a matching
        ``initial_path`` parameter, and a new ``_on_open_in_code_editor_
        requested`` handler wired to ``ChatPanel.open_in_code_editor_
        requested`` (alongside the existing ``code_editor_requested``
        connection) completes the chain from a chat file card's button to
        a real, editable, runnable file in the dialog.
      - Tests: `tests/ui/test_artifact_widgets.py` (3 new: button shown
        only for ``.py``, absent for other extensions, click emits the
        real path), `tests/ui/test_chat_panel.py` (2 new: the relay from
        both the live ``FileArtifactCreated`` path and the resumed-history
        ``artifact_widget_for`` path), `tests/ui/test_code_editor_dialog.py`
        (4 new: seeding from ``initial_path``, Save writing back to that
        same file, "Open…" loading a chosen file, cancelling "Open…"
        leaves the editor untouched), `tests/ui/test_main_window.py` (1
        new end-to-end case: a real ``write_file`` tool call through
        ``MockProvider`` → file card → "Open in Code Editor" click → the
        dialog opens at the real generated file with the real content).
        Full suite: 1247 passed (up from 1237), same one known
        pre-existing chmod-as-root failure, `ruff check .` clean.

- [x] **(B12) Conversations sidebar: empty rows, no right-click menu, no
      multi-select.** User request, three parts. (1) "Let's not add in
      this list (or remove automatically when new conversations is
      created) conversations which have no messages in them. Currently
      there are conversations which are empty (were created on start or
      workspace change and never used). At this time they are called
      (untitled)." Root cause: ``ChatSession``'s ``ConversationRecorder``
      unconditionally calls ``store.create_conversation(...)`` at
      session-*start* time — before any message exists — so every app
      launch, workspace switch, "New Chat", and Resume left the
      *previous* conversation behind as a permanent empty row the moment
      the user didn't type anything into it. (2) "Add meaningful (one
      conversation action) button functions to the right click (rename,
      resume, delete)" — the sidebar had Resume/Rename…/Delete…/Clean Up…
      buttons but no context menu at all. (3) "Enable multiple file
      selection (usual shift click to select range and ctrl/cmd click to
      select specific ones) useful for deleting multiple chats" — the
      list was ``SingleSelection`` only.

      **Done (2026-08-24):** all three.
      - (1) Two layers, matching the user's own "not add... or remove
        automatically" framing. Display: ``ConversationsSidebar.
        set_conversations`` now filters to ``message_count > 0`` —
        ``ConversationSummary`` already carried this field (used by U5's
        search box), so this retroactively hides every already-
        accumulated empty row too, no DB migration needed. Cleanup:
        ``MainWindow`` gained ``_delete_conversation_if_empty(
        conversation_id)`` (opens a fresh ``ConversationStore``,
        deletes via the existing ``aida.persistence.cleanup.
        delete_conversation`` if and only if ``message_count == 0``) and
        a guarded ``_active_conversation_id(bridge)`` helper (``bridge.
        session``/``session.recorder`` are both optional). Called from
        ``_restart_session`` right after the old bridge's ``shutdown()``
        (covers workspace switch, New Chat, and Resume — all three route
        through it) and from ``closeEvent`` (quitting the app on an
        untouched conversation shouldn't leave it behind either).
      - (2) ``ConversationsSidebar``'s list gained a
        ``CustomContextMenu``: right-click on a row already part of the
        current selection acts on that whole selection; right-click
        elsewhere selects just that row first (standard file-manager
        behavior). One conversation selected offers Resume/Rename…/
        Delete…; more than one offers Delete… only (Resume/Rename don't
        make sense for several at once) — split into a testable
        ``_build_context_menu()`` plus a tiny ``_popup_context_menu()``
        wrapper around the actual ``QMenu.exec()`` call, since a compiled
        Qt slot method can't be reliably monkeypatched in a test (a first
        attempt at testing this directly hung the suite on a real modal
        menu waiting for mouse input).
      - (3) Selection mode changed from ``SingleSelection`` to
        ``ExtendedSelection`` (Qt's own built-in shift-range/ctrl-toggle
        behavior — no custom mouse handling needed). A new
        ``selected_conversation_ids()`` (plural) backs bulk actions;
        Delete now branches on selection size — one conversation still
        emits the existing ``delete_requested`` signal unchanged (every
        prior connection/test stays valid), several emit a new
        ``delete_many_requested`` signal, handled by a new ``MainWindow.
        _on_delete_many_requested`` that loops and refreshes the sidebar
        once at the end, mirroring ``_on_cleanup_requested``'s existing
        shape.
      - Tests: `tests/ui/test_conversations_sidebar.py` (17 new: empty-
        conversation filtering, ``ExtendedSelection`` is actually
        configured, ``selected_conversation_ids()``, bulk vs. single
        delete signal routing, context-menu contents for one vs. several
        selected, right-click selection-fixup both when the clicked row
        is and isn't already selected, right-click on empty space is a
        no-op), `tests/ui/test_main_window.py` (7 new: the sidebar never
        shows the conversation ``MainWindow.__init__`` creates up front,
        New Chat/workspace-switch/window-close each delete an untouched
        conversation but never one with real messages, bulk delete
        end-to-end). Full suite: 1266 passed (up from 1247), same one
        known pre-existing chmod-as-root failure, `ruff check .` clean.

Deliberately *not* recommended right now: swapping the RAG store for a vector
DB, adopting an agent framework, or a web frontend — the plan's original
reasoning still holds, and nothing in this review contradicts it.

---

## 3. Front-end / configuration UX (the "cumbersome config" fixes)

Today the GUI can manage MCP servers and knowledge bases fully, but the two
config objects *everything else depends on* — provider profiles and workspaces —
can only be created by hand-editing YAML or via CLI flags. Even AIDA's own
dialogs point users at files ("Configure an embedding profile in providers.yaml
first"). Closing this gap is the biggest usability win available.

- [x] **(U1) Workspace editor dialog** — the top item. A "Workspaces…" dialog
      following the exact `McpManagementDialog` pattern (list left; Add/Edit/
      Remove; form dialog): name, profile (dropdown of configured profiles),
      source folders (reuse the row widgets), target folder, sidecar name,
      mcp_group (dropdown of `known_group_names` + "none"), skills
      (checkbox list from `list_skills`), knowledge bases (checkbox list),
      safety mode (with the existing relaxed-mode warning via
      `relaxed_mode_warning_if_newly_enabled`), system prompt (text or .md file
      picker), scripting on/off, interpreter, command allowlist. All the
      backend pieces exist (`save_workspace`, `delete_workspace`,
      `validate_workspace` for inline warnings) — this is purely assembly.
      With this in place, a new user can go from empty config to a working
      pyIrena workspace without opening a text editor.
      **Done (2026-08-22):** new `aida.ui.qt.workspace_management_dialog`
      module — `WorkspaceFormDialog` (all fields from the plan; folder lists
      use the KB dialog's one-per-line `QPlainTextEdit` precedent rather than
      `FolderDisplay`, which is tightly coupled to live session state) and
      `WorkspaceManagementDialog` (list left, Add/Edit/Remove, details panel
      showing `validate_workspace` warnings). No `ChatBridge` needed — purely
      config CRUD, persisted immediately via `save_workspace`/
      `delete_workspace`. Wired into `MainWindow` behind a new "Workspaces…"
      toolbar action. Tests: `tests/ui/test_workspace_management_dialog.py`
      (17 cases: form seeding/defaults/round-trip, skills/KB checkbox
      round-trip, mcp_group population, blank-name rejection, relaxed-mode
      warning blocking save on Cancel, add/edit/remove persistence to disk,
      duplicate-name rejection, validation warnings shown in the details
      panel).

- [x] **(U2) Provider & embedding profile editor** — second priority. Extend
      the Settings dialog (or a dedicated "Providers…" dialog): Add/Edit/Remove
      for both `profiles` and `embedding_profiles` (name, kind, base_url,
      model, capability notes, plus the new sampling/cost fields from B2), a
      secret field that writes to the OS keychain via `set_secret` (never to
      YAML), and a "Test" button reusing `validate_profile` /
      `validate_embedding_profile` on the background loop (they're already
      async and never raise). This also fixes the dead-end where the KB dialog
      refuses to proceed until the user hand-edits providers.yaml.
      **Done (2026-08-22):** new `aida.ui.qt.profiles_dialog` module — a
      `ProfilesDialog` (QTabWidget, one tab per config object) with
      `ProviderProfileFormDialog`/`EmbeddingProfileFormDialog` covering every
      field named above, including the new B2 sampling/cost fields. The
      secret field is write-only (blank = keep existing, exactly like a
      "change password" form) and goes straight to `set_secret` — never to
      `providers.yaml`. "Test" reuses `ChatBridge` exactly like the existing
      MCP "Test Connection" button: two new signal/method pairs
      (`validate_provider_profile`/`profile_validated` and
      `validate_embedding_provider_profile`/`embedding_profile_validated`)
      mirroring `test_mcp_connection`/`mcp_connection_tested`, so validation
      runs on the background loop and never blocks the Qt thread. Also fixed
      the KB dialog's dead end: `_on_add()` with zero embedding profiles now
      offers to open this dialog directly instead of a bare warning. Wired
      into `MainWindow` behind a new "Providers…" toolbar action. Tests:
      `tests/ui/test_profiles_dialog.py` (20 cases) plus 3 new bridge tests
      in `tests/ui/test_bridge.py` and 2 rewritten KB-dialog tests covering
      the Yes/No paths of the new offer.

- [x] **(U3) Settings dialog completeness.** Expose the AppConfig fields that
      currently require hand-editing: `default_safety_mode`, global
      `allowed_folders`, global `command_allowlist`, `max_context_tokens`.
      Remove or implement `theme` — it is stored and round-tripped but nothing
      ever reads it (dead setting; confusing if a user sets it by hand).
      **Done (2026-08-22):** `SettingsDialog` gained a safety-mode combo,
      multi-line folder/command-allowlist editors, and a `max_context_tokens`
      spin box (0 = "Disabled (no trimming)", matching `AppConfig`'s own
      documented sentinel). Changing the global default into `relaxed` now
      surfaces the same `relaxed_mode_warning_if_newly_enabled` warning used
      for a single workspace's safety field, wired through
      `MainWindow.open_settings_dialog`. `theme` was removed rather than
      implemented — real Qt light/dark theming is a much larger change than
      this item's scope, and `AppConfig`'s existing "ignore unknown YAML
      keys" coercion means an old `config.yaml` with a stray `theme: dark`
      line keeps loading fine. Tests: 5 new cases in
      `tests/ui/test_settings_dialog.py` (seeding, editing/round-trip, the
      max-tokens-zero sentinel, and a regression guard that `theme` stays
      gone) plus updated cases in `tests/test_settings.py`.

- [x] **(U4) First-run experience.** On launch with no profiles configured,
      show a small onboarding panel instead of the bare "No profile given"
      failure dialog: run the doctor checks, then offer "Add a provider
      profile…" (U2) → "Create a workspace…" (U1). One afternoon once U1/U2
      exist, and it converts the worst first impression into a guided path.
      **Done (2026-08-22):** new `aida.ui.qt.onboarding_dialog.OnboardingDialog`
      — runs `run_checks()` (exception-safe: a `run_checks` crash shows a
      "could not run environment checks" message instead of taking the
      dialog down with it), then offers "Add a Provider Profile…" (opens
      `ProfilesDialog`) and "Create a Workspace…" (opens
      `WorkspaceManagementDialog`, disabled until at least one profile
      exists). `MainWindow._on_startup_failed` now checks
      `self.settings.providers.profiles`: genuinely empty triggers this
      onboarding panel instead of the old bare critical dialog (any other
      startup failure with at least one profile configured still gets the
      plain critical dialog, unchanged). After onboarding closes, if a
      profile now exists, `_restart_session` is retried automatically.
      Tests: `tests/ui/test_onboarding_dialog.py` (6 cases) plus 2 new/split
      cases in `tests/ui/test_main_window.py` covering both branches of
      `_on_startup_failed`.

- [x] **(U5) Conversations sidebar polish.** Row labels currently start with a
      raw UTC ISO timestamp (`2026-08-22T14:03:22.123456+00:00 …`). Format as
      local short date/time (`Aug 22 09:03`), and add a small filter/search box
      above the list (title substring match is enough) — the list grows fast in
      real use.
      **Done (2026-08-22):** `ConversationsSidebar` gained `_format_timestamp`
      (parses the stored UTC ISO string, converts to the viewer's local
      timezone, formats as e.g. "Aug 22 09:03"; falls back to the raw string
      on anything unparseable) used in `_row_label`, and a live search
      `QLineEdit` above the list. Filtering is case-insensitive substring
      match against title, re-applied via `_apply_filter` every time
      `set_conversations` is called — so a refresh (resume/delete/rename/
      cleanup all call it) re-applies whatever the user already typed rather
      than silently clearing the search box. Tests: 7 new cases in
      `tests/ui/test_conversations_sidebar.py`.

- [x] **(U6) Better resumed-conversation rendering.** `load_history` renders
      every `role="tool"` message as a full text bubble, so a resumed analysis
      session replays as a wall of raw tool output, and images are appended at
      the very end rather than in place. Two steps: (a) render resumed
      tool messages as collapsed `ToolCallRow`-style rows (the pairing info —
      `tool_call_id`, `name` — is already persisted); (b) add a `seq`/message
      anchor to artifact records at write time so resumed images can interleave
      at their original positions. (a) is easy and delivers most of the value.
      **Done (2026-08-22):** both steps.
      (a): a resumed `role="tool"` message now renders as the same
      `ToolCallRow` the live path uses, via a new `ToolCallRow.mark_historic`
      (neutral "•" marker and no elapsed time, since `is_error`/duration
      were never persisted on `Message`) — `ChatPanel.load_history` recovers
      each call's original arguments by scanning the matching assistant
      message's `tool_calls`. A tool-call-only assistant turn (no text) now
      also produces no bubble, mirroring the live `TextStarted`-deferred
      behavior.
      (b): schema v2 adds `artifacts.seq` (nullable — old rows and any
      future caller that doesn't know the owning message's seq yet just
      fall back to v1's "append at the end" behavior). `ChatSession` tags
      each recorded artifact with `ConversationRecorder.next_message_seq()`
      — the tool-result message an `ImageArtifactCreated`/
      `FileArtifactCreated` event belongs to hasn't been persisted yet at
      the point the event arrives (`AgentLoop.run` yields the artifact
      event first), so this is the seq that message is *about* to receive,
      computed live from the DB (`ConversationStore.next_seq`) rather than
      assumed from in-memory bookkeeping — correct even after
      `repair_tool_call_pairing` edits a resumed history. `MainWindow.
      _load_resumed_history` replaces the old `_load_resumed_artifacts`:
      queries `store.load_messages_with_seq` + `store.load_artifacts`
      directly (rather than the in-memory, possibly-repaired
      `session.messages`) and hands both to `ChatPanel.load_history`'s new
      optional `seqs`/`artifacts_by_seq` parameters to interleave; any
      artifact with no seq is still appended after the whole transcript,
      same as v1. Tests: `tests/test_persistence_db.py` (a hand-built v1
      DB migrates to v2 without data loss), `tests/test_persistence_store.py`
      / `tests/test_persistence_recorder.py` (seq round-trips, `next_seq`/
      `next_message_seq`), `tests/test_chat_cli.py` (two end-to-end cases
      proving the recorded artifact's seq matches the tool message's actual
      seq), `tests/ui/test_tool_call_widget.py` (`mark_historic`),
      `tests/ui/test_chat_panel.py` (7 new cases: collapsed tool rows,
      unmatched call_id, seq interleaving, missing-file skip, backward
      compat with no seqs given, `artifact_widget_for`), and the existing
      `test_resume_conversation_redisplays_prior_image_artifact` in
      `tests/ui/test_main_window.py` (real mock-mcp subprocess) continues
      to pass unchanged, now exercising the seq-based path end to end.

- [x] **(U7) Small paper cuts.**
      - `capability_notes` is stored but shown nowhere; display it in the
        profile selector tooltip / Settings list so the "small local model —
        prefer lean MCP groups" hints the config format was designed for are
        actually visible.
      - `_restart_session` blocks the Qt thread up to 5 s
        (`ChatBridge.shutdown` waits synchronously); acceptable, but show a
        busy cursor / "Closing previous session…" status message so the freeze
        reads as intentional.
      - A menu bar (File/Help) with "Open config folder", "Open records
        folder", "Documentation", "About" — cheap discoverability for exactly
        the folders users otherwise have to find by hand.
      **Done (2026-08-22):** all three.
      `ProfileSelector.set_profiles` gained an optional `capability_notes`
      map setting each combo entry's tooltip (`Qt.ItemDataRole.ToolTipRole`);
      `SettingsDialog`'s read-only profile list (`_profile_rows`) appends
      `" — <capability_notes>"` when set. `MainWindow._restart_session` now
      shows a status message + `QApplication.setOverrideCursor(Qt.
      CursorShape.WaitCursor)` around the blocking `old_bridge.shutdown()`
      call, restored in a `finally`. A new `_build_menu_bar` adds File
      ("Open Config Folder", "Open Records Folder") and Help
      ("Documentation" → the repo's GitHub URL, "About AIDA" → a
      `QMessageBox.about` with the installed version) — the app previously
      had no menu bar at all. Tests: `tests/ui/test_selectors.py` (2 new),
      `tests/ui/test_settings_dialog.py` (1 new), and 6 new cases in
      `tests/ui/test_main_window.py` (busy cursor set/restored around a
      real `New Chat` restart, capability_notes tooltip wiring, menu
      presence, and each of the four menu actions).

- [x] **(U8) MCP Groups dialog had no way to create a group.** A server's own
      edit form lets you add it to a new group name, but the standalone
      "MCP Groups" dialog only had Rename/Delete — with several servers to
      put in one new group, that meant opening each server's form
      individually and typing the same new group name into each one. A
      group has no separate registry (`aida.mcp.groups`'s design: purely
      derived from which servers reference it), so there was never a
      not-yet-wired "create" path to find — it needed building.

      **Done (2026-08-22):** new `aida.mcp.groups.add_group(mcp_config,
      name, server_names)` adds a group name to each named server's
      `groups` list (skips a server that already has it or an unknown
      name, mirroring `rename_group`/`delete_group`'s existing
      no-op/skip conventions). The Groups dialog gained an "Add Group…"
      button opening a small picker (`_AddGroupDialog`): a name field plus
      a checklist of every configured server, OK disabled in spirit until
      at least one is checked (enforced on accept with a warning dialog).
      Picking a name that already exists asks to confirm before adding the
      selected servers to it rather than silently merging. Tests:
      `tests/test_mcp_groups.py` (5 new cases for `add_group` itself),
      `tests/ui/test_mcp_management_dialog.py` (3 new cases: creates a
      group, cancel makes no changes, no-servers-configured warns instead
      of opening the picker).

      **CLI parity (2026-08-22):** the CLI had the identical gap (`aida mcp
      group` only had `list`/`rename`/`delete`) — adding a new
      `cmd_group_add` handler plus `aida mcp group add <name> --servers
      s1,s2` parser wiring keeps the CLI and GUI at the same capability
      level rather than leaving the docs to describe an asymmetric feature
      set. Unknown server names are rejected outright (not silently
      skipped, unlike `add_group` itself) since a CLI invocation has no
      picker UI to catch a typo before it's submitted. Tests: 5 new cases
      in `tests/test_mcp_cmds.py`.

- [x] **(U9) Documentation sync.** `docs/*.md` had fallen behind several
      features already shipped this cycle: B4's concurrent MCP startup,
      B5's `script_timeout_seconds`, B6's `keyring:`/`secret:` env secrets,
      U8's Add Group flow, the McpQuickPanel's live start/stop checkboxes
      (superseding the older read-only fix described in §1), the
      "Workspace permissions" panel rename, the context-trim status bar
      message, and the Providers…/Workspaces… toolbar actions from U1/U2.
      **Done (2026-08-22):** swept all 9 files in `docs/`. Updated:
      `mcp-servers.md` (new "Storing secrets in the OS keychain" section,
      B4 concurrency note, Groups section documents `aida mcp group add`
      and the GUI's Add Group… button), `workspaces.md` (`script_timeout_seconds`
      row + the fact it has no CLI flag, Workspaces… dialog documented),
      `coding-and-scripting.md` (new `script_timeout_seconds` section:
      default, kill behavior, per-call cap), `gui-overview.md` ("Workspace
      permissions" label, McpQuickPanel's live-control behavior, the
      transient context-trim status message, Providers…/Workspaces…
      toolbar entries), `installation.md` (pip fallback command was
      missing the `docs` extra). `README.md`, `knowledge-bases.md`,
      `providers-and-secrets.md`, and `safety-and-permissions.md` were
      checked line-by-line against their corresponding source and found
      already accurate — no changes made to those four.

---

## 4. Suggested schedule (1–2 weeks, in dependency order)

| Day(s) | Work |
|---|---|
| 1 | All of §1 (verified bugs) — each is small and independently testable |
| 2–3 | B2 (profile sampling/cost fields) then U2 (profile editor GUI) — B2 first because U2's form wants the new fields |
| 4–5 | U1 (workspace editor dialog) + U3 (settings completeness) + U4 (first-run path) |
| 6–8 | B1 (vision input) — providers first with unit tests on the translation functions, then the agent-loop attachment policy, then GUI/CLI attachment of image files |
| 9 | B3 (prompt caching) + B4 (parallel MCP startup) + B5 (script timeout) |
| 10 | U5/U6/U7 polish; B6 (mcp.json secrets) and B7 (trim visibility) as time allows; B8 (session module extraction) is a good rainy-day refactor any time |

Working agreement reminder (PLAN.md §11): each of these should land with its
checkbox ticked here in the same commit, and anything that changes a §2
decision in PLAN.md gets a dated note there.

---

## 5. Notes for later (not this cycle)

- Phase 10 (`aida run`, stored workflows, scheduler, PyPI release automation)
  remains the open phase; nothing in this review changes its plan.
- Retrieval loads every chunk vector into memory per query — fine at current
  corpus sizes, revisit only if a KB grows past ~10k chunks (the plan's own
  escape hatch).
- `AnthropicProvider.ping()` sends a real 1-token paid message per doctor run /
  profile validation; harmless at this scale, but a cheaper reachability check
  could replace it if doctor runs become frequent or Argo meters requests.
- Tool calls within a turn execute sequentially; parallel fan-out ("plot all
  of these") would speed UC3/UC4 but complicates cancellation and event
  ordering — only worth it if it becomes a felt bottleneck.
- Two AIDA instances sharing `~/.aida` (SQLite + config writes) is unguarded;
  single-user assumption is fine for now, but a simple lock file would make the
  failure mode explicit if it ever comes up.
- **(2026-08-23, from user question) Preinstall Node/npx + offer common
  `npx`-based MCP servers (Playwright, etc.) at install time.** Right now a
  user who wants an `npx`-launched MCP server (e.g. `npx -y
  @playwright/mcp@latest`) has to have Node/npx on their machine already —
  nothing in AIDA's own install path provides it. Two independent pieces:
  (a) **Node itself**: `environment.yml`'s conda path could add a `nodejs`
  dependency from conda-forge, so `conda env create` gives you `npx`
  alongside AIDA for free; the plain `pip install -e ".[dev,gui,docs]"`
  fallback has no equivalent (pip can't install Node), so that path would
  still need Node installed separately, or `aida doctor` could at least
  detect its absence and say so. (b) **Offering specific servers**: an
  `npx`-based server doesn't need a real "install" step beyond the config
  entry — `npx -y <pkg>` downloads and caches the package on first launch —
  so "preconfigured" mostly means shipping a ready-to-enable config entry
  (e.g. in `examples/config/mcp.json`) or a one-click "Add browser
  automation (Playwright)" in the onboarding dialog (U4) / doctor output,
  reusing the existing `aida mcp server add`/`import` path. Deliberately
  **not** auto-enabled with no user action: an MCP server is code AIDA
  will execute on the user's machine, and everything else in AIDA's design
  (secrets, confirm-before-run) requires an explicit opt-in — this should
  be a one-click *offer*, not something that ships pre-turned-on. Also
  worth remembering: first real launch still needs network access to fetch
  the npm package unless the npx cache is pre-warmed during install, so
  "preinstalled" isn't quite "works fully offline on first run" without
  that extra step.

---

## 6. Known issues

### CI crashed (segfault on Linux, access violation on Windows) mid-`pytest -v` (2026-08-22)

**Report:** CI's `pytest -v` step (the whole suite, `tests/` and `tests/ui/`
together, in one process) crashed the interpreter — a native crash, not a test
failure — at a different call site each time: Linux inside
`persistence/db.py`'s sqlite3 migration during `ChatBridge._start`, Windows
inside `artifacts_dir()`'s `Path.mkdir`, and locally (reproduced repeatedly)
inside Qt's own `QCoreApplication::notifyInternal2`/
`QTimerInfoList::activateTimers`. Always several hundred tests into the run,
never in a small subset run alone.

**Diagnosis:** every GUI test in `tests/ui` shares one process-wide
`QApplication` (Qt disallows more than one), and `MessageBubble`
(`aida.ui.qt.chat_panel`) parents a single-shot `QTimer` to itself to coalesce
streamed-text renders. None of the ~50+ tests that build a real `MainWindow`
tear it down via `deleteLater()` — most just let the local variable go out of
scope at the end of the test function. Python's *cyclic* garbage collector
doesn't run on every refcount drop, only once its generational allocation
thresholds are crossed, so a `MainWindow`'s widget tree (including any
`MessageBubble` with a still-active `_render_timer`) can survive, unreferenced
but uncollected, for many tests before finally being reaped — possibly mid a
*later, unrelated* test's `qapp.processEvents()` call. A `QTimer` whose owning
QObject is destroyed at that moment can still have a pending entry in Qt's
internal timer list; the next tick delivers the timeout event to freed
memory — a native crash, invisible to Python's own exception handling, which
is why it surfaces as a segfault/access violation rather than a clean test
failure. This explains every observed property: needs a *large* combined
volume of tests to reproduce (crossing the GC threshold), never reproduces in
a small file subset, and the actual crash site is wherever the interpreter
happens to be executing when the stale timer fires — unrelated to whatever
code is "blamed" in the traceback.

Reproduced locally with `gdb` attached to a full `pytest -v` run (3/3
crashes, always at the same test given the same execution order) — see the
transcript referenced in this session's delivery notes for the full native
backtrace confirming the crash address is inside Qt's timer dispatch on the
main thread, not anywhere in `aida`'s own code.

**Fix (2026-08-22):**
1. `tests/ui/conftest.py` gained an autouse `_drain_qt_garbage_after_each_test`
   fixture: `gc.collect()` + `qapp.processEvents()`, twice, after every GUI
   test. This forces each test's own garbage to be reaped (and any
   `deleteLater()`-queued deletions to actually run) right after that test,
   in a window where nothing else is mid-dispatch, instead of leaving it to
   float until the cyclic collector's next arbitrary run. Verified: 3/3 full
   `pytest -v` runs crashed before this fix, 2/2 passed cleanly after it
   (only the pre-existing root-user chmod environment failure in
   `test_knowledge_ingest.py` remains).
2. `MessageBubble` gained `stop_pending_render()`, called from
   `ChatPanel.clear()` before `deleteLater()` — belt-and-suspenders: stops the
   coalescing timer explicitly wherever a bubble is deliberately retired,
   on top of the test-side mitigation above.
3. `.github/workflows/ci.yml`'s Pytest step split into two invocations
   (`pytest -v --ignore=tests/ui` then `pytest -v tests/ui`) — halves the
   cumulative Qt/thread churn either process accumulates, as a second,
   independent safety margin on top of (1).

**Not fully closed:** this is a mitigation with strong empirical support, not
a mathematical guarantee — the underlying mechanism (deferred GC racing Qt's
timer dispatch) is a known class of PySide/PyQt issue, not something `aida`
can fully rule out from application code alone. If it recurs as the test
suite keeps growing, the next escalation is running each `tests/ui/*.py` file
in its own subprocess (e.g. `pytest-forked` or one `pytest` invocation per
file) so no single process ever accumulates enough GUI-test garbage to matter.
