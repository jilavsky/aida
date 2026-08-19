# Phase 9 — Coding support, script execution & web search

**Goal:** BeamlineAdvisor-parity coding features inside AIDA: template-based
generation of small instrument functions, a code editor widget, saving scripts, and
**executing Python/allowlisted commands** in allowed folders (AIEvaluator checks,
beamline status). Plus modular web search. Large programming stays in VS Code —
this is deliberately small-scale.

**Prerequisites:** Phase 6 (safety model). Phase 7 recommended (bait_mcp per-tool
confirm).
**Use cases advanced:** UC5 (and sharpens UC3/UC4).

---

## Tasks

### Code templates (`aida.coding.templates`)

- [ ] Templates folder per workspace (e.g. link to bits-usaxs templates — the
      BeamlineAdvisor pattern); templates are plain `.py` files with docstrings
- [ ] Templates surfaced to the model via context (small sets) — generation prompt
      encourages template-following for instrument functions
- [ ] Saved-scripts folder per workspace (`saved_scripts/` under target folder or
      configured path)

### Code editor widget (GUI)

- [ ] Code panel: syntax-highlighted Python editor (Qt plain-text + highlighter
      first; QScintilla only if genuinely needed)
- [ ] Code blocks in chat get "Open in editor" ; editor has Save (into
      saved-scripts), Save As, and **Run** (below)
- [ ] Diff-style view when the agent proposes changes to an existing script
      (nice-to-have checkbox — may slip)

### Execution (`aida.coding.runner`)

- [ ] Run Python scripts via subprocess: configurable interpreter/conda env per
      workspace (e.g. the `aievaluator` env — `.env` precedent
      `AIEVALUATOR_CONDA_ENV`), cwd inside an allowed folder, configurable timeout,
      captured stdout/stderr shown in a run-output pane and available to the agent
      as a typed result
- [ ] **Command allowlist**: user-editable list of safe commands (e.g. `git status`,
      `ls`, specific scripts); agent tool `run_command` executes only allowlisted
      commands inside allowed folders; anything else → confirmation request
- [ ] Agent tool `run_python_script(path, args)` under the same safety rules
      (script must live in an allowed folder; per-workspace on/off switch)
- [ ] Kill button for runaway processes; no shell=True anywhere

### Web search (modular)

- [ ] `web_search(query)` as a pluggable tool: implementation via a search MCP
      server if a good one exists, else a direct API adapter — the agent core only
      ever sees the tool, per the prior proposal's modularity rule
- [ ] `fetch_url(url)` returning readable text (size-capped); both tools flagged
      network-touching (safety: visible indicator, per-workspace enable)

### Tests

- [ ] Runner: timeout kill, output capture, env selection, cwd containment,
      non-allowed script refused
- [ ] Allowlist matching (exact + arg-pattern) tests; refusal path emits
      confirmation event
- [ ] Template + saved-scripts flow with MockProvider

---

## Acceptance — phase is done when all are checked

- [ ] **UC5 demo (at beamline or against recorded data):** workspace
      "instrument-ops" runs an AIEvaluator check script (correct conda env), agent
      reads the output and summarizes beamline status; a bait_mcp read confirms a
      device value; a bait_mcp write asks for confirmation
- [ ] Ask for "a function to <instrument operation> following our templates" →
      generated code opens in the editor, saves to saved_scripts, runs
- [ ] Non-allowlisted command (`rm -rf` style) is refused/needs confirmation even
      in relaxed mode
- [ ] A runaway script is killed at the timeout; GUI stays responsive
- [ ] Web search answers a question with a fetched source, and can be disabled per
      workspace
- [ ] CI green

## Out of scope for this phase

Full IDE features (VS Code's job); EPICS/pyepics direct integration (bait_mcp is
the instrument path); arbitrary shell access (never by default).
