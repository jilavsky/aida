# AIDA — where we are, and what 1.0.0 needs

**Reviewed 2026-10-16, against `main` at `e221e09`; 1.0.0 cut the same
day.** This file answers two questions and nothing else: *is AIDA ready
to be called 1.0?* and *what is actually left?* It is the working list;
`PLAN.md` keeps the reasoning behind each item and
`planning/COMPLETED.md` keeps what is already done.

---

## Verdict: yes, ship 1.0.0

Nothing open is a missing feature. Every one of the ten phases is
implemented and in daily use; the whole of `PLAN.md` §1 is now either
verification that needs real hardware, housekeeping, or something
explicitly deferred. The feature set matches what `planning/DESIGN.md` §1
set out to build, including the pieces that were mid-priority guesses at
the time (RAG, the scheduler, portability).

### Evidence as of this review

| Check | Result |
|---|---|
| Test suite | **2426 passed, 0 failed** (one process, macOS, Python 3.11) |
| CI, all three OSes | **green** — run `38281164466`, six matrix legs (ubuntu/macos/windows × 3.11/3.13) plus the `package` job |
| `ruff check .` | clean |
| `ruff format --check` | clean across `src/` and `tests/` (254 files), and gated in CI |
| Layering rules (`DESIGN.md` §3) | hold — no Qt outside `aida.ui.qt`, no direct `PySide6` import outside `_qt.py`, no matplotlib in core |
| PyPI | `aida-workbench` 0.2.0 published; `publish.yml` ran for real on the v0.2.0 release |
| `aida doctor` on a live install | 27/31, the four failures being the config bug fixed below |
| CLI smoke (`workspace`/`kb`/`workflow`/`schedule`/`conversations`) | all work |

### What "1.0" is being claimed

That the feature set is complete for its stated audience, the config and
file formats are stable, and breaking changes from here get a major
version. It is *not* a claim that every manual acceptance demo in
`planning/completed/` has been performed — see §3.

---

## 1. Cutting the tag

- [x] **Version bumped to 1.0.0** in `pyproject.toml` and
      `src/aida/__init__.py`. `tests/test_version_metadata.py` fails if
      they ever disagree, and `publish.yml` re-checks both against the git
      tag before it builds anything.
