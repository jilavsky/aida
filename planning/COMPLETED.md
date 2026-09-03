# AIDA — completed work (through 0.1.0b1, 2026-08-28)

Everything below is **done, in the code, and covered by the test suite**
(1319 tests green on macOS/Windows/Linux, ruff clean). It was moved out of
`PLAN.md` when that file was cut down to open work only.

Where to look for detail:

| Document | What it holds |
|---|---|
| [`DESIGN.md`](DESIGN.md) | The enduring design: objective, decisions table, architecture + hard rules, config model, safety model, documents, testing philosophy, dependency policy, distribution. Formerly `PLAN.md` §§1–9. |
| `phase01…phase09_*.md` | The original per-phase checklists, still ticked box by box. |
| [`improvement_plan_2026-08.md`](improvement_plan_2026-08.md) | The full-codebase review of 2026-08-22 and the 35 items it produced, each with a dated "Done" note explaining what was actually changed and which tests cover it. |
| [`local_scientific_ai_agent_proposal.md`](local_scientific_ai_agent_proposal.md) | The original pre-`PLAN.md` proposal, kept for history. |

---

## 1. Phases delivered

### Phase 1 — Foundation (`phase01_foundation.md`)
- Repo scaffold, `pyproject.toml` (hatchling), `environment.yml`, MIT license.
- Config system: `~/.aida/` layout, `config.yaml` / `providers.yaml` /
  `workspaces.yaml` / `mcp.json` / `knowledge.yaml`, `AIDA_HOME` override,
  atomic writes, type-coercing loaders that survive hand-edited YAML.
- Secrets in the OS keychain via `keyring`, with environment-variable override.
- Rotating file logging; `aida doctor` with 8 real checks including live
  provider reachability.
- GitHub Actions CI: ruff + pytest on 3.11/3.13 across macOS, Windows, Linux.

### Phase 2 — Agent core (`phase02_agent_core.md`)
- `OpenAICompatProvider` (Ollama, LM Studio, Unsloth, OpenAI, any compatible
  endpoint) and `AnthropicProvider` (Claude direct and via the ANL Argo proxy),
  both streaming, both translating native tool calls.
- Named provider profiles with per-profile model, sampling settings, vision
  flag, and price; switchable mid-session without losing history.
- The agent loop: streaming text, tool dispatch, typed artifacts, iteration
  cap, user Stop (which answers its own cancelled tool calls so history stays
  valid).
- Event stream (`TextDelta`, `ToolCallStarted`, `ImageArtifactCreated`,
  `ContextTrimmed`, `AgentError`, …) shared by every frontend.
- Context building: system prompt + skills files + generated workspace/coding/
  identity blocks; whole-turn history trimming under a token budget.
- `aida chat` CLI with `/profile`, `/max-iterations`, and friends.
- `MockProvider` for testing the loop with no model.

### Phase 3 — MCP (`phase03_mcp.md`)
- stdio MCP client manager: lazy start, concurrent startup, namespaced tools
  (`server__tool`), per-server failure isolation.
- **The keystone:** an MCP `ImageContent` becomes a real `ImageArtifact` with
  decoded bytes, saved to the artifact store and surfaced as its own event —
  never flattened into text.
- Typed conversion for every content-block kind, plus a documented
  "what the model sees" text policy per artifact type.
- MCP groups (named server sets) and skills linked to servers.
- Each server's own `instructions` from the initialize handshake folded into
  the session context.
- Argument coercion: quoted numbers/booleans a model emits are repaired
  against the tool's own schema before dispatch.
- A `mock-mcp` fixture server exercising text, image, JSON, and error results.

### Phase 4 — Persistence and workspaces (`phase04_persistence_workspaces.md`)
- SQLite (`~/.aida/aida.db`): conversations, messages, tool calls, artifact
  metadata; binary artifacts as files, not blobs.
- Crash-safe recording (each message persisted as it lands), resume, rename,
  delete, cleanup-older-than-N-days, Markdown transcript export to
  `~/Documents/Aida/`.
- Named workspaces bundling profile, source folders, target folder, sidecar
  folder name, MCP group, skills, knowledge bases, safety mode, scripting
  settings, and quick tasks; `aida workspace` CLI.
