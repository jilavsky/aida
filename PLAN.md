# AIDA — open work

**A local scientific agent workbench.** Repo `jilavsky/Aida` · import package
`aida` · PyPI distribution `aida-workbench` · MIT · Python >= 3.11 · PySide6.

**Status: 0.1.0b3 (beta), 2026-09-01.** Phases 1–9 are implemented, tested
(1,400+ tests, three OSes) and in daily use. This file now holds **only what is
not done**.

- What AIDA is, and every design decision behind it → [`planning/DESIGN.md`](planning/DESIGN.md)
- What has been delivered → [`planning/COMPLETED.md`](planning/COMPLETED.md)
- Per-phase checklists → `planning/phase01…phase09_*.md`
- User-facing setup and configuration → [`docs/`](docs/README.md)

Code comments and docstrings that cite "PLAN.md §N" or "PLAN.md Phase N"
refer to the sections and phase files as they were before this split — the
numbered sections now live in `planning/DESIGN.md`, the phases in
`planning/phase0N_*.md`. Nothing they describe has changed.

Two lists follow, and the distinction between them is the point:

- **§1 Planned** — committed work. Each item has a decided shape; the only
  question is when.
- **§2 Considered** — discussed, understood, deliberately *not* committed. An
  item graduates to §1 only when a concrete need appears; keep adding here
  rather than growing §1.

Working agreements (unchanged): a new capability is a new item in this file,
not silent scope growth; a decision that changes something in `DESIGN.md` §2
gets a dated note appended there; error messages say *which* layer failed.

---

## 1. Planned

### 1.1 Beta feedback loop — the current priority

Nothing here is speculative work; it is what a beta is for.

- [ ] Publish `aida-workbench` 0.1.0b3 to PyPI and verify
      `pip install "aida-workbench[gui,docs]"` → working `aida-gui` on a
      clean macOS, Windows, and Linux machine.
- [ ] First outside users installing from PyPI, with issues triaged into this
      file rather than fixed ad hoc.
- [ ] Watch for the two things most likely to bite a new user: a provider
      profile that won't connect, and an MCP server that won't start. Both
      have diagnostics; the question is whether they are *findable*.
- [ ] Verify the pyIrena coexistence matrix on a real beamline machine
      (Windows and macOS, conda rather than a venv) — it has been verified
      in a clean Linux venv, both install orders, but conda's own resolver
      and Windows path handling are the ones that actually matter here.
      See `docs/pyirena.md`.
- [ ] Mirror any future pyIrena PySide6 pin change into AIDA's `gui` extra.
      The two are deliberately kept identical so no pip resolution order can
      break the other package; nothing enforces that automatically.

### 1.2 Phase 10 — automation and distribution (`phase10_automation_distribution.md`)

**Automation half done (2026-09-03), on branch `phase10-in-app-scheduler`,
merged to `main`.** The distribution half (release automation, conda
feedstock) is still open.

The *shape* of the automation half was decided and written up in
[`planning/phase10_scheduling_design.md`](planning/phase10_scheduling_design.md):
four layers (`run` → `workflow` → `schedule` → trigger), where only the top
layer touches the operating system. Three conclusions from it that shaped
what got built:

1. **Workflows and scheduling are separable, and workflows are most of the
   value.** "Replay this analysis on a new folder" needs no clock at all.
   Shipped first, complete.
2. **In-app scheduler first, OS schedulers later and additively (Option
   A).** Built and shipped; the launchd/Task Scheduler/systemd installers
   remain deliberately unbuilt — see
   [`docs/workflows.md`](docs/workflows.md#why-no-os-level-scheduler) and
   §2's "Considered" note below. Nothing built for the in-app scheduler is
   thrown away if they're added later (`ScheduleEntry.trigger` already
   exists for exactly this reason).
3. **The blocker nobody sees coming is secrets, not clocks.** Handled: the
   existing env-var fallback (`AIDA_SECRET_<PROFILE>`) is what `aida run`/
   `workflow run`/scheduled fires authenticate through, and `aida doctor`
   reports per-profile whether that's actually in place.

