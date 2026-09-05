# AIDA — open work

**A local scientific agent workbench.** Repo `jilavsky/Aida` · import package
`aida` · PyPI distribution `aida-workbench` · MIT · Python >= 3.11 · PySide6.

**Status: 0.1.0b5 (beta), reconciled 2026-09-04.** Phases 1–10's automation
half are implemented, tested (1,700+ tests, three OSes) and in daily use.
This file holds **only what is not done** — anything ticked here has been
moved to `planning/COMPLETED.md`, and anything shipped is dated in
`CHANGELOG.md`. If you find a `[x]` below, it is a bug in this file.

- What AIDA is, and every design decision behind it → [`planning/DESIGN.md`](planning/DESIGN.md)
- What has been delivered, with rationale → [`planning/COMPLETED.md`](planning/COMPLETED.md)
- What shipped in which release → [`CHANGELOG.md`](CHANGELOG.md)
- Per-phase checklists → `planning/phase01…phase10_*.md`
- User-facing setup and configuration → [`docs/`](docs/README.md)

Two companion proposals live at the repo root rather than in §1, because
they are accepted-in-principle but not yet committed work. Items graduate
from them into §1 as they are taken on:

- [`PLAN_INSTRUMENT_INTEGRATION.md`](PLAN_INSTRUMENT_INTEGRATION.md) —
  BeamlineAdvisor retirement, aievaluator, a safety-limited EPICS MCP, and
  the deployment model for `usaxscontrol`.
- [`AIEVALUATOR_EPICS_MCP_SETUP.md`](AIEVALUATOR_EPICS_MCP_SETUP.md) — the
  concrete wiring checklist for those two MCP servers.

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

- [ ] Publish `aida-workbench` 0.1.0b5 to PyPI and verify
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

### 1.2 Phase 10 — the distribution half (`phase10_automation_distribution.md`)

The **automation** half shipped 2026-09-03: `aida run`, stored workflows,
the in-app scheduler with its deferral policy, and `docs/workflows.md`.
Full account in [`planning/COMPLETED.md`](planning/COMPLETED.md) §9. What
remains is distribution, plus one deliberate deferral:

- [ ] Release automation: a GitHub Actions publish workflow with the version
      in `pyproject.toml` authoritative and tag-checked.
- [ ] conda: keep `environment.yml` current; evaluate a conda-forge feedstock
      once PyPI releases are routine (record the decision either way).
- [ ] *Deferred, not scheduled:* `aida schedule install/uninstall/status`
      generating native launchd/Task Scheduler/systemd artefacts — only if
      a user actually asks for "logged out, not just closed". `ScheduleEntry.
      trigger` already exists so nothing shipped has to be rewritten for it.

### 1.4 Verification owed (cannot be done from a sandbox)

Every one of these is a *manual* check the phase files left open because no
sandbox can perform it. They are the acceptance evidence for work already
believed complete. Items verified so far — the pyirena-mcp keystone, MCP
group switching, UC2, UC3, and live `ollama-local`/`argo-claude` profiles
— are recorded in `planning/COMPLETED.md`.

- [ ] CI green on all three OSes after the next push (phases 1, 3, 4, 6, 7, 8, 9
      each left this box open for the same reason).
- [ ] bait_mcp connects and lists its tools from AIDA (no instrument needed).
- [ ] **UC5:** check beamline status via an AIEvaluator script plus bait_mcp
      from an AIDA workspace. See `AIEVALUATOR_EPICS_MCP_SETUP.md` for the
      pre-flight commands.
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

Reviewed 2026-08-29 and again 2026-09-04. Everything that was quick and
unambiguous is done and archived in `planning/COMPLETED.md` §7; what is
left below either needs a real UX decision, needs domain knowledge only
the user can supply, or is a read-through rather than a mechanical edit.

- [ ] **Enforce `ruff format` in CI** (external review, P3). `ruff check`
      passes and is gated; `ruff format --check .` currently reports
      **128 files would be reformatted, 97 already formatted**. Two commits,
      in this order: one formatting-only pass with no other change in it
      (so it can be added to `.git-blame-ignore-revs`), then a
      `ruff format --check .` step next to the existing lint step in
      `.github/workflows/ci.yml`. Doing it the other way round makes CI red
      on main. Nothing else in the review's P3 list is still open.
- [ ] **Comment hygiene pass** (external review, P3). Long historical
      "this used to be broken because…" comments are useful while a fix is
      fresh and become false as the code moves on — the review caught two
      that had already gone stale. The rule going forward: a code comment
      states the *current* invariant and why it exists; the history and the
      rationale move to `planning/COMPLETED.md` or a short decision record.
      A read-through, wanted once before 1.0 rather than continuously.
- [ ] Set a workspace's default MCP group from the MCP dialog itself
      (`phase07`) — no longer blocked: the workspace editor it was waiting
      on now exists, so this is adding the control to the MCP dialog and
      writing through to `workspaces.yaml`.
- [ ] Diff-style view when the agent proposes changes to an existing script
      (`phase09`) — **deferred**. An explicit "may slip" nice-to-have when
      Phase 9 shipped, not a cleanup: it needs a UX decision (side-by-side
      pane? inline +/- markup in the transcript? a review dialog before the
      write?) that only the user can settle.
