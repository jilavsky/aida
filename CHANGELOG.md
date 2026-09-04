# Changelog

All notable changes to AIDA are recorded here, newest first. The format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/), with a `bN` beta
pre-release identifier until 1.0 — see [`PLAN.md`](PLAN.md) for what 1.0
requires.

This file is the terse, dated "what changed and for whom," by release. Two
other files are easy to confuse it with: [`planning/COMPLETED.md`](planning/COMPLETED.md)
holds the fuller design rationale behind shipped work, in narrative form, not
tied to a version number; [`planning/DESIGN.md`](planning/DESIGN.md) §2 has
its own "Changelog" — dated amendments to the *design document itself* (a
decision revised), unrelated to what shipped when. Entries below link to
`COMPLETED.md` where useful; `PLAN.md` tracks what's still open.

## [Unreleased]

### Added

- **"Allow for this chat"**: every safety-confirmation dialog/prompt now
  offers a third answer alongside Deny and Allow. Choosing it remembers
  that approval — scoped to the containing folder (or, for a shell
  command, its working directory; for an MCP per-tool confirm flag, the
  exact tool name) and to that one action kind — for the rest of the
  current conversation only, so repeated writes/deletes/commands into the
  same already-approved folder stop popping identical dialogs. Nothing is
  persisted to disk; a New Chat, a resumed conversation, a workspace
  switch, or a fresh `aida chat` process all forget it. `fetch_url` is
  deliberately excluded and keeps asking unconditionally, every time. See
  [docs/safety-and-permissions.md](docs/safety-and-permissions.md#allow-for-this-chat).

### Fixed

- Reading or attaching a real document (e.g. a multi-page PDF) silently
  handed the model well under one page of it. `read_document()` already
  truncates PDF/DOCX/XLSX/PPTX/text content to a reasonable 20,000-char
  budget, but both the `read_file` tool and the GUI's drag-and-drop/
  "Attach…" path then ran that text through `describe_for_model()`'s own,
  separate 4,000-char default on top — re-truncating it without either
  call site overriding it. In practice this meant a dropped journal paper
  arrived clipped almost immediately, and the model (correctly noticing
  the content was incomplete) would fall back to writing and running its
  own PDF-extraction script via `run_python_script` instead of just
  reading the file, costing two script-execution confirmation dialogs for
  something that should need none. Both call sites now share a single,
  larger interactive budget (100,000 chars / 150 pages) so a full paper
  reaches the model in one native `read_file` call or attachment, matching
  the pattern RAG ingestion already used for itself.

## [0.1.0b3] - 2026-09-01

### Added

- `aida doctor`'s new `max_tokens_vs_context_window` check, which fails
  specifically when a profile's `max_tokens` crowds out its own
  `context_window` — the easy-to-hit mistake of reading `max_tokens` as
  "the model's total window" and setting it to that model's full context
  size, which silently clamps every turn's history budget to the 8000-token
  floor regardless of how large `context_window` actually is.
- Tooltips on the Providers… dialog's **Max tokens** and **Context
  window** fields explaining the difference, plus a callout with the exact
  log message in `docs/providers-and-secrets.md`.
- **Workspace Notes**: a free-text notepad in the right-hand panel,
  saved with the workspace (`notes` in `workspaces.yaml`, written on the
  next save for workspaces that predate the field) and auto-saved a beat
  after you stop typing. Deliberately private — never added to the
  system prompt, not readable by any tool — so a running commentary costs
  no tokens and can't steer the model.
- The right-hand session panels (Folders, MCP Servers, Quick Tasks,
  Workspace Notes) are now collapsible, and the whole column scrolls.
  Which panels you keep collapsed is remembered between launches
  (`collapsed_panels` in `config.yaml`).
- You can type while the agent is working. Text sent mid-turn is handed to
  the running turn and delivered at its next round trip — never mid-stream
  and never between a tool call and its result — then shown in the
  transcript where it actually landed. **Stop** is now its own button
  rather than a relabelled **Send**, so both are available at once; a
  queued message the turn ends before reaching comes back to the input box
  instead of vanishing.
- The status bar's **Session total** (tokens + estimated cost) and
  **Context** labels now refresh every 30 seconds while a turn is running,
  not only when it finishes — a long tool-loop turn used to show the
  numbers from before it started. `ChatSession` already accumulated usage
  per model round trip; only the repaint was missing. The poll runs
  strictly between `turn_started` and `turn_finished`.
- A visible "working" state in the GUI while a turn is in flight: the
  Send button becomes a red **Stop** button and a live `Working… 12s`
  indicator ticks beside it. Previously the only cues were the disabled
  text box and the button's changed label, which was easy to miss.

### Fixed

- Quick tasks no longer disappear from a workspace. Editing a workspace
  in the **Workspaces…** dialog rebuilt its config from that form's own
  fields, so every field the form doesn't show was reset on OK —
  `quick_tasks` (edited in the main window's own panel) silently emptied,
  and `templates_dir`/`saved_scripts_dir` reset to unset. Those fields are
  now carried through an edit, the workspace detail panel lists the saved
  quick tasks, and a quick-task edit with no active workspace says so in
  the status bar instead of being dropped in silence.
- A model that rejects a `temperature` *value* rather than the parameter
  itself is now recovered from too ("Unsupported value: 'temperature' does
  not support 0.7 with this model. Only the default (1) value is
  supported.", from OpenAI-family models via the ANL Argo proxy). The
  recovery existed but was gated on `except APIStatusError`, and an error
  delivered *inside* an SSE stream — HTTP 200, then an error event, which
  is how that proxy reports an upstream 400 — reaches the SDK's
  status-less base error class instead. It fell through to "unexpected
  provider error" with no retry, even though the message said exactly
  which parameter to drop. Every error branch now asks, and eligibility
  depends on what the message says rather than on how the SDK wrapped it.
- A model that rejects `temperature` no longer fails the turn. Newer
  Claude models answer the parameter with a 400 ("`temperature` is
  deprecated for this model") — and the ANL Argo proxy relays that inside
  its own 200 — which surfaced as a provider error with no way to fix it
  from a profile, since the profile could only override `temperature`,
  never omit it. Both providers now drop the offending sampling parameter,
  retry the request once, and remember the rejection for the rest of the
  session; an error about anything else still surfaces unchanged.

### Changed

- AIDA no longer invents a `temperature`. A provider profile that doesn't
  set one now sends **no** temperature and lets the endpoint apply its own
  default; previously an unset field silently became 0.7 on every request.
  That invented value is the root of the whole "unsupported temperature"
  class of failure — models that fix temperature at their own default
  reject 0.7 outright, and no client can know statically which models
  those are. Profiles that *do* set a temperature are unaffected, and the
  drop-and-retry recovery still covers a value a model won't take. Set
  `temperature: 0.7` explicitly on a profile to keep the old behavior.
- The "Scratch folder" system-prompt paragraph (`build_workspace_context_block`)
  now tells the model what to do when an MCP tool rejects its scratch-folder
  path — some servers (browser-automation tools like Playwright MCP
  especially) sandbox their own separate output directory and reject a
  path built from AIDA's scratch folder outright. New
  [mcp-servers.md](docs/mcp-servers.md) section on the failure mode and
  the fix.

## [0.1.0b2] - 2026-08-29

### Added

- Context-window management: token-aware history budget per provider
  profile, context-fullness display in the CLI and GUI status bar, and
  automatic + manual (`/compact`, **Compact Conversation**) conversation
  summarization when a session runs low on context. See
  [`docs/context-and-limits.md`](docs/context-and-limits.md) and
  `planning/COMPLETED.md` §6.
- One-click pyIrena MCP setup: `aida mcp add-pyirena` / `aida mcp
  find-pyirena` and an **Add pyIrena…** button in the MCP management
  dialog, which detect an installed `pyirena-mcp`, write its server
  config, and install the matching bundled skills. See
  [`docs/pyirena.md`](docs/pyirena.md) and `planning/COMPLETED.md` §4.
- Estimated tool count per MCP group in the Groups dialog, summed over
  each group's running member servers.
- `{{image:ARTIFACT_ID}}` placeholder support in `write_markdown_report`
  and `write_docx_report`, letting the model place an image at a specific
  point in a report body instead of always after it.
- **Install Bundled Skills…** button in the Skills dialog, installing
  AIDA's sample skills without needing `add-pyirena` first.
- This changelog.

### Changed

- `AnthropicProvider.ping()` (used by `aida doctor` and profile
  validation) now calls the free `models.list` endpoint instead of a
  paid 1-token completion.

### Fixed

- Five bugs from a pre-beta review round: duplicated FastMCP tool-result
  payloads counted twice against the context budget; `aida workspace
  edit` silently wiping `quick_tasks`/`script_timeout_seconds` on an
  unrelated one-flag edit; a truncated OpenAI-compatible stream producing
  an empty reply instead of the text that had already arrived; MCP
  subprocesses leaking on a startup error path other than the one that
  was handled; source folders on an unmounted share being fabricated as
  empty local directories. See `planning/COMPLETED.md` §3.

## [0.1.0b1] - 2026-08-28

Initial public beta. Phases 1-9 of `PLAN.md`: config and diagnostics, the
provider layer and agent loop, MCP with typed artifacts, persistence and
workspaces, the PySide6 GUI, documents and the safety model, MCP
management UI, RAG, and coding/scripting. See the README's "What it does"
for the feature summary as of this release, and `planning/COMPLETED.md`
§§1-2 for the full build history behind it.

## [0.0.1] - 2026-08-18

Earliest tagged snapshot, pre-beta.

[Unreleased]: https://github.com/jilavsky/aida/compare/v0.1.0b3...HEAD
[0.1.0b3]: https://github.com/jilavsky/aida/compare/v0.1.0b2...v0.1.0b3
[0.1.0b2]: https://github.com/jilavsky/aida/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/jilavsky/aida/compare/v0.0.1...v0.1.0b1
[0.0.1]: https://github.com/jilavsky/aida/releases/tag/v0.0.1
