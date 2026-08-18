# Phase 1 — Foundation & configuration

**Goal:** A properly structured, installable, CI-tested package skeleton with the
on-device configuration system (config files, secrets, paths, logging) that every
later phase builds on. No agent functionality yet.

**Prerequisites:** none.
**Unblocks:** Phase 2.
**Use cases advanced:** none directly (infrastructure).

---

## Tasks

### Repository & packaging

- [x] Create repo layout per PLAN.md §3 (`src/aida/`, `tests/`, `examples/`, `planning/`)
- [x] `pyproject.toml`: name `aida`, version `0.0.1`, MIT, `requires-python >= 3.11`,
      extras `gui`, `docs`, `rag`, `dev`; console entry points `aida` (CLI) and
      `aida-gui` (stub for now)
- [x] `environment.yml` for conda env `aida` (mirror pyIrena's style)
- [x] `.gitignore`: ensure `~`-style local config never applies, but explicitly ignore
      `.env`, `*.db`, `logs/`, any `secrets*` pattern inside the repo
- [x] README.md: one-paragraph description, install, status badge, "pre-alpha" notice
- [x] LICENSE (MIT), basic CONTRIBUTING note
- [ ] **Claim `aida` on PyPI**: build and publish 0.0.1 placeholder (name checked
      unclaimed 2026-08-18 — do this early; if the name is rejected, fall back to
      `aida-workbench` and record the change in PLAN.md §2). *Requires a PyPI account
      and cannot be done from the build sandbox — see delivery instructions.*

### Configuration system (`aida.config`)

- [x] `paths.py`: resolve `~/.aida/` (create on first run), records dir default
      `~/Documents/Aida/` (overridable in config.yaml), artifacts dir, logs dir
- [x] `settings.py`: load/validate `config.yaml`, `providers.yaml`, `workspaces.yaml`,
      `mcp.json` with defaults for every missing field (pyIrena rule: old configs must
      always load)
- [x] Config schema versioning field from day one (`config_version: 1`)
- [x] `secrets.py`: `keyring`-backed secret store keyed by profile name;
      environment-variable override (`AIDA_SECRET_<PROFILE>`); **test that no secret
      value can end up serialized into any YAML/JSON**
- [x] Ship commented example configs in `examples/config/` (never auto-copied with
      real values)

### Logging & diagnostics

- [x] Rotating file log in `~/.aida/logs/` + console log level from config
- [x] Log format includes subsystem tag (`provider`, `mcp`, `core`, `ui`, ...) —
      groundwork for "which layer failed" diagnostics
- [x] `aida doctor` CLI command: report Python version, config file status/validity,
      keyring availability, reachable provider endpoints (ping only), writable dirs

### Testing & CI

- [x] pytest scaffold, headless, with per-test timeout (pyIrena pattern)
- [x] Contract test: no Qt import anywhere outside `aida/ui/` (grep-style test)
- [x] Unit tests: paths creation, config load/defaults/roundtrip, secrets set/get/
      env-override, doctor output
- [x] ruff config (line-length 100, pyIrena-style select/ignore)
- [x] GitHub Actions: ruff + pytest on 3.11 and 3.13, ubuntu + macos + windows
      *(workflow written; will run once pushed — see below)*

---

## Acceptance — phase is done when all are checked

- [x] `pip install -e ".[dev]"` succeeds in a fresh env — verified on Linux in the
      build sandbox (23/23 tests pass, `ruff check .` clean). **Not yet verified on
      macOS/Windows** — first push will confirm via CI.
- [x] First run creates `~/.aida/` with valid default configs and no secrets on disk
      — verified (`aida doctor` from a clean `$HOME`).
- [x] `aida doctor` correctly reports a working setup AND correctly flags a broken
      one (bad YAML, missing keyring entry) — verified by
      `tests/test_doctor.py::test_doctor_flags_broken_config` and a manual run.
- [ ] CI green on all three OSes — pending first push to GitHub (cannot run GitHub
      Actions from the build sandbox).
- [ ] `aida` name secured on PyPI (or fallback name recorded in PLAN.md) — pending
      user action (requires a PyPI account/credentials).

## Out of scope for this phase

Any LLM/MCP/GUI functionality; workspace semantics (files exist as config entries
only, validated in Phase 4).

## Notes

Built 2026-08-18. Repository content was assembled in an isolated build sandbox
(no GitHub push access) and handed off for the user to commit/push and to
perform the two account-bound steps (PyPI claim, confirming CI) themselves — see
the delivery message in this session for exact commands.
