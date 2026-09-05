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

- **Optional Mistral OCR backend for reading figures**, off by default.
  The built-in extractor pairs an image with the caption nearest to it on
  the page, which is a guess on the two-column layouts most journals use.
  Mistral OCR returns the page in *reading order*, so a caption pairs with
  its figure as reliably on two columns as on one — which is what makes
  "show me Figure 1" trustworthy on a real paper.

  Enabled per **workspace** (`use_ocr`), not per install, because the
  answer differs: a workspace for reading vendor manuals can have it on
  while one used to review unpublished manuscripts keeps it off. Needs the
  new `ocr` extra (`pip install "aida-workbench[ocr]"` — just `httpx`, no
  vendor SDK) and an API key, stored the same way as a provider key. The
  key now has a write-only **Document OCR** field (and Clear key action) in
  Settings, while the Workspaces dialog exposes and reviews each
  workspace's `use_ocr` switch.

  **Documents are uploaded to a third-party service, and AIDA asks before
  each one** — with "Allow for this chat" available, so working through a
  stack of manuals is not a stack of dialogs. An unattended run refuses the
  upload unless the name is explicitly pre-approved with
  `--preapprove-tool mistral_ocr_upload`. A document already examined is
  never re-uploaded.

  Every failure — declined, no key, extra not installed, service down,
  timeout — falls back to the built-in extractor **and says so** in what
  the model reads, because quietly producing worse labels than expected is
  how a figure answer becomes wrong without anyone noticing. `aida doctor`
  reports the three preconditions separately and never contacts the
  service.

- **You can ask about a figure in an attached paper.** Two new tools:
  `list_document_figures` names what is in a document — label, caption,
  page — as *text only*, and `get_document_figure` returns one image by
  label. Nothing pushes pictures at the model: an unlabelled figure it
  cannot name is worse than no figure, and this way a twelve-figure paper
  costs a couple of hundred tokens to describe while the agent spends its
  vision budget on the two figures that matter.

  Labels are honest about how sure they are. A caption matched on a
  single-column page is reported as reliable; on a multi-column layout —
  where the text below an image is regularly the next column — the index
  says the labels are uncertain and to check the caption before relying on
  a figure number. An image with no caption nearby gets a positional label
  (`image 2 (page 4)`) rather than an invented one.

  Extraction happens the first time you ask, not when the document is
  attached, so a paper nobody asks a figure question about costs nothing;
  results are cached beside the document in its `.assets` folder and go
  away with the conversation like everything else there. Figures are
  available for documents **you attached** — a file the agent opened itself
  with `read_file` was never copied into the conversation, and the tool
  says so rather than reporting an empty list.

- **A conversation now keeps the documents you attach to it.** Attached
  *images* were already copied into AIDA's artifact store and survived a
  restart; the documents were not — a PDF dropped in from a Downloads
  folder that later got cleaned left the conversation discussing a paper
  nobody could open again, and the Markdown transcript held what the model
  *said* about it but not the thing itself. Attachments are now copied
  into `<records dir>/attachments/<conversation>/` alongside the text
  extracted from them, and linked from the transcript. That is the records
  folder rather than `~/.aida` on purpose: you can find, browse and clean
  these by hand.

  Only files **you** attach are copied. A file the agent opens with
  `read_file` is left where it is — it already lives in your own folders,
  and duplicating it would put a second copy of possibly-sensitive data
  somewhere you did not ask for.

  **Deleting a conversation deletes its documents.** Not tidiness: someone
  who deletes a chat holding a manuscript under review must not find that
  manuscript still in their home directory. The folder's real location is
  recorded on the conversation rather than recomputed at delete time, so
  changing the Records folder in Settings can no longer strand it — a
  latent bug that already applied to the `figures/` sidecar and is fixed
  for both. As a backstop, `aida doctor` reports attachment folders whose
  conversation is gone, and a new `aida conversations gc` removes them
  after asking. The GUI now confirms accepted attachment names in the
  status bar and provides **File → Open Conversation Folder** without
  creating empty folders for chats that have no attachments.

- **`aida documents figures FILE`** — run the real extraction against a
  file and see exactly what happened: which backend ran, what it found,
  and the reason if OCR was configured but did not run (no key, extra
  missing, workspace switch off, upload declined, or the service's own
  error). An OCR failure exits non-zero. There is also
  `aida documents verify-ocr`, the command-line twin of Settings' Verify
  key.

  This existed to answer a question nothing else could: *was my document
  actually sent to Mistral?* Every other signal was indirect — a dialog
  that only appears once the agent asks about figures, a note inside a tool
  result, a `backend` field in a JSON file — and "attach a PDF and read the
  reply carefully" is not a way to check a setup. A failing OCR call used
  to leave nothing but a line in a log file nobody was watching and figures
  quietly a little worse than expected.

- **Fixed: the extracted text was never written beside an attached
  document.** The attachments folder was documented as holding both the
  original and the text the model was given, but the text was dropped on
  the way — `keep_attachments` was called without it — so the folder held
  only the file. It now shows what the model actually received.

- `aida doctor`'s `ocr` line no longer implies a document is uploaded when
  it is attached. It says what really happens: OCR runs the first time a
  document's figures are asked for, and asks first. The GUI says the same
  in the status bar when a PDF is attached to an OCR-enabled workspace.

