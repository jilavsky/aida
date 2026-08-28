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

## 4. Items from the old "future ideas" list that have since shipped

- **GUI workspace editor, including knowledge-base selection** — shipped as
  `workspace_management_dialog.py` (U1). Was the biggest item in the first
  round of real-use feedback.