- [x] `aida run --workspace W "prompt"` — non-interactive single turn: exit
      codes (distinguishing step failure / config error), `--json` output,
      stdin prompt, `--input file…` attachments.
- [x] Headless confirmation policy: fail-with-message by default;
      `--yes-in-allowed` narrows the workspace's own safety mode and never
      widens it; MCP `confirm_before_run` flags need explicit
      `--preapprove-tool`/`preapproved_tools:` pre-approval; never hangs
      waiting for a human who is not there.
- [x] Non-interactive secret access: env-var fallback for every
      `secret_ref` (already existed), plus an `aida doctor` check for it.
- [x] Stored named workflows in `~/.aida/workflows/NAME.yaml` — workspace ref
      plus steps, placeholder substitution, optional `expect_files` per step,
      `aida workflow run/list/show/validate`. All steps share one session.
- [x] "Save this conversation as a workflow" from the GUI, and a workflow
      picker (Workflows… management dialog) that runs one into a normal
      conversation view.
- [x] Failure semantics: a failed step stops the workflow with a clear report
      and leaves partial output in place.
- [x] Reproducibility manifest per run (`PLAN.md` §2.6) — a by-product of
      the workflow runner (`run-<name>-<timestamp>.aida.json`).
- [x] In-app scheduler over `~/.aida/schedules.yaml` (definition) plus
      SQLite last-run state: due/catch-up-once semantics, no overlap
      (in-process guard + cross-process advisory lock), its own session
      rather than the user's, visible last-run status in the GUI dialog,
      and a status-bar failure indicator.