- [x] **Status banners updated** — `README.md` and the 12 `docs/*.md`
      that carry one. The README's also dropped "First public release"
      and the "breaking changes before 1.0 will be called out" sentence,
      which 1.0 replaces with the real promise: a breaking change to a
      config format or a CLI command now means a new major version.
      Two stale README cross-references went with it (`PLAN.md` §1 for
      the rationale — that moved to `planning/DESIGN.md` §1 — and
      `environment.yml`'s extras).
- [x] **`CHANGELOG.md`'s `[Unreleased]` closed as `[1.0.0]`**, dated
      2026-10-16, with a compare-link and a short preamble saying what
      1.0 claims.
- [x] **`CONTRIBUTING.md` no longer says "AIDA is early-stage (beta)"**,
      and no longer points contributors at `planning/phaseNN_*.md` for
      open work — wrong twice over, since those files moved and open work
      lives here. Its "Cutting a release" section now describes the real
      process (tag, GitHub release, trusted publishing) instead of a
      manual `twine` upload.
- [ ] **Tag `v1.0.0` and publish a GitHub release** — the one step left.
      `.github/workflows/publish.yml` fires on release publication and
      uploads through trusted publishing; no manual `twine`. It refuses
      to build if the tag and the two version strings disagree.

Six docs carry no status banner at all (`axis-camera`, `concepts`,
`documents`, `moving-and-sharing`, `organizing-conversations`,
`talk-intro-slide`). Either convention is fine, but it is worth picking
one: a reader who sees a version on some pages reads its absence on the
others as "unversioned, maybe unmaintained".

## 2. Fixes found in this review

Applied on this branch, with tests. Listed so they can be checked rather
than taken on trust.

- [x] **`aida doctor` reported four `[FAIL]`s on a working install.**
      `ProviderProfileFormDialog.result_profile` defaulted a blank
      "Secret ref" field to the profile's own name
      (`secret_ref=... or name`), so every profile created in the GUI
      claimed a secret. For a keyless local Ollama or LM Studio endpoint
      nothing is ever stored under that name, and doctor's own
      "a profile without `secret_ref` ... is skipped" branch was therefore
      unreachable from the GUI. Four red lines on an installation that
      worked perfectly — and this is exactly the "is a provider problem
      *findable*?" risk `PLAN.md` §1.1 names, inverted into crying wolf.
      Blank now means no secret unless a secret value was typed alongside
      it; the embedding-profile form had the same bug and the same fix.
- [x] **The same check now names the real remedy.** Profiles already
      written with the bad default still fail — correctly, since they do
      reference a secret that does not exist. When the profile's
      `base_url` is a loopback address the message now adds that deleting
      the `secret_ref:` line is probably what you want.
- [x] **`aida --version` did not exist.** The first question on every bug
      report. Added `-V`/`--version`, handled above the dispatch table so
      it answers even when the config that would otherwise load is the
      broken thing.
- [x] **`aida doctor` never said which AIDA produced the report.** Now the
      first line.
- [x] **`aida --help` leaked internal phase numbers** ("Interactive chat
      (Phase 2)"). Planning vocabulary in user-facing output; removed, with
      a test that keeps it out.
- [x] **A chatty `KEEP` renamed the conversation to "KEEP — the current
      title still fits".** `clean_title` compared the whole reply to the
      sentinel, so any model that explained itself — which small local
      models do, and they are the ones AIDA has to work with — had its
      explanation installed as the title. Matched as a leading word now,
      with a word boundary so "Keeping the sample cell at 40 C" survives.
- [x] **`publish.yml` had no tag check**, though `DESIGN.md` §9 specifies
      "version in `pyproject.toml` authoritative, tag-checked". A release
      tagged `v1.0.0` cut from a tree still saying `0.2.0` would have
      published 0.2.0 under that tag, unfixably — PyPI does not allow a
      re-upload. Now checked, along with `pyproject` vs `aida.__version__`.
- [x] **`PLAN.md` referenced a §1.3 that no longer exists** (three times) —
      the multi-user section, removed when it shipped. Rewritten to point
      at the shipped work.
- [x] **`PLAN.md` §1.5 and §1.2 listed work that is already done.**
      "Enforce `ruff format` in CI" reported 128 files needing
      reformatting; the tree is clean and the CI step exists. "Release
      automation: a GitHub Actions publish workflow" — `publish.yml`
      exists and has run. Both now reflected in §4 below.

### Smaller things, also fixed here

- [x] **`environment.yml` omitted the `ocr` extra** that CI installs, so
      the documented conda path exercised different optional-import
      branches than CI did. It is only `httpx`, already pulled in
      transitively, so adding it costs nothing.
- [x] **No `Programming Language :: Python :: 3.12` classifier**, though
      `requires-python = ">=3.11"` allows it. Added.
- [x] **`SkillsPanel.set_skills` could flash stale checkboxes.** It took
      the old widgets out of the layout and queued them for deletion
      without unparenting them, so they kept painting at their old
      geometry until the event loop caught up. One line.
- [x] **`planning/COMPLETED.md` stopped at 2026-09-05 (§11)**, leaving
      0.1.0, 0.2.0 and the round after them recorded only as changelog
      bullets. §12 now covers the six weeks from 0.1.0b1 to 1.0.0 — the
      portability work, remote MCP, MCP at pyIrena scale, conversation
      naming, the skills panel, and the long tail of Windows and Linux
      fixes a beta is actually for.
- [x] **A dead cross-repo link** in `planning/mcp_tool_scaling.md` pointed
      at a pyIrena planning file that has since been reorganized away.
      Named rather than linked now, since a `../../pyirena/` path cannot
      resolve for anyone but us anyway.

### Still open, not blocking

- [x] **The four stale `secret_ref:` lines removed** from the live
      `~/.aida/providers.yaml` (`Ollama-muse`, `ollama-gemma`,
      `ollama-best`, `Ollama-fast`). `aida doctor` now reports **28/28**.
      Worth knowing for anyone else hitting this: blanking the field in a
      *running* GUI does not help, because that instance still has the
      pre-fix code loaded and writes the profile name straight back —
      edit the YAML, or restart the GUI first.
- [x] **`Claude outputs/` committed** — the finished presentation
      figures and the Bernardyn 1.0 / pyIrena 1.2.0 content update. Its
      two diagram scripts are excluded from ruff rather than reformatted:
      they are written as layout tables, one aligned row per box, and the
      formatter explodes each row into eight lines, destroying the
      property that makes them editable. Same argument as the existing
      Markdown exclusion. The rest of the tree still passes
      `ruff format --check` with no exceptions.

## 3. Manual verification owed — the honest gap

None of this blocks a 1.0 tag. All of it is acceptance evidence for work
believed complete, and none of it can be done from a development machine.
This replaces the per-phase `[ ]` boxes still visible in
`planning/completed/` — that is the one place they are tracked now.

**Already verified** and recorded in `PLAN.md` §1.4: the pyirena-mcp
keystone (an MCP image becoming a real artifact), MCP group switching,
UC2, UC3, and live `ollama-local` / `argo-claude` profiles. CI on three
OSes was verified in this review and the boxes are ticked.

### At a real beamline machine

- [ ] **UC5** — beamline status via an AIEvaluator script plus `bait_mcp`
      from an AIDA workspace. Pre-flight commands in
      `planning/AIEVALUATOR_EPICS_MCP_SETUP.md`.
- [ ] **`bait_mcp` connects and lists its tools** from AIDA. No instrument
      needed; the cheapest item on this list.
- [ ] **pyIrena coexistence on a real beamline machine** — Windows and
      macOS, under conda rather than a venv. Verified so far only in a
      clean Linux venv, both install orders; conda's resolver and Windows
      path handling are the ones that actually matter. See
      `docs/pyirena.md`.

### Knowledge bases

- [ ] **UC1 in full** — a documentation folder indexed, answers citing
      retrieved passages, index rebuilt from the GUI, the same knowledge
      base working with local *and* Argo embeddings, and the ten canned
      USAXS questions answered with correct citations.
- [ ] **RAG benchmark on a real corpus** (USAXS instructions + a pyIrena
      docs folder + an Obsidian vault). This one has a consequence:
      retrieval currently loads every chunk vector per query, and the
      escape hatch to a vector DB (`PLAN.md` §2.4) is only justified by
      measurement. Current indexes are ~11,800 / ~2,800 / ~2,000 chunks,
      which is already past the ~10k figure §2.4 names as the point to
      look again.

### GUI, on each OS

- [ ] **Manual smoke checklist on macOS and Windows**, including: the Stop
      button interrupting a long generation cleanly with the app still
      usable afterwards, and the flagship pyIrena demo on both a cloud and
      a local Ollama model.
- [ ] **Clean-machine install from PyPI on all three OSes** —
      `pip install "aida-workbench[gui,docs]"` → working `aida-gui`. Worth
      doing *after* the 1.0 upload rather than before.

### Automation

- [ ] **UC6** — a scheduled daily report generating on time while the app
      runs, and a workflow recorded in the GUI replaying from the CLI by
      name with a changed argument.

## 4. Open work after 1.0

Carried from `PLAN.md` §1, with the items this review closed removed.

- [ ] **conda-forge feedstock** — evaluate once PyPI releases are routine,
      and record the decision either way. `environment.yml` stays current
      regardless. (`PLAN.md` §1.2)
- [ ] **Comment hygiene pass** — long "this used to be broken because…"
      comments go stale as the code moves on; the rule is that a comment
      states the current invariant and the history moves to
      `COMPLETED.md`. A read-through, wanted once. (`PLAN.md` §1.5)
- [ ] **Set a workspace's default MCP group from the MCP dialog** — no
      longer blocked; the workspace editor it waited on exists.
      (`PLAN.md` §1.5, `phase07`)
- [ ] **Portability Tier 3 — full backup and restore.** `aida.db`,
      `artifacts/`, `knowledge/` on top of the Tier 1 bundle. The real
      work is rewriting the absolute paths recorded *inside* the DB
      through the same token map. Graduates when moving between beamline
      workstations becomes routine. (`PLAN.md` §1.6)

**Deferred by decision, not forgotten:** native launchd/Task Scheduler
artefacts for "logged out, not just closed"; a diff view when the agent
proposes changes to an existing script (needs a UX decision); extending
one-click MCP setup past pyIrena (needs `bait_mcp`'s real env-var names);
PyInstaller/Briefcase bundles; a passphrase-encrypted secrets sidecar.
Everything considered and consciously not committed is in `PLAN.md` §2 —
including §2.8's sample-holder recognition from the USAXS camera, which is
the largest piece of thinking parked there.

## 5. Known risk, carried into 1.0

**Qt timer/GC native crash in long GUI test runs.** Mitigated — per-test
GC drain, explicit timer stop, CI split into two pytest invocations — not
mathematically closed, because it is a known PySide/PyQt class of issue
rather than something application code can rule out. It has not recurred;
this review ran the full 2426-test suite in a *single* process, the
combination CI deliberately avoids, without a crash. If it returns as the
suite grows, the next escalation is one pytest process per `tests/ui/*.py`
file. Full writeup in
`planning/completed/improvement_plan_2026-08.md` §6.

---

## Where everything lives

| | |
|---|---|
| What is open | **this file**, and `PLAN.md` for the reasoning |
| What was considered and deliberately not committed | `PLAN.md` §2 |
| Why AIDA is shaped the way it is | `planning/DESIGN.md` |
| What has been delivered, with rationale | `planning/COMPLETED.md` |
| What shipped in which release | `CHANGELOG.md` |
| The per-phase checklists, archived | `planning/completed/` |
| User-facing setup and configuration | `docs/` |
