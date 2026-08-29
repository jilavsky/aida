# Phase 10 — CLI automation, stored workflows & distribution

> **Still the one open phase as of 0.1.0b2.** Tracked from `PLAN.md` §1.2;
> this file stays the detailed checklist.

**Goal:** AIDA is scriptable from data pipelines (`aida run`), users can record and
replay **named workflows**, a simple scheduler covers timed reports, and the package
ships properly on PyPI (+ conda story). This phase turns AIDA from an app into
infrastructure.

**Prerequisites:** Phases 4 & 6 (core, workspaces, safety). GUI recording parts
need 5.
**Use cases advanced:** UC6; scripted forms of UC3–UC5.

---

## Tasks

### Headless CLI (`aida run`)

- [ ] `aida run --workspace W "prompt text"` — non-interactive: executes one agent
      task, writes outputs to the workspace target folder, exit code reflects
      success, `--json` emits a machine-readable result summary (for pipelines)
- [ ] stdin prompt support and `--input file...` attachments
- [ ] All confirmations in headless mode: fail-with-message by default,
      `--yes-in-allowed` to auto-approve only inside allowed folders — never a
      blanket `--yes`
- [ ] Quiet/verbose logging flags; secrets via keyring or env (Phase 1) so no
      interactive auth is ever needed

### Stored workflows (`aida.core.workflows`)

- [ ] Workflow file format in `~/.aida/workflows/NAME.yaml`: workspace ref +
      ordered steps (prompt templates with `{placeholders}`), optional per-step
      output expectations (e.g. "a file must appear in target")
- [ ] `aida workflow run NAME [--var key=value]`, list/show/validate commands
- [ ] Create from GUI: "save this conversation as workflow" — turns the user's
      prompts into an editable step list (edit as plain YAML; no visual workflow
      builder — deliberate simplicity)
- [ ] Run from GUI: workflow picker executes steps into a normal conversation view
- [ ] Failure semantics: a failed step stops the workflow with a clear report

### Simple scheduler (internal, optional)

- [ ] `aida schedule add NAME --workflow W --every "24h" | --at "07:00"` — a small
      persistent scheduler usable while the app runs, plus documented recipes for
      OS-level scheduling (launchd / Task Scheduler / cron invoking `aida run`) —
      external triggers remain external code invoking the CLI (per plan)
- [ ] Schedule list/remove; last-run status; output lands in target folder (UC6
      report generation)

### Distribution

- [ ] PyPI: real release automation (GitHub Actions publish workflow, version in
      `pyproject.toml` authoritative + tag check — pyIrena's proven pattern)
- [ ] `pip install aida[gui]` → working `aida-gui` verified on clean macOS, Windows,
      Linux machines
- [ ] conda: environment.yml maintained; evaluate conda-forge feedstock (record
      decision)
- [ ] Docs pass: README quickstart, docs/ for configuration reference (providers,
      mcp.json extras, workspaces, safety), skills-authoring guide, workflow guide
- [ ] Investigate (timeboxed, decision recorded, not committed): PyInstaller/
      Briefcase single-app bundles per OS

### Tests

- [ ] `aida run` end-to-end with MockProvider + mock-mcp in CI (exit codes, --json
      schema, headless confirmation behavior)
- [ ] Workflow parse/validate/run tests incl. placeholder substitution and
      failure-stop
- [ ] Scheduler unit tests with a fake clock

---

## Acceptance — phase is done when all are checked

- [ ] **Pipeline demo:** a shell script calls
      `aida run --workspace use-pyirena "reduce and report new data in <folder>" --json`
      and a report MD + figures appear in the target folder, no GUI, no prompts
- [ ] A workflow recorded in the GUI replays from the CLI by name with a changed
      `--var folder=...`
- [ ] **UC6 demo:** scheduled daily report generates on time while the app runs;
      the cron recipe does the same without the app
- [ ] Fresh-machine install test passes on all three OSes from PyPI
- [ ] CI green, publish workflow dry-run verified

## Out of scope for this phase

Visual workflow builders; server/daemon deployment; multi-user anything; app-store
distribution.
