# Phase 10 — CLI automation, stored workflows & distribution

> **Automation half shipped 2026-09-03** on branch `phase10-in-app-scheduler`
> (merged to `main`). Distribution is still open. Tracked from `PLAN.md`
> §1.2; this file stays the detailed checklist. User-facing usage docs are
> in [`docs/workflows.md`](../docs/workflows.md).
>
> **2026-09-02:** the automation half of this phase got a decision
> document — [`phase10_scheduling_design.md`](phase10_scheduling_design.md).
> It settled the questions this checklist left open (in-app scheduler vs
> the three OS schedulers, workflow file format, headless confirmation and
> secrets, catch-up and overlap semantics) and re-ordered the work: workflows
> complete first, in-app scheduler second, native OS registration deferred
> indefinitely (not just "last"). Where the two disagree, the design
> document is current. It also gained a 2026-09-03 addendum (§7) on
> deferring scheduled runs while the user is active, added after GUI
> testing surfaced the gap.

**Goal:** AIDA is scriptable from data pipelines (`aida run`), users can record and
replay **named workflows**, a simple scheduler covers timed reports, and the package
ships properly on PyPI (+ conda story). This phase turns AIDA from an app into
infrastructure.

**Prerequisites:** Phases 4 & 6 (core, workspaces, safety). GUI recording parts
need 5.
**Use cases advanced:** UC6; scripted forms of UC3–UC5.

---

## Tasks

### Headless CLI (`aida run`) — done

- [x] `aida run --workspace W "prompt text"` — non-interactive: executes one agent
      task, writes outputs to the workspace target folder, exit code reflects
      success, `--json` emits a machine-readable result summary (for pipelines)
- [x] stdin prompt support and `--input file...` attachments
- [x] All confirmations in headless mode: fail-with-message by default,
      `--yes-in-allowed` to auto-approve only inside allowed folders — never a
      blanket `--yes`; MCP `confirm_before_run` tools need explicit
      `--preapprove-tool`
- [x] secrets via env var (`AIDA_SECRET_<PROFILE>`, already existed) so no
      interactive auth is ever needed; `aida doctor` checks it per profile.
      (No separate quiet/verbose logging flags were added — not requested,
      `--json` already gives a clean machine-readable path.)

### Stored workflows (`aida.core.workflows`) — done

- [x] Workflow file format in `~/.aida/workflows/NAME.yaml`: workspace ref +
      ordered steps (prompt templates with `{placeholders}`), optional per-step
      output expectations (e.g. "a file must appear in target")
- [x] `aida workflow run NAME [--var key=value]`, list/show/validate commands
- [x] Create from GUI: "save this conversation as workflow" — turns the user's
      prompts into an editable step list (edit as plain YAML; no visual workflow
      builder — deliberate simplicity)
- [x] Run from GUI: workflow picker (Workflows… management dialog) executes
      steps into a normal conversation view
- [x] Failure semantics: a failed step stops the workflow with a clear report

### Simple scheduler (internal) — done, plus a busy-guard added from testing

- [x] `aida schedule add NAME --workflow W --every "24h" | --at "07:00"` — a small
      persistent scheduler usable while the app runs (in-app only — the design
      doc dropped the "documented OS-level recipes" half of this item as its
      own deliverable; `aida run`/`workflow run` already compose with any
      external scheduler with zero AIDA-side work, so there was nothing to add)
- [x] Schedule list/remove/enable/disable; last-run status (GUI dialog +
      `aida schedule list`); output lands in target folder (UC6 report
      generation); `aida schedule watch` for a headless box; `aida schedule
      run NAME` to force a fire now
- [x] **Not originally scoped:** defer a due job while the user is actively
      using AIDA rather than firing on top of them — hard-blocks on a
      running turn, soft-blocks (waived after a cap) on unsent text/recent
      activity. Status bar **⏳ N jobs waiting** indicator. See
      `phase10_scheduling_design.md` §7 and `docs/workflows.md`.

### Distribution

- [ ] PyPI: real release automation (GitHub Actions publish workflow, version in
      `pyproject.toml` authoritative + tag check — pyIrena's proven pattern)
- [ ] `pip install aida[gui]` → working `aida-gui` verified on clean macOS, Windows,
      Linux machines
- [ ] conda: environment.yml maintained; evaluate conda-forge feedstock (record
      decision)
- [x] Docs pass, workflow guide part: [`docs/workflows.md`](../docs/workflows.md)
      (README quickstart and the rest of the config-reference docs already
      existed pre-Phase-10 — see `docs/README.md`)
- [ ] Investigate (timeboxed, decision recorded, not committed): PyInstaller/
      Briefcase single-app bundles per OS

### Tests — done

- [x] `aida run` end-to-end with MockProvider + mock-mcp (exit codes, --json
      schema, headless confirmation behavior)
- [x] Workflow parse/validate/run tests incl. placeholder substitution and
      failure-stop
- [x] Scheduler unit tests with a fake clock, incl. catch-up-fires-once,
      overlap-skips, a clock jumped backwards, and the user-busy deferral
      policy (1705 tests total across non-GUI + GUI suites)

---

## Acceptance — phase is done when all are checked

> The automation half (first three items) has been hand-tested through the
> GUI end to end (2026-09-03, "scheduler works and it all makes sense") —
> Checkpoints A/B/C from the implementation plan, plus the deferral guard.
> Left unchecked below because the exact scripted forms haven't been run
> verbatim; check them off once actually run that way.

- [ ] **Pipeline demo:** a shell script calls
      `aida run --workspace use-pyirena "reduce and report new data in <folder>" --json`
      and a report MD + figures appear in the target folder, no GUI, no prompts
- [ ] A workflow recorded in the GUI replays from the CLI by name with a changed
      `--var folder=...`
- [ ] **UC6 demo:** scheduled daily report generates on time while the app runs;
      the cron recipe does the same without the app (no cron recipe was
      written — deliberately out of scope, see "Why no OS-level scheduler"
      in `docs/workflows.md`; the in-app half is verified)
- [ ] Fresh-machine install test passes on all three OSes from PyPI
      (distribution — not part of the automation work just shipped)
- [ ] CI green, publish workflow dry-run verified

## Out of scope for this phase

Visual workflow builders; server/daemon deployment; multi-user anything; app-store
distribution.