- [ ] Extend the one-click MCP setup pattern beyond pyIrena — `bait_mcp`
      first, then a small offer list of npx-based servers. The detection and
      config-building shape in `aida.mcp.pyirena_setup` generalizes; what
      does not is knowing *which* env vars and group each server wants —
      **deferred**: needs `bait_mcp`'s actual env-var names and group
      conventions; nothing to build against yet.

---

## 2. Considered — discussed, not committed

Kept deliberately. Each line records something already thought through, so it
does not have to be rediscovered — and so it stays *out* of §1 until something
concrete asks for it.

### 2.1 Deployment and multi-user

- **Per-user beamline credentials.** The identity half graduated to §1.3;
  the *secrets* half did not. Per-user provider profiles would mean
  per-user secrets in the OS keychain of one shared login — which the
  keychain does not partition. A shared staff profile is what
  BeamlineAdvisor effectively does today and what §1.3 assumes. Revisit
  only if per-person Argo billing or auditing becomes a requirement.
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
  least make the failure mode explicit. §1.3 makes this materially more
  likely (two people at one beamline machine), so it may need to graduate
  with it — see `planning/multiuser_plan.md` §6.
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
  API has proven stable through the PySide6 app. See
  `PLAN_INSTRUMENT_INTEGRATION.md` §1.3 — this is one of the deployment
  options there, and the one that would give users access without an install.
- **Voice STT input** — macOS dictation already covers it; Windows/Linux would
  mean local Whisper, a heavy dependency. Criterion: real demand at the
  beamline. If ever done: a mic button feeding the normal input box, nothing
  deeper.
- **TTS output** — unclear what it should even mean here. Revisit only if STT
  ships and earns its keep.
- **Extra GUI niceties**: per-display font/scaling profiles, more dockable
  widgets.
- **Context-aware Quick Tasks.** BeamlineAdvisor's `get_suggestions()` is
  ~60 lines of keyword heuristics ("traceback" → "Debug this"; a code block
  in the last reply → "Load into editor"). The same heuristics could drive a
  transient second row of buttons under the input box; the Quick Tasks panel
  and event API already exist. Low value unless users miss it.
- **Native app bundles** (PyInstaller/Briefcase) — a timeboxed investigation
  at most. `pip install aida-workbench[gui]` is acceptable for an audience
  that already installs pyIrena that way.

### 2.4 Knowledge and analysis

- **Figures from documents, and an OCR backend.** The readers are
  text-only, so every figure in an attached paper is dropped — and a
  scanned PDF reads as empty with no warning. The warning and the
  attachment-folder fix graduated to §1.5; what stays here is the richer
  half. The shape decided in discussion (2026-09-04): **do not push
  figures at the model** — an unlabeled blob it cannot name is worse than
  a note saying a figure exists. Ingest a document once into its
  attachments folder, hand the model the text plus a *labeled figure
  index*, and add a `get_document_figure(document, label)` tool so the
  agent pulls the one or two figures it needs. That turns
  `MAX_ATTACHED_IMAGES = 4` from a limitation into the correct budget for
  a pull. It all rests on the index being right, which `pymupdf` alone
  cannot reliably deliver on two-column journal layouts — which is the
  actual argument for an optional **Mistral OCR** backend (three REST
  calls, no new package beyond declaring `httpx`; ~1000 pages per dollar;
  returns reading-ordered markdown with inline image placeholders, so
  caption pairing stops being a layout problem). Off by default, per
  workspace, confirmation on upload, never in a headless run without
  explicit pre-approval, and always falling back to plain text extraction
  when unavailable. Full analysis, including what survives a chat restart
  today, in [`planning/document_images.md`](planning/document_images.md).
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
  sending an analysis session to a colleague. BeamlineAdvisor could export a
  chat as JSON; graduate when someone asks.
- **pynika and other package MCPs** as they appear — should "just work"
  through the Phase 7 management UI; ship starter skills files alongside.

### 2.6 From the external review (2026-08-31)

The review's P1/P2 findings are fixed (`59a4b92`); its P3 remainder is in
§1.5. What follows is the review's *feature* list — recorded here rather
than in §1 because none of it has a concrete need pushing on it yet.
Ordered as the review ordered it, by value-to-complexity. The
reproducibility manifest that headed this list shipped with Phase 10's
workflow runner (`COMPLETED.md` §9) and has been removed from it.

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
  Phase 10's workflows.
- **Source freshness indicators.** Compare a saved input's size/mtime to
  the file on disk when showing an artifact or resuming a conversation, and
  say "source changed since analysis" instead of letting an old result look
  current. Size/mtime by default so large HDF5/NXcanSAS files stay cheap;
  hash only on request. Pairs with the run manifest.
- **MCP tool allowlists, not only denylists.** `disabled_tools` cannot stop
  an upstream server update from silently adding twenty new schemas — or a
  new mutating tool — to a workspace that had reviewed the old set. An
  `enabled_tools` allowlist/preset can, and it is leaner. Show the resolved
  workspace's estimated schema-token budget next to the group tool count
  (§1.5 shipped the count). Directly helps small local models. Cheap
  enough to graduate to §1 whenever a workspace's tool list gets noisy —
  and `PLAN_INSTRUMENT_INTEGRATION.md` §4 wants exactly this for the
  `usaxs-user` / `usaxs-staff` split.
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
