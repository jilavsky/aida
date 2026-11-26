# AIDA — what is left to do

**Updated 2026-11-26.** Only open items live here. What shipped is in
`CHANGELOG.md` and, with the reasoning, `planning/COMPLETED.md`. Why each
item is shaped the way it is, and everything deferred by decision, is in
`PLAN.md` (§1 planned, §2 considered and not committed, §3 known risk).

## 1. Manual verification owed

Acceptance evidence for work believed complete. None of it can be done
from a development machine.

### At a real beamline machine

- [ ] **`bait_mcp` connects and lists its tools** from AIDA. No instrument
      needed; the cheapest item on this list.
- [ ] **UC5**: beamline status via an AIEvaluator script plus `bait_mcp`
      from an AIDA workspace. Pre-flight commands in
      `planning/AIEVALUATOR_EPICS_MCP_SETUP.md`.
- [ ] **pyIrena coexistence** on Windows and macOS, under conda rather
      than a venv. Verified so far only in a clean Linux venv, both install
      orders; conda's resolver and Windows path handling are the ones that
      matter. See `docs/guides/pyirena.md`.

### Knowledge bases

- [ ] **UC1 in full**: a documentation folder indexed, answers citing
      retrieved passages, the index rebuilt from the GUI, the same
      knowledge base working with local *and* Argo embeddings, and the ten
      canned USAXS questions answered with correct citations.
- [ ] **Retrieval quality on a real corpus** (USAXS instructions, a
      pyIrena docs folder, an Obsidian vault). Speed is settled (about
      0.03 s per query at 11,800 chunks); quality is not.

### GUI, on each OS

- [ ] **Manual smoke checklist on Windows**: the Stop button interrupting
      a long generation cleanly with the app usable afterwards, and the
      flagship pyIrena demo on a cloud and a local Ollama model. Done on
      macOS (2026-11-23).
- [ ] **Clean-machine install from PyPI on all three OSes**:
      `pip install "aida-workbench[gui,docs,science]"` gives a working
      `aida-gui`.

### Automation

- [ ] **UC6**: a scheduled daily report generating on time while the app
      runs, and a workflow recorded in the GUI replaying from the CLI by
      name with a changed argument.

### Loop detection in real use

- [ ] **Watch a long local-model run with the loop nudges on** (Qwen via
      Splash, the case that prompted them). Check in the chat and in
      `aida.log` ("loop sign (...)") that they fire when the agent really
      is going in circles, not on productive work, and that the run
      finishes sooner. Tune `loop_nudge_threshold` or `SCRIPT_SIMILARITY`
      (`core/loop_detection.py`) from what is seen.

## 2. Open work

- [ ] **Split the three largest files when next working in them**:
      `ui/qt/main_window.py` (conversation management out of the window),
      `core/session.py` (`_start_session` into builders for folders and
      guard, knowledge bases, MCP, recorder) and `config/settings.py` (one
      generic config loader). Review item S1; `planning/COMPLETED.md` §13.
- [ ] **Set a workspace's default MCP group from the MCP dialog.** No
      longer blocked; the workspace editor it waited on exists.
      (`PLAN.md` §1.5)
- [ ] **Comment hygiene, file by file when next touched**: a comment
      states the current invariant; the history stays in git.
      `core/session.py` and `ui/qt/bridge.py` are done. (`PLAN.md` §1.5)
- [ ] **Pick one convention for docs status banners.** Five guides carry
      none (`axis-camera`, `documents`, `moving-and-sharing`,
      `organizing-conversations`, `skills`); a reader takes a missing
      version as "maybe unmaintained".
- [ ] **conda-forge feedstock**: evaluate once PyPI releases are routine,
      and record the decision either way. (`PLAN.md` §1.2)
- [ ] **Portability Tier 3, full backup and restore**: `aida.db`,
      `artifacts/` and `knowledge/` on top of the Tier 1 bundle, rewriting
      the absolute paths stored inside the database through the same
      token map. When moving between beamline workstations becomes
      routine. (`PLAN.md` §1.6)