- Broken resumed history (orphaned tool calls from a crash) repaired on load.

### Phase 5 — GUI (`phase05_gui.md`)
- PySide6 desktop app (`aida-gui`): streaming chat, inline images, tool-call
  widgets, artifact open/reveal, attachments and drag-and-drop.
- Qt/asyncio bridge: one background event loop, every core event re-emitted as
  a Qt signal; core never imports Qt (enforced by a contract test), all Qt
  imports through a single `_qt.py` shim.
- Toolbar workspace/profile switchers, conversations sidebar, status bar with
  live token/cost, first-run onboarding dialog.

### Phase 6 — Documents and safety (`phase06_documents.md`)
- Native workspace tools: list, find, search text, read, write, mkdir, copy,
  move, delete, metadata — all timeout-guarded for slow network mounts.
- Allowed-folders safety model with relaxed/confirm modes, always-confirm
  outside the allowed set, `_trash` instead of hard deletes, one confirmation
  channel shared by CLI prompt and GUI modal.
- Document readers: PDF, DOCX, XLSX, PPTX, CSV, JSON, MD, code, images.
- Writers: Markdown in Obsidian layout (images copied to a sidecar folder and
  linked relatively) and DOCX.

### Phase 7 — MCP management UI (`phase07_mcp_management.md`)
- Full management dialog: add/edit/remove servers, live start/stop/restart,
  connection test with timing, per-tool disable and confirm-before-run, group
  creation and assignment, skills linkage, env vars with keychain storage.
- Diagnostics: tool-call log across servers in true call order, raw result
  inspector, per-layer error messages.
- `aida mcp` CLI including import of a standard `mcp.json`.

### Phase 8 — RAG (`phase08_rag.md`)
- Ingestion (chunking, incremental update, rebuild), plain-SQLite index per
  knowledge base, pure-Python cosine retrieval — no vector DB, as decided.
- Local or cloud embedding profiles configured like LLM profiles.
- Per-workspace knowledge bases, retrieval injected per turn as ephemeral
  context that is never persisted or resent, with a citations widget.
- `aida kb` CLI and a GUI management dialog with index build/update.

### Phase 9 — Coding and scripting (`phase09_coding_scripting.md`)
- Code editor dialog with Python highlighting, save/save-as/run/kill, opened
  pre-filled from a chat code block or a generated file.
- Code templates surfaced to the model as name + docstring.
- `run_python_script` / `run_command` with a per-workspace interpreter,
  configurable timeout ceiling, and a command allowlist that is a genuine
  always-confirm gate.
- `fetch_url` as a stdlib-only always-confirmed native tool; web *search*
  deliberately left to an MCP server rather than a built-in adapter.

---

## 2. Review round of 2026-08-22 → 08-26

A full read of the codebase produced 35 items — all closed. Summarized;
each has a dated "Done" note in `improvement_plan_2026-08.md`.

**Bugs fixed (11):** dead MCP quick-panel checkboxes · silent failed profile
switch · `aida doctor` recommending a nonexistent command · scalar-vs-list
footgun in hand-edited YAML · silently truncated replies · `/max-iterations`
reset by `/profile` · unreadable dark-mode error banner · non-atomic config
writes · KB ingest blocking the shared event loop · workspace selector stuck
on "(no workspace)" · a Windows CI race in the bridge tests. Plus a
native-crash mitigation for Qt timer/GC interaction in long GUI test runs.

**Backend (B1–B15):** vision input so the model can actually see the plots ·
per-profile sampling settings and honest cost · Anthropic prompt caching with
visible cache stats · concurrent MCP startup · configurable script timeouts ·
MCP env secrets in the keychain · context-budget visibility · session engine
moved out of `aida.cli` into `aida.core.session` · MCP argument coercion · a
well-known scratch folder every agent and MCP server writes into · Code Editor
reachable from a generated file · conversations sidebar rework · telling the
model where its own MCP output landed · workspace-scoped Quick Tasks ·
assistant name and personal context in the system prompt.

**Front-end / configuration UX (U1–U9):** workspace editor dialog · provider
and embedding profile editor · settings dialog completeness · first-run
onboarding · sidebar polish · better resumed-conversation rendering · assorted
paper cuts · MCP group creation · a documentation sync pass.

