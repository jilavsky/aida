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

---

## 2. Backend improvements (highest value first)

- [ ] **(B1) Vision input — let the model actually see the plots.** Today an
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

- [ ] **(B2) Per-profile sampling settings + honest cost.** PLAN.md §4 promised
      "a profile = … sampling defaults", but `ProviderProfile` has no
      `max_tokens`/`temperature`, and `ChatSession` builds
      `CompletionSettings(model=…)` with hardcoded temperature 0.7 and
      max_tokens None (→ Anthropic's 4096 default). Add optional
      `max_tokens`, `temperature`, and `usd_per_m_input`/`usd_per_m_output`
      fields to `ProviderProfile`; thread them into `CompletionSettings` and
      `estimate_cost_usd` (which currently uses one fixed rate for every
      provider — misleading when the active profile is a free local model).
      Small change, removes two real annoyances at once.

- [ ] **(B3) Anthropic prompt caching.** Every turn resends the whole system
      block — workspace context + skills + pyirena-mcp's long `instructions` —
      plus the full tool schema list (100+ tools namespaced). Adding
      `cache_control: {"type": "ephemeral"}` markers on the system prompt and
      the tools array in `to_anthropic_params`/`complete()` is ~20 lines and
      typically cuts input-token cost by 5–10× for multi-turn tool-heavy
      sessions (exactly the UC3/UC4 pattern). Report `cache_read_input_tokens`
      in `UsageInfo` so the savings are visible.

- [ ] **(B4) Parallel MCP server startup.** `McpManager.start_all` launches
      servers sequentially; with 2–3 servers at up to 30 s startup timeout each,
      worst-case session start is additive. `asyncio.gather` over handles keeps
      the same failure isolation (each start already catches `McpServerError`)
      and makes the common case as fast as the slowest server.

- [ ] **(B5) Configurable script/command timeout.** `run_python_script`/
      `run_command` are pinned to `DEFAULT_RUN_TIMEOUT_SECONDS = 30` — a real
      reduction/analysis script will blow through that. Add
      `WorkspaceConfig.script_timeout_seconds` (default 30) and an optional
      `timeout` argument in the two tool schemas (capped by the workspace
      value), so the model can ask for more when the task warrants it.

- [ ] **(B6) Secrets in `mcp.json` env blocks.** MCP server `env` values (e.g. a
      Tavily/Brave API key for the search server) are stored in plaintext
      `mcp.json` — the one remaining hole in the "secrets never touch
      YAML/JSON" rule. Support a `keyring:NAME` (or `secret:NAME`) value syntax
      resolved at launch time in `McpServerHandle.start()` via
      `aida.config.secrets`, and let the GUI's env editor offer "store value in
      keychain" instead of masking-only.

- [ ] **(B7) Context-budget visibility.** Trimming works but is invisible
      (logged only), and `estimate_tokens` ignores `tool_calls` arguments, so
      the estimate skews low in tool-heavy sessions. Two small steps: count
      tool-call argument JSON in `estimate_tokens`, and emit a
      `ContextTrimmed(dropped_turns, estimated_tokens)` event that the CLI
      prints and the GUI shows in the status bar. This continues the
      "no black boxes" thread from the cost work.

- [ ] **(B8) Move `ChatSession`/`start_session` out of `aida.cli`.** The Qt
      bridge importing `aida.cli.chat` is the one place the layering reads
      wrong (`ui → cli`). Mechanically extract the session engine
      (`ChatSession`, `start_session`, the error types) into
      `aida.core.session` (or `aida.session`) and have `aida.cli.chat` and
      `aida.ui.qt.bridge` both import from there; keep re-exports in
      `aida.cli.chat` so nothing external breaks. Not urgent, but it makes the
      contract test honest and helps every future frontend (Phase 10's
      `aida run` will want the same engine, too).

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

- [ ] **(U1) Workspace editor dialog** — the top item. A "Workspaces…" dialog
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

- [ ] **(U2) Provider & embedding profile editor** — second priority. Extend
      the Settings dialog (or a dedicated "Providers…" dialog): Add/Edit/Remove
      for both `profiles` and `embedding_profiles` (name, kind, base_url,
      model, capability notes, plus the new sampling/cost fields from B2), a
      secret field that writes to the OS keychain via `set_secret` (never to
      YAML), and a "Test" button reusing `validate_profile` /
      `validate_embedding_profile` on the background loop (they're already
      async and never raise). This also fixes the dead-end where the KB dialog
      refuses to proceed until the user hand-edits providers.yaml.

- [ ] **(U3) Settings dialog completeness.** Expose the AppConfig fields that
      currently require hand-editing: `default_safety_mode`, global
      `allowed_folders`, global `command_allowlist`, `max_context_tokens`.
      Remove or implement `theme` — it is stored and round-tripped but nothing
      ever reads it (dead setting; confusing if a user sets it by hand).

- [ ] **(U4) First-run experience.** On launch with no profiles configured,
      show a small onboarding panel instead of the bare "No profile given"
      failure dialog: run the doctor checks, then offer "Add a provider
      profile…" (U2) → "Create a workspace…" (U1). One afternoon once U1/U2
      exist, and it converts the worst first impression into a guided path.

- [ ] **(U5) Conversations sidebar polish.** Row labels currently start with a
      raw UTC ISO timestamp (`2026-08-22T14:03:22.123456+00:00 …`). Format as
      local short date/time (`Aug 22 09:03`), and add a small filter/search box
      above the list (title substring match is enough) — the list grows fast in
      real use.

- [ ] **(U6) Better resumed-conversation rendering.** `load_history` renders
      every `role="tool"` message as a full text bubble, so a resumed analysis
      session replays as a wall of raw tool output, and images are appended at
      the very end rather than in place. Two steps: (a) render resumed
      tool messages as collapsed `ToolCallRow`-style rows (the pairing info —
      `tool_call_id`, `name` — is already persisted); (b) add a `seq`/message
      anchor to artifact records at write time so resumed images can interleave
      at their original positions. (a) is easy and delivers most of the value.

- [ ] **(U7) Small paper cuts.**
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