- [x] **Added beyond the original scope, from GUI hands-on testing
      (2026-09-03):** the scheduler now *defers, not skips* a due job while
      the user is actively using AIDA — a running turn hard-blocks it,
      unsent input text or recent activity soft-blocks it for a
      configurable quiet period (default 5 min), waived after a
      configurable cap (default 1 h) except mid-turn. Status bar shows a
      **⏳ N jobs waiting** indicator; both timings are in Settings. See
      [`docs/workflows.md`](docs/workflows.md#deferred-not-skipped--the-scheduler-and-you-at-the-same-time)
      and §7 of the design doc.
- [x] `docs/workflows.md` — `aida run`/workflow/schedule usage, headless
      confirmation and secrets caveats up front, and why OS-level triggers
      were deferred rather than built.
- [ ] *Deferred, not scheduled:* `aida schedule install/uninstall/status`
      generating native launchd/Task Scheduler/systemd artefacts — only if
      a user actually asks for "logged out, not just closed" (§2.1-style
      "Considered", not committed).
- [x] Tests: `aida run`/workflow/scheduler covered end-to-end with
      MockProvider + mock-mcp (1705 tests total, non-GUI + GUI), including
      catch-up-fires-once, overlap-skips, a clock jumped backwards, and the
      full deferral policy.
- [ ] Release automation: a GitHub Actions publish workflow with the version
      in `pyproject.toml` authoritative and tag-checked.
- [ ] conda: keep `environment.yml` current; evaluate a conda-forge feedstock
      once PyPI releases are routine (record the decision either way).

### 1.3 Context-window management and compaction — done (2026-08-28)

The failure this prevented: a long pyIrena analysis conversation dying
halfway through, with no in-app recovery. Shipped: per-message/tool-schema
token counting, a per-profile `context_window` budget, CLI + GUI
context-fullness display, and automatic + manual (`/compact`, **Compact
Conversation**) summarizing compaction. Full detail in
[`planning/COMPLETED.md`](planning/COMPLETED.md) §6; user-facing docs in
[`docs/context-and-limits.md`](docs/context-and-limits.md).

### 1.4 Verification owed (cannot be done from a sandbox)

Every one of these is a *manual* check the phase files left open because no
sandbox can perform it. They are the acceptance evidence for work already
believed complete.

- [ ] CI green on all three OSes after the next push (phases 1, 3, 4, 6, 7, 8, 9
      each left this box open for the same reason).
- [x] `aida chat --profile ollama-local` against a real local model, and
      `--profile argo-claude` through the ANL proxy on-site.
- [x] **Keystone, against the real thing:** a real model calling real
      pyirena-mcp, PNG decoded and displayed, saved to disk.
- [ ] bait_mcp connects and lists its tools from AIDA (no instrument needed).
- [x] Switching MCP groups demonstrably changes the tool list the model sees.
- [x] **UC2:** drop a PDF and an MD file on the GUI, ask questions, get a new
      MD plus figures sidecar in the target folder.
- [x] **UC3 full:** "find data in <source folder> with Rg 20–50 Å, plot them,
      write it up" end to end.
- [ ] **UC5:** check beamline status via an AIEvaluator script plus bait_mcp
      from an AIDA workspace.
- [ ] **UC1 full:** a documentation folder indexed, answers citing retrieved
      passages, index rebuilt from the GUI; the same knowledge base working
      with local *and* Argo embeddings; the ten canned USAXS questions
      answered with correct citations.
- [ ] RAG benchmark on a real corpus (USAXS instructions + a pyIrena docs
      folder + an Obsidian vault) — the escape hatch to a vector DB is only
      justified by measurement.
- [ ] Stop button interrupts a long generation cleanly and the app stays
      usable; manual smoke checklist on macOS *and* Windows.

### 1.5 Small, decided, not yet done

Reviewed 2026-08-29: of the eight items below, four were quick, unambiguous,
backward-compatible wins and are now done; four turned out to need either a
real UX decision or missing domain knowledge only the user can supply, and
are deferred with the reason noted inline.

- [x] Show estimated tool count per group in the MCP dialog — done
      (2026-08-29).
- [x] Let the model place images *within* a generated report rather than
      always appended at the end — done (2026-08-29).
- [x] A cheaper `AnthropicProvider.ping()` — done (2026-08-29).
- [x] Offer to install the bundled skills from the GUI generally, not only
      as a side effect of `add-pyirena` — done (2026-08-29).

Full detail on all four in [`planning/COMPLETED.md`](planning/COMPLETED.md) §7.

- [ ] Diff-style view when the agent proposes changes to an existing script
      (`phase09`, left open) — **deferred**. This was already an explicit
      "may slip"/dropped nice-to-have when Phase 9 shipped, not a plain
      cleanup: it needs a real UX decision (a side-by-side pane? inline
      +/- markup in the chat transcript? a separate review dialog before the
      write happens?) that only the user can settle — there's no single
      "obviously correct" shape to just build.
- [x] Per-workspace extra skills selection beyond server-linked ones
      (`phase07`) — done: `WorkspaceManagementDialog`'s Add/Edit form
      covers `skills` along with `profile`, `mcp_group`, `knowledge_bases`,
      `system_prompt`, `safety`, `scripting_enabled` and the script-timeout
      spinner. The blocker these two entries cited — "no GUI workspace
      editor exists yet" — no longer holds; the dialog is reachable from
      the toolbar. See [`docs/workspaces.md`](docs/workspaces.md) for the
      two fields (`templates_dir`, `saved_scripts_dir`) that remain
      config-file only.
- [ ] Set a workspace's default MCP group from the MCP dialog itself
      (`phase07`) — still open, but no longer blocked: the workspace editor
      it was waiting on now exists, so this is a matter of adding the
      control to the MCP dialog and writing through to `workspaces.yaml`.
- [ ] **Enforce `ruff format` in CI** (external review, P3). `ruff check`
      passes and is gated; `ruff format --check .` currently reports
      **128 files would be reformatted, 97 already formatted**. Two commits,
      in this order: one formatting-only pass with no other change in it
      (so it can be added to `.git-blame-ignore-revs`), then a
      `ruff format --check .` step next to the existing lint step in
      `.github/workflows/ci.yml`. Doing it the other way round makes CI red
      on main. Nothing else in the review's P3 list is still open — docs
      reconciliation, the vacuous `or True` assertions, the tracked
      `.DS_Store`, the wheel-build/install smoke job and `pip check` all
      landed in `59a4b92`.
- [ ] **Comment hygiene pass** (external review, P3). Long historical
      "this used to be broken because…" comments are genuinely useful while
      a fix is fresh and become false as the code moves on — the review
      caught two that had already gone stale (the startup-cleanup comment
      and the profile-switch failure handler's claim that the session was
      left untouched; both were rewritten in `59a4b92`). The rule going
      forward: a code comment states the *current* invariant and why it
      exists; the history and the rationale for the change move to
      `planning/COMPLETED.md` or a short decision record. This is a
      read-through, not a mechanical edit, so it wants doing once before
      1.0 rather than continuously.
- [ ] Extend the one-click MCP setup pattern beyond pyIrena — `bait_mcp`
      first, then a small offer list of npx-based servers. The detection and
      config-building shape in `aida.mcp.pyirena_setup` generalizes; what
      does not is knowing *which* env vars and group each server wants,
      which is exactly the per-server knowledge that makes it worth doing
      — **deferred**: this needs `bait_mcp`'s actual env-var names and
      group conventions from the user; nothing to build against yet.

---

## 2. Considered — discussed, not committed

Kept deliberately. Each line records something already thought through, so it
does not have to be rediscovered — and so it stays *out* of §1 until something
concrete asks for it.

### 2.1 Deployment and multi-user

- **Per-user beamline credentials.** Username selection → per-user chats and
  saved scripts, as in BeamlineAdvisor. Real overhead: data separation in the
  DB, per-user records folders, per-user secrets. Revisit when AIDA is
  actually deployed on `usaxscontrol`; likely a thin "active user" layer over
  persistence, not real auth. The Phase 4 schema already carries a nullable
  `user` column as cheap insurance.
- **Credentials for browser automation.** How an automated Playwright MCP
  run could log in to a web system without the agent, the provider, or
  AIDA's own records ever seeing the password — session reuse, a
  non-interactive credential injected as MCP server env (which AIDA already
  supports), or a `fill_secret`-style broker. Analysed and recorded in
  [`planning/credentials_and_browser_automation.md`](planning/credentials_and_browser_automation.md),
  including a traced map of the eight places a secret lands in AIDA today,
  none of them redacted. No decision taken; the manual "log in once a day,
  work inside that session" practice covers the attended case meanwhile.
- **Two AIDA instances sharing `~/.aida`** (SQLite + config writes) is
  unguarded. The single-user assumption is fine today; a lock file would at
  least make the failure mode explicit.
- **Preinstall Node/npx and offer common npx-based MCP servers** (Playwright
  and friends). Two separable pieces: `environment.yml` could pull `nodejs`
  from conda-forge so `npx` comes along, and AIDA could ship ready-to-enable
  config entries plus a one-click "Add browser automation" offer in onboarding
  or `aida doctor`. Deliberately an *offer*, never pre-enabled: an MCP server
  is code AIDA executes on the user's machine. Note that first launch still
  needs network to fetch the npm package.

### 2.2 Transport and integration

- **Remote MCP servers over HTTP/SSE** — instrument-side MCPs reachable from
  an office machine. The manager was designed transport-pluggable; add when a
  concrete remote server exists.
- **MCP Apps / rich interactive tool outputs**, if the ecosystem standardizes
  them.
- **Interactive plots** (a pyqtgraph pane fed by structured data artifacts) as
  an upgrade over static PNGs — coordinate with pyIrena MCP first.
- **External event triggers** (something happened at the instrument → generate
  a report) stay *external code invoking `aida run`*; Phase 10 gives the hook.
  A folder-watcher inside AIDA only if the pattern actually repeats.

### 2.3 Frontends and interaction

- **Alternative web frontend** (NiceGUI or similar) on the same event API, for
  browser access from beamline LAN machines. Only worthwhile once the event
  API has proven stable through the PySide6 app.
- **Voice STT input** — macOS dictation already covers it; Windows/Linux would
  mean local Whisper, a heavy dependency. Criterion: real demand at the
  beamline. If ever done: a mic button feeding the normal input box, nothing
  deeper.
- **TTS output** — unclear what it should even mean here. Revisit only if STT
  ships and earns its keep.
- **Extra GUI niceties**: per-display font/scaling profiles, more dockable
  widgets.
- **Native app bundles** (PyInstaller/Briefcase) — a timeboxed investigation
  at most. `pip install aida-workbench[gui]` is acceptable for an audience
  that already installs pyIrena that way.

### 2.4 Knowledge and analysis

- **RAG over past conversations** ("what did we conclude last week?").
- **A reranking model** in retrieval, if quality plateaus.
- **Automatic knowledge-base refresh** via a folder watcher.
- **Retrieval currently loads every chunk vector per query** — fine at present
  corpus sizes; revisit past roughly 10k chunks. This is the plan's own escape
  hatch, and it needs the §1.4 benchmark before anyone acts on it.
- **Parallel tool fan-out within a turn** ("plot all of these") would speed up
  UC3/UC4 but complicates cancellation and event ordering. Only if it becomes
  a felt bottleneck.
- **HDF5/NeXus native reading** — pyirena-mcp owns this deliberately. Only if
  a concrete non-pyIrena need appears.

### 2.5 Sharing

- **Conversation export bundles** (a zip of transcript plus artifacts) for
  sending an analysis session to a colleague.
- **pynika and other package MCPs** as they appear — should "just work"
  through the Phase 7 management UI; ship starter skills files alongside.

### 2.6 From the external review (2026-08-31)

The review's P1/P2 findings are fixed (`59a4b92`); its P3 remainder is in
§1.5. What follows is the review's *feature* list — recorded here rather
than in §1 because none of it has a concrete need pushing on it yet.
Ordered as the review ordered it, by value-to-complexity.

- **Reproducibility manifest beside every generated report.** A
  `report.md.aida.json` (or one per run) naming conversation / workspace /
  profile / model, input paths with size+mtime and SHA-256 on demand, MCP
  server and tool names, tool arguments and artifact paths, the generated
  script and interpreter, timestamps and AIDA version. Almost all of it
  already exists in the session, tool log, artifact store and config; the
  first version is plain JSON with no schema migration. For a scientist
  this answers "what exactly produced this plot?", which is worth more
  than most agentic features — and it is the natural by-product of a
  Phase 10 workflow run, so **build it together with §1.2, not before**.
- **First-class table and JSON artifact cards.** `TableArtifact` and
  `JsonArtifact` exist but only image/file artifacts get frontend creation
  events, so structured results are flattened into the tool-call detail
  text. Add `TableArtifactCreated`/`JsonArtifactCreated` plus compact Qt
  cards (preview rows or tree, copy, export CSV/JSON). No dataframe or
  plotting dependency needed.
- **Repeat a tool call with edited arguments.** The MCP log already keeps
  name, arguments, result, timing and error. A button that reopens the
  arguments as editable JSON and resubmits through the normal confirmation
  path — plus "save this invocation as a Quick Task" — turns successful
  exploration into repeatable practice, and is a much smaller step than
  §1.2's workflows.
- **Source freshness indicators.** Compare a saved input's size/mtime to
  the file on disk when showing an artifact or resuming a conversation, and
  say "source changed since analysis" instead of letting an old result look
  current. Size/mtime by default so large HDF5/NXcanSAS files stay cheap;
  hash only on request. Pairs with the manifest above.
- **MCP tool allowlists, not only denylists.** `disabled_tools` cannot stop
  an upstream server update from silently adding twenty new schemas — or a
  new mutating tool — to a workspace that had reviewed the old set. An
  `enabled_tools` allowlist/preset can, and it is leaner. Show the resolved
  workspace's estimated schema-token budget next to the group tool count
  (§1.5 shipped the count). Directly helps small local models. Cheap
  enough to graduate to §1 whenever a workspace's tool list gets noisy.
- **Local feedback and a diagnostic bundle.** A thumbs-up/down plus
  optional note stored locally on an answer or tool run, and an "Export
  diagnostic bundle" producing sanitized logs, active configuration *names*
  (never secrets), tool schemas, version/platform data and the relevant
  event trace. Makes §1.1 beta reports actionable with no telemetry and no
  service dependency.
- **Parallel read-only RAG retrieval.** `_retrieve_context` awaits
  knowledge bases one at a time; independent read-only queries can
  `asyncio.gather` behind the existing per-KB error isolation. Ingest and
  index build stay sequential unless measured otherwise. The smallest item
  on this list — graduate it the moment a workspace with several knowledge
  bases feels slow.

### 2.7 MCP presets and integrations (2026-08-31 review)

The review's framing is worth keeping: the valuable thing is **not a
catalog** but a small *audited preset* system generalized from
`aida.mcp.pyirena_setup` — pinned command and package version, a narrow
default tool set, a group, skills, scratch/output directory, keyring
references, confirm-before-run defaults, and a `doctor` check per preset.
§1.5's "extend one-click MCP setup beyond pyIrena" is that mechanism; this
is the list of what to point it at, in the review's priority order.

- **Playwright MCP — first preset after `bait_mcp`.** Matches the
  business-system use case directly, and upstream explicitly states it is
  *not* a security boundary, so AIDA's confirmation layer stays load-bearing.
  Preset defaults: pin a tested version rather than `@latest`; `--output-dir`
  into the AIDA scratch folder with a size cap; a per-workspace user-data
  directory or explicit storage state; confirmation on every mutating
  action (submit, upload, download, destructive click); a
  read/navigation-only group for small models and review tasks; never
  business credentials in `mcp.json` (see
  [`planning/credentials_and_browser_automation.md`](planning/credentials_and_browser_automation.md)
  and §2.1). Overlaps the npx/Node note in §2.1 — same work, different half.
- **Jupyter MCP** (Datalayer) — optional, for users whose notebook *is* the
  scientific record; unnecessary for anyone happy with the Phase 9 script
  runner. Start with connect / list / read notebook, insert / update cell,
  execute cell, retrieve output; confirm on writes, deletes and kernel
  restart.
- **Crossref DOI/metadata** — small, read-only, no signup. Surface is small
  enough (`lookup_doi`, `search_works`, `get_references`, maybe
  `lookup_funder`) that an audited wrapper or even narrow native read-only
  tools would be cheap. Real value for paper review and citation checking.
- **Zotero** — connects paper review to the user's actual library, PDFs,
  annotations and tags. The community server ecosystem here is fragmented
  and inconsistent; start local and read-only (search, item metadata, full
  text, annotations) and confirm on notes/tags/attachments/edits. A narrow
  in-house adapter may be less work than absorbing behavioural variation
  across several packages. Registry presence is not an audit — the official
  MCP registry terms disclaim any safety guarantee.
- **GitHub MCP** — useful for AIDA/pyIrena development, not for analysis
  workspaces. Development-only group, minimum token scopes, read-only
  defaults.
- **Narrow MCPs for internal business systems** — where a stable API
  exists, a small domain server beats browser automation for anything
  repeated. Start read-only (sample/status lookup, proposal and run
  metadata, inventory, scheduling, document retrieval); add named writes
  later, with confirmation. Playwright stays the fallback for screens with
  no usable API.

**Decided against**, so it does not get rediscovered: a generic filesystem
MCP (native file tools already carry workspace-aware safety and typed
results); a generic shell/Python MCP (duplicates the script runner and
splits one coherent permission model); a separate memory/RAG MCP (native
persistence and folder RAG are already wired into the UI and workspace
model); a bundle of paper-search servers enabled at once (overlapping
schemas cost tokens and create ambiguity — Crossref plus Zotero first,
PubMed/arXiv/DataCite only on a real need); and remote HTTP/SSE-only
servers until AIDA deliberately implements remote transport, auth and
lifecycle (§2.2).

---

## 3. Known open risk

- **Qt timer/GC native crash in long GUI test runs** — mitigated (per-test GC
  drain, explicit timer stop, CI split into two pytest invocations), not
  mathematically closed; it is a known PySide/PyQt class of issue rather than
  something application code can rule out. If it recurs as the suite grows,
  the next escalation is one pytest process per `tests/ui/*.py` file. Full
  writeup in `planning/improvement_plan_2026-08.md` §6.