---

## 3. Beta-promotion round (2026-08-28)

Review against `PLAN.md` before declaring beta. Five bugs found and fixed,
each with regression tests:

- **Duplicated tool-result payload.** FastMCP servers (pyirena-mcp included)
  return a structured result twice — serialized into a text block *and* as
  `structuredContent`. AIDA converted both, so every such tool result reached
  the model rendered twice: double the tool-result tokens on the exact path
  where results are largest. An exact duplicate is now dropped;
  genuinely-different structured content still gets through.
  (`aida/mcp/results.py`)
- **`aida workspace edit` silently wiped fields it has no flag for.** It
  rebuilt the config from the flags alone, resetting `quick_tasks` (up to ten
  saved prompt templates) and `script_timeout_seconds` on any unrelated
  one-flag edit. Now carries the existing workspace forward and overrides only
  what was passed. (`aida/cli/workspace_cmds.py`)
- **A truncated OpenAI-compatible stream produced an empty reply.** Turn
  termination hung entirely on a `finish_reason` chunk; a compatible server or
  dropped connection that ended the stream without one left the agent loop
  appending an *empty* assistant message — the streamed text vanished and any
  requested tool call was dropped. The turn is now reconstructed from what
  arrived. (`aida/providers/openai_compat.py`)
- **Session startup leaked MCP subprocesses.** Cleanup ran only for
  `UnknownProfileError`, but the same block can raise
  `UnknownProviderKindError` (a typo'd `kind:`) or an unreadable-skills-file
  error — leaving every just-launched MCP server orphaned for the life of the
  process. Cleanup now covers every failure path. (`aida/core/session.py`)
- **Missing source folders were fabricated with parents.** A source folder on
  an unmounted share had its whole path created as empty local directories,
  shadowing the real mount point so the agent reported an empty data folder
  instead of "not mounted". Source folders are now only created when their
  parent already exists; target folders still get `parents=True`.
  (`aida/core/session.py`)

Plus: `aida doctor` now points at the real docs instead of a nonexistent
command; version bumped to `0.1.0b1` with the `Development Status :: 4 - Beta`
classifier; README rewritten for external users with PyPI install
instructions; the "pre-alpha" banner replaced across `docs/`; and the
previously undocumented features (per-profile `max_tokens` / `temperature` /
`supports_vision` / pricing, Quick Tasks, assistant name and personal
context, first-run onboarding) documented.

---

## 4. pyIrena interoperability round (2026-08-28)

Prompted by the observation that AIDA's target audience runs pyIrena too,
and may `pip install` both into one environment in either order.

**Coexistence, verified empirically** (both install orders, in a clean venv,
`pip check` clean afterwards, and AIDA's own `McpManager` launching the real
`pyirena-mcp` — 68 tools discovered, server instructions received):

- AIDA's `gui` extra now mirrors pyIrena's PySide6 exclusions
  (`>=6.6,!=6.7.*,!=6.10.*`). Sequential `pip install` resolves only the
  requirement it was asked for, so a bare `>=6.6` could land on a release
  pyIrena has excluded and break it with nothing worse than a warning.
- Found and documented: pyIrena 1.0.1 on PyPI does not cap `mcp`, so
  `pip install "pyirena[mcp]"` alone pulls mcp 2.x and `pyirena-mcp` fails
  at import (2.x removed `mcp.server.fastmcp`). Installing AIDA into the
  same environment repairs it as a side effect, and pyIrena 1.1.0's own cap
  fixes it properly.
- The remaining hard edge is the Python floor: pyIrena allows 3.10, AIDA
  needs 3.11. A 3.10 environment refuses the AIDA install with a clear
  message rather than breaking anything.

**One-click pyIrena MCP setup** — configuring an MCP server was the hardest
thing a new user faced, and pyIrena is the one server this audience is
guaranteed to want:

- New `aida.mcp.pyirena_setup`: finds `pyirena-mcp` in AIDA's own
  environment, on `PATH`, as `python -m pyirena.mcp.server`, or in sibling
  conda/mamba environments; reports the pyIrena version behind each; builds
  the server config (absolute path — a GUI app inherits no shell `PATH`,
  which is the single most common MCP misconfiguration — plus the
  `pyirena-analysis` group, both skills, and `PYIRENA_MAX_ARRAY_POINTS`,
  with `PYIRENA_DATA_ROOT` on request).
- `aida mcp add-pyirena` and `aida mcp find-pyirena`.
- An **Add pyIrena…** button in the MCP management dialog, which shows what
  it found and confirms before writing anything (an MCP server is code AIDA
  launches on the user's machine), suggesting `PYIRENA_DATA_ROOT` from the
  active workspace's source folder. Also offered on the onboarding screen,
  and only when pyIrena is actually installed.
- An `aida doctor` check reporting both halves — installed or not,
  configured or not — always as OK, never a failure, since a user with no
  interest in pyIrena must not see a red FAIL for a package they chose not
  to install.

**Bundled skills now ship in the wheel.** `skills/` was repo-only, so a
`pip install aida-workbench` user had no skills at all while the
`workspaces.yaml` examples and the pyIrena setup both reference them by
name. They are force-included into the wheel and installed into
`~/.aida/skills/` on demand by `install_bundled_skills`, which never
overwrites a file the user already has.

New docs: `docs/pyirena.md`, plus pointers from the README, `installation.md`,
`mcp-servers.md`, and `gui-overview.md`.

---

## 5. Items from the old "future ideas" list that have since shipped

- **GUI workspace editor, including knowledge-base selection** — shipped as
  `workspace_management_dialog.py` (U1). Was the biggest item in the first
  round of real-use feedback.


---

## 6. Context-window management and compaction (2026-08-28)

The failure this prevented: a long pyIrena analysis conversation dying
halfway through, with no in-app recovery. Full design, steps, and measured
numbers in
[`planning/context_management.md`](context_management.md) (marked
implemented); user-facing docs in
[`docs/context-and-limits.md`](../docs/context-and-limits.md).

- **Count what is actually sent.** MCP tool schemas, tool-call
  arguments/results (dense-estimated), and vision images are now all
  counted (`aida.core.context.estimate_tool_schema_tokens`/
  `estimate_message_tokens`) — previously the budget summed only the plain
  message list, missing pyirena-mcp's measured ~10,200 tokens of schema
  JSON on every request.
- **Per-profile `context_window`.** `ProviderProfile.context_window` (falls
  back to `AppConfig.max_context_tokens`) plus
  `aida.core.context.history_budget` (context window × 0.85 safety
  fraction, minus reserved output tokens, minus tool-schema tokens, clamped
  to an 8000-token floor). Editable in the Providers… dialog and
  `providers.yaml`.
- **Context-fullness visibility.** A `[context]` line after every CLI turn
  and a **Context: Nk / Mk (P%)** GUI status-bar label, separate from the
  cumulative **Session total:** label.
- **Compaction.** `ChatSession._trim_context`/`_apply_trim_plan` summarize
  dropped turns via the active provider instead of discarding them, with a
  `/compact` CLI command and a **Compact Conversation** GUI action for a
  manual trigger at a task boundary. Falls back to plain trimming if the
  summarization call itself fails.
- **Recovery from a full context.** Compaction (automatic and manual) is
  the fix — a conversation now recovers headroom instead of every later
  turn failing identically.

Documented in `docs/context-and-limits.md`, cross-linked from
`docs/providers-and-secrets.md`, `docs/mcp-servers.md`, and `docs/pyirena.md`.

---

## 7. Small decided items round (2026-08-29)

`PLAN.md` §1.5 listed eight small, already-decided items. Four were
ready to implement outright; the other four needed either a real UX
decision or missing domain knowledge and stayed in §1.5, deferred with the
reason noted there.

- **Estimated tool count per MCP group.** `GroupsDialog` sums
  `McpManager.tool_names()` across each group's *running* member servers
  and shows it next to the group (e.g. "(3 tools)", or "(2 tools from 1/2
  running)" when some members aren't started) — a server that isn't
  running is called out as such rather than folded into a silent zero.
  (`aida/ui/qt/mcp_management_dialog.py`)
- **Images placed within a report, not just appended.**
  `write_markdown_report` and `write_docx_report` now accept a
  `{{image:ARTIFACT_ID}}` placeholder inside `body`; it's substituted
  in place with that image's link (Markdown) or becomes an inline
  `DocxSection` at that point (DOCX). Anything not placed with a
  placeholder still lands after the body exactly as before, so this is
  purely additive. The bundled `pyirena-usage` skill now teaches the model
  to use it for reports covering more than one fit or dataset.
  (`aida/documents/writers/md_obsidian.py`, `aida/documents/tools.py`,
  `skills/pyirena-usage.md`)
- **A free `AnthropicProvider.ping()`.** Now calls `models.list(limit=1)`
  instead of a real paid 1-token `messages.create(...)` — `aida doctor`
  and profile validation both stop spending money just to confirm a key
  works. Falls back to the old paid ping only for a proxy (e.g. the ANL
  Argo proxy) that doesn't implement the newer Models API.
  (`aida/providers/anthropic_.py`)
- **Install Bundled Skills… button.** The Skills dialog can install
  AIDA's sample skills (`saxs-basics`, `pyirena-usage`, `review-checklist`)
  directly, instead of only as a side effect of `add-pyirena`.
  (`aida/ui/qt/mcp_management_dialog.py`)

---

## 8. "Allow for this chat" — remembered safety confirmations (2026-09-03)

Bug report, in effect: a `confirm`-mode workspace pops an identical dialog
for every single write into the same already-allowed folder — five
scratch files written to one folder is five dialogs — which is exactly
the kind of repetition that trains a user to stop reading and just click
"Yes." The fix is a third answer, "Allow for this chat," alongside every
existing Deny/Allow — not a permanently relaxed workspace, and nothing
written to `workspaces.yaml`/`config.yaml`.

Decided scope: remembering is folder-level (the containing folder, not
the exact file, not subfolders) and per-action (approving a write never
covers a delete or a read in the same folder). Shell commands
(`authorize_execute`) are scoped by working directory instead — a
deliberately bigger grant, since a remembered approval there also covers
*different* future commands from that same directory. MCP per-tool
"confirm before run" flags are scoped by exact tool name. A folder outside
every allowed root participates too (not just in-bounds `confirm`-mode
folders). `fetch_url` is the one deliberate exception — it always asks,
unconditionally, with no remember option — enforced two independent ways
(the URL fetcher never attaches a scope to its own confirmation, and the
remembering layer only ever caches an explicit allowlist of action kinds
that omits `fetch_url`), so the guarantee can't be broken by a future
change to only one of those two places.

Mechanism (`aida/core/confirmation.py`): `ConfirmationRequest` gained a
`remember_scope: str | None` field (`None` = never remembered). A new
tri-state `ConfirmAnswer` (`DENY`/`ALLOW_ONCE`/`ALLOW_FOR_CHAT`) is what
the two *interactive* raw callbacks — `cli_confirm`
(`aida/core/session.py`) and `ChatBridge._confirm_interactive`
(`aida/ui/qt/bridge.py`) — return; a `RememberingConfirm` wrapper turns
that back into the plain bool every `ConfirmCallback` consumer
(`SafetyGuard`, `McpManager`) already expects, caching an
`ALLOW_FOR_CHAT` answer under `(action, remember_scope)` for its own
in-memory lifetime. One `RememberingConfirm` instance lives exactly as
long as one chat: the CLI builds one per `_start_session` call (one per
process), and `ChatBridge` builds exactly one per bridge instance, shared
between `start()`'s default and the separate `McpManager` built lazily by
`_ensure_mcp_manager()` (missing that sharing would have silently
defeated confirmation there once the interactive callback stopped
returning a plain bool — enums are truthy, so `if not approved` would
never fire). `SafetyGuard.authorize_run_script` was given its own
`"run_script"` action string, distinct from `authorize_execute`'s
`"execute"` — they used to share the literal string, which would have let
an approval for one silently cover the other whenever a script's folder
and a shell command's cwd happened to coincide, exactly the cross-action
leak the per-action scoping rule above was meant to prevent.

Every non-interactive `ConfirmCallback` consumer (tests, headless mode's
`build_headless_confirm_callback`, an explicitly-supplied callback) is
untouched — only the two interactive raw callbacks changed their return
type, and only the two places that supply *defaults* wrap them.
Documented in
[docs/safety-and-permissions.md](../docs/safety-and-permissions.md#allow-for-this-chat).
