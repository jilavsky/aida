# AIDA — open work

**A local scientific agent workbench.** Repo `jilavsky/Aida` · import package
`aida` · PyPI distribution `aida-workbench` · MIT · Python >= 3.11 · PySide6.

**Status: 0.1.0b1 (beta), 2026-08-28.** Phases 1–9 are implemented, tested
(1319 tests, three OSes) and in daily use. This file now holds **only what is
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

- [ ] Publish `aida-workbench` 0.1.0b1 to PyPI and verify
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

The one phase not started. `aida run` exists as a stub that prints "not yet
implemented".

- [ ] `aida run --workspace W "prompt"` — non-interactive single turn: exit
      codes, `--json` output, stdin prompt, `--input file…` attachments.
- [ ] Headless confirmation policy: fail-with-message by default, with an
      explicit opt-in flag for unattended relaxed operation.
- [ ] Stored named workflows in `~/.aida/workflows/NAME.yaml` — workspace ref
      plus steps, placeholder substitution, `aida workflow run/list/show/validate`.
- [ ] "Save this conversation as a workflow" from the GUI, and a workflow
      picker that runs one into a normal conversation view.
- [ ] Failure semantics: a failed step stops the workflow with a clear report.
- [ ] Simple scheduler: `aida schedule add NAME --workflow W --every 24h |
      --at 07:00`, list/remove, last-run status, output into the target folder.
- [ ] Tests: `aida run` end-to-end with MockProvider + mock-mcp in CI,
      workflow parse/validate/run, scheduler against a fake clock.
- [ ] Release automation: a GitHub Actions publish workflow with the version
      in `pyproject.toml` authoritative and tag-checked.
- [ ] conda: keep `environment.yml` current; evaluate a conda-forge feedstock
      once PyPI releases are routine (record the decision either way).

### 1.3 Context-window management and compaction — done (2026-08-28)

The failure this prevented: a long pyIrena analysis conversation dying
halfway through, with no in-app recovery. Full design, steps, and measured
numbers in
[`planning/context_management.md`](planning/context_management.md) (now
marked implemented); user-facing docs in
[`docs/context-and-limits.md`](docs/context-and-limits.md).

- [x] **Count what is actually sent.** MCP tool schemas, tool-call
      arguments/results (dense-estimated), and vision images are now all
      counted (`aida.core.context.estimate_tool_schema_tokens`/
      `estimate_message_tokens`) — previously the budget summed only the
      plain message list, missing pyirena-mcp's measured ~10,200 tokens of
      schema JSON on every request.
- [x] **Per-profile `context_window`.** `ProviderProfile.context_window`
      (falls back to `AppConfig.max_context_tokens`) plus
      `aida.core.context.history_budget` (context window × 0.85 safety
      fraction, minus reserved output tokens, minus tool-schema tokens,
      clamped to an 8000-token floor). Editable in the Providers… dialog
      and `providers.yaml`.
- [x] **Context-fullness visibility.** A `[context]` line after every CLI
      turn and a **Context: Nk / Mk (P%)** GUI status-bar label, separate
      from the cumulative **Session total:** label.
- [x] **Compaction.** `ChatSession._trim_context`/`_apply_trim_plan`
      summarize dropped turns via the active provider instead of discarding
      them, with a `/compact` CLI command and a **Compact Conversation**
      GUI action for a manual trigger at a task boundary. Falls back to
      plain trimming if the summarization call itself fails.
- [x] **Recovery from a full context.** Compaction (automatic and manual)
      is the fix — a conversation now recovers headroom instead of every
      later turn failing identically.
- [x] Documented in `docs/context-and-limits.md`, cross-linked from
      `docs/providers-and-secrets.md`, `docs/mcp-servers.md`, and
      `docs/pyirena.md`.

### 1.4 Verification owed (cannot be done from a sandbox)

Every one of these is a *manual* check the phase files left open because no
sandbox can perform it. They are the acceptance evidence for work already
believed complete.

- [ ] CI green on all three OSes after the next push (phases 1, 3, 4, 6, 7, 8, 9
      each left this box open for the same reason).
- [ ] `aida chat --profile ollama-local` against a real local model, and
      `--profile argo-claude` through the ANL proxy on-site.
- [ ] **Keystone, against the real thing:** a real model calling real
      pyirena-mcp, PNG decoded and displayed, saved to disk.
- [ ] bait_mcp connects and lists its tools from AIDA (no instrument needed).
- [ ] Switching MCP groups demonstrably changes the tool list the model sees.
- [ ] **UC2:** drop a PDF and an MD file on the GUI, ask questions, get a new
      MD plus figures sidecar in the target folder.
- [ ] **UC3 full:** "find data in <source folder> with Rg 20–50 Å, plot them,
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

- [ ] Diff-style view when the agent proposes changes to an existing script
      (`phase09`, left open).
- [ ] Per-workspace extra skills selection beyond server-linked ones
      (`phase07`, left open).
- [ ] Show estimated tool count per group in the MCP dialog — the reminder of
      why lean groups matter for small local models (`phase07`).
- [ ] Set a workspace's default MCP group from the MCP dialog itself
      (`phase07`).
- [ ] Let the model place images *within* a generated report rather than
      always appended at the end — `write_markdown_report` currently emits all
      figures after the body.
- [ ] A cheaper `AnthropicProvider.ping()`: today every doctor run and profile
      validation sends a real 1-token paid message.
- [ ] Extend the one-click MCP setup pattern beyond pyIrena — `bait_mcp`
      first, then a small offer list of npx-based servers. The detection and
      config-building shape in `aida.mcp.pyirena_setup` generalizes; what
      does not is knowing *which* env vars and group each server wants,
      which is exactly the per-server knowledge that makes it worth doing.
- [ ] Offer to install the bundled skills from the GUI generally, not only
      as a side effect of `add-pyirena` (`install_bundled_skills` already
      does the work).

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

---

## 3. Known open risk

- **Qt timer/GC native crash in long GUI test runs** — mitigated (per-test GC
  drain, explicit timer stop, CI split into two pytest invocations), not
  mathematically closed; it is a known PySide/PyQt class of issue rather than
  something application code can rule out. If it recurs as the suite grows,
  the next escalation is one pytest process per `tests/ui/*.py` file. Full
  writeup in `planning/improvement_plan_2026-08.md` §6.