- **Right-click a conversation ▸ Move to User** — the repair for having the
  wrong name selected when a chat was started, which is the mistake a
  free-text label makes easy and the one thing renaming cannot fix (that
  moves everything a name owns). Works on a multi-selection too, since a
  run of chats is usually wrong together, and offers "(no user)" and a
  "New user…" prompt alongside the existing names. Relabelling deliberately
  does not touch a conversation's timestamp: it is not activity, and
  bumping it would reorder the list and change what a cleanup-older-than
  sweep would catch.

- **Settings ▸ Document OCR ▸ Verify key** checks a Mistral key against the
  service and reports what it found, **without uploading a document** — it
  lists models, the cheapest authenticated call there is. Otherwise the
  only way to test the setup would be to perform the exact action the key
  is there to be careful about. It also flags the confusing middle state: a
  valid key whose account cannot see an OCR model, where the upload would
  succeed and the OCR call fail.

- **File ▸ Manage Users…** gained **New User…**, and the toolbar's User
  box no longer acts on every keystroke. Two problems with the first cut: typing a name emitted a
  change per character — so "jan" tore down and rebuilt the session three
  times before you finished the word — and there was no way to fix a
  mistyped name once it existed. The box now commits on Return or when
  focus leaves it, shows a "type a name to add" hint, and the new dialog
  renames a label (merging, if the new name already exists — which is what
  fixing a typo means) or clears it. Clearing removes the *label*: the
  conversations stay and become visible under every user.

  **New User…** starts working under a name. Nothing is registered
  anywhere — a name exists from the moment a conversation uses it — so the
  button makes the name active and the list shows it as "no conversations
  yet — active" rather than pretending something was saved. Without it the
  dialog was a dead end with no names in use: an empty list and two greyed
  buttons. There is still no "delete user": deleting conversations stays in
  the sidebar, where it already is, rather than being duplicated somewhere
  the wrong one can be clicked.

  The toolbar now reads **User, Workspace, Provider**, left to right in the
  order the choices narrow each other, and the User box is wide enough to
  read a name (it sized itself to its widest *item*, and on a fresh install
  that is the empty "no user" entry — about two characters).

  Two behaviours were wrong and are fixed. **A name you declare no longer
  disappears** when you switch away before sending anything: a session's
  conversation row is created empty and deleted again if unused, so a name
  whose only conversation was that empty one vanished from the toolbar.
  Declared names are now remembered in `config.yaml` and unioned with the
  names the conversations carry, so the list can only ever gain a name, not
  contradict the database. And **selecting a user filters the conversation
  list to that user** — it used to also include every unlabelled
  conversation, which was meant to stop a pre-existing history vanishing
  but, since all of that history is unlabelled, made picking a name look
  like it did nothing. The sidebar filter now follows the toolbar, an
  explicit choice there survives ordinary refreshes, and a new **(no user)**
  entry reaches unlabelled conversations without having to show
  everyone's.

- **Conversations can carry a `user` label** — an organization axis for a
  shared beamline machine (where the buckets are people) or a laptop with
  several projects on it (where they are tasks). A flat, ever-growing chat
  list has no safe bulk cleanup: "delete everything older than 30 days" in
  a shared list takes conversations somebody else wanted kept. Set
  `active_user` in `config.yaml`, or pass `--user NAME` to `aida chat` /
  `aida run`, or set `$AIDA_USER` for headless and scheduled runs
  (precedence in that order, flag first). `aida conversations list` gains
  `--user` and `--all-users`.

  **This is organization, not security.** Anyone at the machine can pick
  any name; there is no password and no permission difference between
  names. Nothing changes for an install that does not set one: the column
  stays NULL, nothing is filtered, and no path gains a user segment.

  `{user}` can be written into `records_dir`, `target_folder`,
  `source_folders`, `templates_dir` and `saved_scripts_dir`, and is
  expanded to a path-safe slug of the active name (`Jan Ilavsky` →
  `jan-ilavsky`, no active user → `default`) before anything reads those
  folders — so each bucket's transcripts and scripts land in their own
  place. Resuming a conversation never re-labels it: it belongs to
  whoever created it, even if a different name is active now. The GUI now
  has an editable toolbar user picker; changing it starts a new chat. The
  sidebar can filter by user while always keeping unlabelled legacy chats
  visible and providing an **All users** escape hatch, and its text search
  now matches workspace and user as well as title. See
  [docs/organizing-conversations.md](docs/organizing-conversations.md).

### Fixed

- A document's images are still not extracted — every reader is text-only —
  but AIDA no longer stays **silent** about it, which was the part that
  actually misled the model. Each reader now counts the embedded images it
  dropped and says so in the text it returns ("[This document contains 7
  embedded images, which were not extracted...]"), and the PDF reader
  additionally detects the case where there was no usable text layer at all
  — a scanned paper, a photographed logbook page, a signed form — and
  reports it as a document that *could not be read* rather than handing
  back an empty-looking one. Previously a scanned PDF extracted as empty
  text with no warning, so the model could not tell it from an empty file
  and would either guess or report the attachment as blank. A repeated
  logo counts once rather than once per page, the note is appended after
  the size budget so truncation can never drop the explanation itself, and
  an ordinary text-only document with no images gets no note at all. See
  [`planning/document_images.md`](planning/document_images.md) for the
  richer options this is the floor of.

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
