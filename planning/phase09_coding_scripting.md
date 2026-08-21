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

- [x] Templates folder per workspace (e.g. link to bits-usaxs templates — the
      BeamlineAdvisor pattern); templates are plain `.py` files with docstrings
      (`WorkspaceConfig.templates_dir`, `aida.coding.templates.load_templates`)
- [x] Templates surfaced to the model via context (small sets) — name +
      docstring only, injected via `start_session`'s `extra_context_texts`
      (`templates_context_text`), same slot `build_workspace_context_block`
      already uses
- [x] Saved-scripts folder per workspace (`saved_scripts/` under target folder or
      configured path) — `WorkspaceConfig.saved_scripts_dir` +
      `resolved_saved_scripts_dir()`, written to by the code editor's Save/Save
      As buttons

### Code editor widget (GUI)

- [x] Code panel: syntax-highlighted Python editor (`aida.ui.qt.python_highlighter.PythonHighlighter`,
      a small regex-based `QSyntaxHighlighter` over a plain `QPlainTextEdit` —
      QScintilla never proved necessary) in `aida.ui.qt.code_editor_dialog.CodeEditorDialog`,
      opened from a toolbar action or from a chat message's own button
- [x] Code blocks in chat get "Open in editor" (`MessageBubble`'s button,
      shown whenever a message contains a fenced code block, extracts the
      first block — same v1 whole-message scope the Copy button already
      uses); editor has Save (into saved-scripts), Save As, and **Run**
      (via `ChatBridge.run_script`, so a real subprocess never blocks Qt)
- [ ] Diff-style view when the agent proposes changes to an existing script
      (nice-to-have checkbox — dropped for this pass, per its own "may slip")

### Execution (`aida.coding.runner`)

- [x] Run Python scripts via subprocess: configurable interpreter per workspace
      (`WorkspaceConfig.python_interpreter` — a direct path to a conda/venv env's
      python executable, e.g. `~/miniconda3/envs/aievaluator/bin/python`, not a
      conda-activate shell-out; the `AIEVALUATOR_CONDA_ENV` use case is satisfied
      by pointing this at that env's own python directly), cwd inside an allowed
      folder, configurable timeout, captured stdout/stderr returned as a typed
      `ToolResult` — the run-output *pane* (GUI) is still open below
- [x] **Command allowlist**: user-editable list of safe commands (e.g. `git status`,
      `ls`, specific scripts, exact match or a trailing `*` wildcard) —
      `AppConfig.command_allowlist` (global) + `WorkspaceConfig.command_allowlist`
      (per-workspace, additive); agent tool `run_command` executes only
      allowlisted commands inside allowed folders; anything else → confirmation
      request (`SafetyGuard.authorize_execute`)
- [x] Agent tool `run_python_script(path, args)` under the same safety rules
      (script must live in an allowed folder — `SafetyGuard.authorize_run_script`,
      mode-governed like write/delete, not allowlist-gated; per-workspace on/off
      switch — `WorkspaceConfig.scripting_enabled`)
- [x] Runaway processes killed at the timeout (`asyncio.create_subprocess_exec` +
      `proc.kill()`); no `shell=True` anywhere. GUI Kill button
      (`CodeEditorDialog`/`ChatBridge.cancel_script_run`) can also kill a run
      manually before its timeout

### Web search (modular)

- [x] **Decided:** no bespoke `web_search` tool. AIDA already has full MCP client
      infrastructure (Phase 3/7) — `web_search` is satisfied by pointing a
      workspace at an existing community search MCP server (Brave/Tavily/etc.,
      the user's own vendor/API key choice) via the already-built `aida mcp add`.
      Zero new AIDA dependency or secret-handling code; `mcp_group`/
      `disabled_tools`/`confirm_tools` already give per-workspace enable +
      per-tool visibility. See PLAN.md §5.
- [x] `fetch_url(url)` returning readable text (size-capped, stdlib
      `urllib.request` only — no new dependency); always requires confirmation
      (no folder concept applies to a URL) — that per-call gate *is* the
      "visible indicator" this task asked for, so no separate per-workspace
      toggle was added.

### Tests

- [x] Runner: timeout kill, output capture, env selection, cwd containment,
      non-allowed script refused (`tests/test_coding_runner.py`,
      `tests/test_coding_tools.py`)
- [x] Allowlist matching (exact + arg-pattern) tests; refusal path emits
      confirmation event (`tests/test_command_allowlist.py`,
      `tests/test_workspace_safety.py`)
- [x] Template + saved-scripts flow with MockProvider — template loading/context
      injection (`tests/test_coding_templates.py`), and the
      generate→open-in-editor→save→run flow end-to-end against a real
      `ChatBridge` (`tests/ui/test_main_window.py`,
      `tests/ui/test_code_editor_dialog.py`)

---

## Acceptance — phase is done when all are checked

- [ ] **UC5 demo (at beamline or against recorded data):** workspace
      "instrument-ops" runs an AIEvaluator check script (correct conda env), agent
      reads the output and summarizes beamline status; a bait_mcp read confirms a
      device value; a bait_mcp write asks for confirmation — real hardware/env,
      manual/out-of-sandbox-scope like every prior phase's real-hardware item
- [x] Ask for "a function to <instrument operation> following our templates" →
      generated code opens in the editor, saves to saved_scripts, runs
      (automated end-to-end in `tests/ui/test_main_window.py`)
- [x] Non-allowlisted command (`rm -rf` style) is refused/needs confirmation even
      in relaxed mode (`SafetyGuard.authorize_execute`,
      `tests/test_workspace_safety.py`)
- [x] A runaway script is killed at the timeout; GUI stays responsive
      (`tests/test_coding_runner.py`, `tests/ui/test_code_editor_dialog.py`)
- [x] Web search answers a question with a fetched source, and can be disabled per
      workspace — via an MCP search server + `mcp_group`, not a bespoke tool (see
      the "Decided" note above); manual verification needs a real search MCP
      server/API key, same "real external service, out of sandbox scope" limit
      every prior phase's real-provider items carry
- [ ] CI green — pending the user's next Windows/Linux CI run, same as every
      other change this session

## Out of scope for this phase

Full IDE features (VS Code's job); EPICS/pyepics direct integration (bait_mcp is
the instrument path); arbitrary shell access (never by default).
