# AIDA — instrument integration plan (BeamlineAdvisor retirement, aievaluator, EPICS)

**Status: proposal, 2026-09-03. No code. Companion to `PLAN.md`; items here
graduate into `PLAN.md` §1 when accepted.**

This document answers three questions:

1. What does BeamlineAdvisor do that AIDA does not yet do, and what has to be
   true before BeamlineAdvisor can be retired?
2. How should `aievaluator` — a working, separately maintained tool — be wired
   into AIDA without importing or redistributing it?
3. Is a *safety-limited EPICS MCP server* a good idea, how should it be
   shaped, and where should it live?

The short version: **BeamlineAdvisor is ~90 % covered by AIDA already**; the
real gaps are the five live-status tools (that is the aievaluator question),
per-user identity on a shared machine, and browser access from LAN machines.
**aievaluator should grow an MCP server of its own** (`aievaluator-mcp`) that
AIDA launches exactly the way it launches `pyirena-mcp`; the interim path —
`run_python_script` with `python_interpreter` pointing at the aievaluator env
— works today with zero code. **The EPICS MCP is a good idea and should be a
separate, small, generic package**, not part of aievaluator, with a
policy file that is the hard safety boundary and AIDA's own
`confirm_tools`/`disabled_tools` as the second, client-side layer.

---

## 1. BeamlineAdvisor → AIDA: feature-by-feature

Sources: `BeamlineAdvisor/app.py`, `ui/*`, `services/*`, `config/*`,
`PLAN.md`; AIDA `docs/*`, `src/aida/config/settings.py`,
`planning/DESIGN.md`.

| BeamlineAdvisor feature | Where it lives in BA | AIDA equivalent | Status |
|---|---|---|---|
| Claude via ANL Argo proxy, model dropdown | `services/llm_service.py`, `ui/sidebar.py` | Provider profiles (`argo-claude`), switchable mid-conversation | **Covered** |
| Small "always in context" docs (`docs/`, `docs/instructions` symlink) | `services/doc_loader.py` | `skills` (per workspace / per MCP server) + `system_prompt: file.md` | **Covered** — skills are the right vehicle for `instructions/*.md` |
| RAG over large docs (LlamaIndex + Chroma, Argo embeddings) | `services/rag_service.py`, Admin tab | Knowledge bases (Phase 8), local or Argo embeddings, GUI rebuild | **Covered** |
| Obsidian vault as RAG source | Admin tab | Knowledge base over a folder | **Covered** |
| User vs. staff system prompt (`PROMPT_TYPE`) | `config/prompts_user.py` / `prompts_staff.py` | Two workspaces (`usaxs-user`, `usaxs-staff`) with different `system_prompt`, `mcp_group`, `skills`, `safety` | **Covered** — and better: staff gets a different *tool set*, not only a different prompt |
| Data-root text box → `PYIRENA_DATA_ROOT` | `ui/sidebar.py` | Workspace `source_folders` / `target_folder`; the model is told the folders | **Covered** |
| pyIrena result readers + plot tools (~18 hand-wrapped functions) | `services/pyirena_tools.py` | `pyirena-mcp` via `aida mcp add-pyirena`; PNG decoded and displayed | **Covered** (and no longer hand-maintained) |
| Code extraction, ACE editor, save to `bits_usaxs/.../user`, run with timeout | `ui/code_panel.py`, `services/code_runner.py` | Phase 9: Code Editor dialog, `saved_scripts_dir`, `python_interpreter`, script timeout, `templates_dir` | **Covered** |
| Bluesky plan templates in context (`docs/bluesky-user-plans`) | doc loader | `templates_dir` (docstrings surfaced) + a `bluesky-plans` skill | **Covered** |
| Starter prompts ("Write a Linkam plan", …) | `ui/suggestions.py` | Quick Tasks (per workspace, in `workspaces.yaml`) | **Covered** for starters |
| Context-aware follow-ups ("Debug this", "Fix execution error", "Load code into editor") | `ui/suggestions.py` | None — Quick Tasks are static | **Gap (small, optional)** — see §1.2 |
| Per-user chats and saved scripts (`username` text box) | `ui/sidebar.py`, `services/chat_storage.py` | None. (`PLAN.md` §2.1 says a nullable `user` column exists in the Phase 4 schema — `persistence/db.py` has no such column; it needs a migration.) | **Gap (real on a shared beamline machine)** — see §1.2 |
| Export/import chat as JSON | `ui/sidebar.py` | Conversation export is `PLAN.md` §2.5 "Considered" | **Gap (minor)** |
| Deployment: Streamlit on `usaxscontrol`, used from Windows LAN machines in a browser | `PLAN.md` "Deployment" | AIDA is a desktop PySide6 app; web frontend is `PLAN.md` §2.3 "Considered" | **Gap (deployment model)** — see §1.3 |
| Tool status lines ("Checking beam…") | `tool_status_message()` | Tool-call widget with name/args/timing | **Covered** |
| `check_beam_status`, `check_energy_match`, `check_flux` (pyepics in-process) | `services/pv_tools.py` | None | **Gap — the aievaluator question (§2)** |
| `run_fitness_report`, `run_check_tunes` (subprocess into aievaluator) | `services/pv_tools.py` | Interim: `run_python_script`; target: `aievaluator-mcp` | **Gap — §2** |
| Plot-cache cleanup on start | `config/settings.py` | Artifact store + `persistence/cleanup.py` | **Covered** |

One thing worth stating because it decides the aievaluator design:
BeamlineAdvisor's `pv_tools.py` does **not** call aievaluator for the three
targeted checks — it re-implements `check_beam`, `check_energy`, `check_flux`
with its own copies of the PV names and thresholds, and only shells out to
`fitness_report.py` and `check_tunes.py`. So today there are two copies of
the USAXS check logic that can drift. Whatever AIDA does must not create a
third copy; it should make aievaluator the single source (§3).

### 1.1 What must be true before BeamlineAdvisor is retired

- The five live-status tools are available in an AIDA workspace on
  `usaxscontrol` (via §2), with the `instructions/*.md` content available
  as skills.
- A `usaxs-user` and a `usaxs-staff` workspace exist as *shipped examples*
  (`examples/config/workspaces.yaml`) so the BeamlineAdvisor prompt split is
  reproducible, not re-invented per install.
- The user-facing conveniences that people actually used are present:
  starter Quick Tasks for the three BA starters, the Code Editor saving into
  the bits_usaxs user folder, the pyIrena tools.
- Some answer to "several users, one beamline machine" (§1.2) — even the
  thin one.
- A decision on the deployment model (§1.3). This is the one item that can
  block retirement outright, because it is about *who can reach AIDA*, not
  what it does.

### 1.2 Small gaps worth closing (AIDA work)

- **Thin "active user" layer.** Not auth: a name picker in the toolbar
  (like BA's text box) that stamps a new nullable `user` column (one
  additive migration) on conversations and artifacts, filters the
  conversations sidebar by it, and
  substitutes `{user}` into `saved_scripts_dir` (so
  `.../saved_scripts/{user}/` works like BA). `PLAN.md` §2.1 already
  describes this; the beamline deployment is the "concrete need" it was
  waiting for. Secrets stay per-machine (the Argo username *is* the API
  key in BA, so per-user profiles would mean per-user secrets — defer that
  half; a shared staff profile is what BA effectively does today).
- **Context-aware Quick Tasks** (optional). BA's `get_suggestions()` is ~60
  lines of keyword heuristics ("traceback" → "Debug this"; a code block in
  the last reply → "Load into editor"). If wanted, the same heuristics can
  drive a second row of transient buttons under the input box; the Quick
  Tasks panel and event API already exist. Low value unless users miss it.
- **Conversation export bundle** (`PLAN.md` §2.5) — BA users could export a
  chat as JSON; graduate when someone asks.

### 1.3 Deployment model — the decision that matters

BeamlineAdvisor's real value at the beamline was that *any* Windows machine
on the LAN could open a browser to `usaxscontrol:8501`. AIDA is a desktop
app. Three options, not mutually exclusive:

1. **AIDA installed on each machine that needs it** (staff laptops, the
   control workstation). pyIrena is already installed that way, so this is
   the path of least surprise. What it needs: the instrument-side tools
   (aievaluator, the EPICS MCP, Tiled) reachable *from* those machines. EPICS
   Channel Access from an arbitrary Windows laptop is unreliable (gateway,
   `EPICS_CA_ADDR_LIST`, firewall); Tiled is HTTP and fine. This is what
   turns `PLAN.md` §2.2 "remote MCP servers over HTTP" from "parked" into a
   concrete need: **run `aievaluator-mcp` / `epics-mcp` on `usaxscontrol`
   as an HTTP (streamable-HTTP) MCP server, and let AIDA on a laptop connect
   to it.** The MCP manager was designed transport-pluggable; this is the
   first real remote server.
2. **A web frontend on AIDA's event API** (`PLAN.md` §2.3, NiceGUI or
   similar), deployed on `usaxscontrol` exactly like BA. Reproduces BA's
   reach with AIDA's engine. It is the larger piece of work and the plan
   deliberately deferred it until the event API is stable — it now is
   (Phase 5 GUI + Phase 10 scheduler bridge both consume it). Worth a
   timeboxed spike once §2 is done, because it is the only option that
   gives *users* (not staff) access without installing anything.
3. **AIDA on `usaxscontrol` only, via remote desktop / NoMachine.** Zero
   work, and how many beamline GUIs are used anyway. Acceptable for staff;
   poor for users.

Recommendation: do (1) for staff now — it is mostly the §2/§4 work plus the
remote transport — and evaluate (2) afterwards with real usage data. Do not
retire BA until one of (1)/(2) covers the people who actually used it.

---

## 2. Wiring aievaluator into AIDA

### 2.1 The options

**A. Scripts through `run_python_script` (works today, zero code).**
Workspace `usaxs-staff` with `python_interpreter:
~/miniconda3/envs/aievaluator/bin/python`, `source_folders` including the
aievaluator checkout, `scripting_enabled: true`. The model runs
`tools/check_flux.py --pretty` and reads the JSON from stdout. This is
literally `DESIGN.md` UC5 and needs only a skill file telling the model the
five scripts and their flags.
Drawbacks: the model has to *know* the CLI (no schemas), results arrive as
text (no typed artifacts, no PNG display for `check_tunes --plot-dir`),
each run is a confirmation in `confirm` mode (or `relaxed` for the whole
folder, which is broader than wanted), and it is not usable from a laptop
(§1.3). Fine as the interim and as the fallback; not the destination.

**B. Import aievaluator into AIDA as native tools.** Rejected, and the
user's instinct is right: AIDA would have to depend on (or vendor)
aievaluator and pyepics, the pyepics/CA environment would have to be AIDA's
own environment, and every threshold change would need an AIDA release.
AIDA's design already decided this for pyIrena: domain logic lives in the
domain package and speaks MCP.

**C. An MCP server inside aievaluator (`aievaluator-mcp`).** Recommended.
aievaluator gains a `pyproject.toml`, a package layout, and a console script
`aievaluator-mcp` (FastMCP, stdio by default, `--transport http --port N`
optional). AIDA launches it as a stdio subprocess with `command` pointing at
the aievaluator conda env's binary — precisely the `pyirena-mcp` pattern —
so pyepics, Tiled and the CA environment stay in *that* env. Later, the
same server runs as a service on `usaxscontrol` for laptops.
Everything AIDA already has applies for free: groups, `disabled_tools`,
`confirm_tools` (put `fitness_report_write` there), keychain env, the tool
log, PNG artifacts (`check_tunes` can return `ImageContent`), the MCP
management dialog, and — once generalized — the one-click preset
(`PLAN.md` §1.5 / §2.7).

Recommendation: **C, with A as the bridge until C exists.** Nothing in A is
thrown away: the skill written for A becomes the `aievaluator` skill
shipped with C.

### 2.2 What the MCP surface should be

Keep it as small as BA's — five tools plus two generic ones — and keep the
JSON shapes BA already validated (`{"status": "ok" | "problem" | "error",
"checks": [...]}`), since they are known to read well for the model:

| Tool | Args | Notes |
|---|---|---|
| `check_beam_status` | — | ring current + shutters |
| `check_energy_match` | — | undulator/mono offset + harmonic |
| `check_flux` | — | diode flux normalized by SR570 gain |
| `check_tunes` | `plan`, `num`, `days`, `plot` | Tiled-backed; with `plot=true` returns PNGs as image content so AIDA displays them |
| `fitness_report` | `write: bool = false` | dry-run text by default; `write=true` writes the Obsidian record — this is the one **mutating** tool; AIDA config marks it `confirm_tools` |
| `read_pvs` | `names: list[str]` | the `pv_status.py` function, **restricted to the PV catalog aievaluator already knows** (the union of `_BEAM_PVS`, the fitness-report PV table, …) — not arbitrary names; arbitrary reads are the EPICS MCP's job (§4) |
| `describe_checks` | — | returns the PV table + thresholds per check, so staff can ask "what does check_flux actually look at?" without reading code |

Two AIDA-side conventions to honour in the server:

- **Nothing blocks.** Every CA call has a timeout (aievaluator already does
  this); a disconnected PV is a `"connected": false` entry, never an
  exception, so a single dead IOC does not turn a status question into a
  tool error.
- **Return structured content, not prose.** AIDA's `mcp/results.py` turns
  JSON into typed artifacts and PNG bytes into image cards; the server
  should not pre-format Markdown except for `fitness_report`, whose output
  *is* a Markdown document by design.

### 2.3 AIDA-side items for the aievaluator integration

- [ ] **Interim now:** a `skills/aievaluator-scripts.md` (or in the
      aievaluator repo, linked as a workspace skill) documenting the five
      CLIs, flags, exit codes and JSON shape; a `usaxs-staff` example
      workspace with `python_interpreter` pointing at the aievaluator env.
- [ ] **`aida mcp add-aievaluator`** — second instance of the one-click
      preset pattern (`PLAN.md` §1.5 "extend beyond pyIrena"; the missing
      per-server knowledge is now available: binary name `aievaluator-mcp`,
      env vars `EPICS_CA_ADDR_LIST`, `EPICS_CA_AUTO_ADDR_LIST`,
      `AIEVALUATOR_CONFIG`, `TILED_URL`; group `instrument-status`; skill
      `aievaluator`; `confirm_tools: [fitness_report]` by default). Doing
      pyIrena and aievaluator side by side is what makes the generalization
      honest — a "preset" dataclass with command detection, env, group,
      skills, confirm defaults and a doctor check.
- [ ] **`enabled_tools` allowlist per server** (`PLAN.md` §2.6). For an
      instrument-facing server this stops an upstream update from silently
      adding a mutating tool to a workspace that reviewed the old set. It is
      cheap and it is the natural moment: the `usaxs-user` workspace should
      say *exactly* which five tools it exposes.
- [ ] **Remote MCP transport (streamable HTTP)** for the laptop case in
      §1.3 — `url` in `mcp.json` alongside `command`, bearer token via
      `keyring:`, lifecycle = connect/reconnect rather than spawn/kill. The
      first concrete remote server exists once `aievaluator-mcp --transport
      http` does.
- [ ] **Scheduled fitness report** as the first real schedule: a workflow
      "run fitness_report with write=true, then summarize problems" on the
      Phase 10 scheduler, replacing `fitness_report.sh` + cron. (The Phase 10
      commits — workflows, scheduler, GUI — are in `git log` but `PLAN.md`
      §1.2 and `docs/README.md` still say "not started"; reconcile the docs
      when this lands.) Headless confirmation policy matters here: the
      schedule must pre-approve `fitness_report` or it will fail by design.
- [ ] **Instrument-status Quick Tasks** in the example workspaces ("Is the
      beam OK?", "Why is my flux low?", "Show me last hour's tunes").

---

## 3. Suggested development in aievaluator

aievaluator is a folder of scripts plus an `instructions/` folder; that was
right for a cron-driven evaluator. To be a dependency (of AIDA via MCP, of
BeamlineAdvisor while it lives, of a future scheduler) it wants to become a
small package, without changing what the scripts do.

Suggested layout (names illustrative):

```
aievaluator/
├── pyproject.toml                # name: aievaluator; console scripts below
├── environment.yml               # unchanged in spirit; adds mcp / fastmcp
├── src/aievaluator/
│   ├── __init__.py
│   ├── config.py                 # loads instrument.yaml (PV tables, thresholds)
│   ├── epics_io.py               # read_pv / read_pvs with timeouts; the ONLY pyepics import
│   ├── checks/
│   │   ├── beam.py               # check_beam_status() -> dict
│   │   ├── energy.py             # check_energy_match() -> dict
│   │   ├── flux.py               # check_flux() -> dict
│   │   ├── tunes.py              # check_tunes(plan, num, days, plot) -> dict (+ PNG paths)
│   │   └── fitness.py            # build_report() -> (markdown, ok); write_report()
│   ├── tiled_client.py           # as today
│   ├── cli.py                    # `aievaluator check-beam --pretty` etc. (thin wrappers)
│   └── mcp_server.py             # `aievaluator-mcp` (FastMCP); tools call checks/* directly
├── config/instrument_usaxs.yaml  # PV names + thresholds, moved out of code
├── instructions/                 # unchanged; also shipped as AIDA skills
└── tests/                        # checks against a fake epics_io (no IOC needed)
```

Specific suggestions, in priority order:

1. **Library first, CLI and MCP as thin shells.** Each check becomes a plain
   function returning the dict it prints today; `cli.py` adds argparse and
   `json.dumps`; `mcp_server.py` adds the tool decorator. The three
   duplicated implementations in BeamlineAdvisor's `pv_tools.py` can then
   be deleted and BA can `from aievaluator.checks import ...` for the rest
   of its life — one source of truth for PV names and thresholds.
2. **One `epics_io` module.** Today four scripts each construct
   `epics.PV(...)`, wait, get, disconnect. Centralizing gives one place for
   timeouts, a connection cache (the fitness report reads ~60 PVs, creating
   and disconnecting each is slow), the `EPICS_CA_ADDR_LIST` handling, and
   — if §4 happens — the point where a policy check plugs in.
3. **PV tables and thresholds in a YAML file**, not module-level dicts.
   `_BEAM_PVS`, the harmonic ranges, `_FLUX_NO_BEAM`, sensitivity maps —
   staff should be able to retune a threshold without a code change, and
   `describe_checks` (§2.2) can simply return the file. Keep a `--config`
   flag / `AIEVALUATOR_CONFIG` env var so a SAXS-station config could exist
   later.
4. **`fitness_report`: separate build from write.** `build_report()` returns
   `(markdown, ok)`; `write_report(path)` is the only function that touches
   the Obsidian vault. The MCP tool exposes both through one `write` flag so
   AIDA can gate the write with `confirm_tools`. Delete the empty
   `fitness_report_new.py`.
5. **`check_tunes` returns images as content.** Today it writes PNGs to
   `--plot-dir`. In the MCP tool, return them as `ImageContent` (base64)
   alongside the JSON, the way pyIrena does; AIDA shows them inline and
   saves them as artifacts. The CLI keeps `--plot-dir`.
6. **Tests without an IOC.** A `FakeEpicsIO` returning canned values lets
   every check be tested (good/bad/disconnected branches) in CI; the tune
   PNGs in `tmp/tunes/` are already de facto fixtures — move a couple of the
   underlying arrays into `tests/fixtures/`.
7. **Version, changelog, and a `doctor`-style self-check** (`aievaluator
   doctor`: can I reach CA? Tiled? the Obsidian path?). AIDA's preset can
   call it.
8. **Working-tree hygiene** — `.loglogin`, `.claude/settings.local.json`
   and `tmp/tunes/*.png` are untracked (good); add them to `.gitignore`
   explicitly so a `git add -A` during the repackaging cannot pull them in.

What aievaluator should *not* grow: a generic "read/write any PV" tool. Its
value is that it encodes USAXS-specific judgment (what "OK" means). Generic
PV access is a different product with a different safety profile — §4.

---

## 4. A safety-limited EPICS MCP server

### 4.1 Is it a good idea?

Yes, with the policy file as a hard boundary inside the server. The
arguments for it:

- Staff questions at the beamline are often "what is `usxLAX:m58:c0:m1.RBV`
  right now?" or "has the Linkam reached temperature?" — one-off reads that
  aievaluator's fixed checks will never cover and should not try to.
- It is reusable beyond USAXS (any EPICS beamline, any MCP client), which
  aievaluator is not.
- The risk is real and well-understood: an agent with `caput` on a live
  instrument. A policy that is *enforced in the server process*, in a file
  the agent cannot edit, is the right shape — AIDA's `confirm_tools` is a
  UI convenience layer, not a boundary, because a different client (or a
  headless `aida run`) could bypass it.

The arguments against are about scope, not existence: it must stay small,
and its write path must be opt-in per deployment, per PV, with constraints.

### 4.2 Where it should live

**A separate small package** (working name `epics-mcp`, or `caguard-mcp` if
the policy library is the identity). Not inside aievaluator, because:

- aievaluator = USAXS judgment; the EPICS server = generic, policy-gated
  channel access. Different maintainers could own them; different beamlines
  would install only one of them.
- The safety review of a write-capable EPICS server is a different
  conversation from reviewing a read-only status tool. Keeping them in
  separate packages keeps `aievaluator-mcp` trivially read-only by
  construction.
- aievaluator may later *depend* on the policy library (its `epics_io` can
  route through the same guarded client), but the dependency arrow should
  point that way, not the reverse.

Same Python/MCP stack as AIDA and pyIrena (FastMCP, pyepics), so it runs in
the aievaluator conda env or its own; AIDA launches it like any other
server. pyepics is the right choice for consistency with aievaluator and
the beamline's existing code; `caproto` (pure Python, no libca) is a
reasonable alternative if the server ever needs to run where libca is
awkward — keep the CA client behind one interface so this stays a
one-module swap. PVAccess (`p4p`) only if a concrete need appears.

### 4.3 Policy model

A single YAML file, path given on the command line or via env; the server
refuses to start without one; **no policy = no PVs**. Illustrative shape:

```yaml
# epics-mcp policy — USAXS staff, read-mostly
mode: read-only            # read-only | read-write   (read-write still needs per-rule write: true)
default_timeout_s: 3
max_pvs_per_call: 50

allow:
  # glob patterns (fnmatch); "re:" prefix = Python regex
  - pattern: "usx*"                 # all USAXS IOCs (usxLAX, usxRIO, usxAERO, usxTEMP, ...)
  - pattern: "XFD:srCurrent"
  - pattern: "PA:12ID:STA_*_BEAMREADY_PL.VAL"
  - pattern: "12ida2:*"
  - pattern: "12idc:*"
  - pattern: "S12ID:USID:*"
  - pattern: "re:^12idPyFilter:.*"

deny:                               # deny always wins over allow
  - pattern: "*.PROC"
  - pattern: "*:m*:*.STOP"
  - pattern: "re:^12idb.*"          # B station is not ours — never even read it

writes:                             # only consulted when mode: read-write; empty = no writes at all
  - pattern: "usxTEMP:*:SP"
    range: [20, 300]
    confirm: true                   # server echoes back a token the client must resend
  - pattern: "usxLAX:shutter:mode"
    enum: ["auto", "manual"]
    rate_limit_per_min: 6
```

Design notes the file above implies:

- **Two separate lists for read and write, and deny beats allow.** The
  user's `usx*` example is right for USAXS *instrument* PVs, but §2 shows
  the status checks also need `XFD:`, `PA:12ID:`, `12ida2:`, `S12ID:USID:`,
  `12idc:`. So the policy should allow *named sets* — or simply several
  patterns — rather than a single prefix. Ship an example policy per
  station so nobody starts from `*`.
- **Glob and regex both**, with an explicit `re:` prefix so a bare `*`
  never accidentally becomes a regex metacharacter. Match against the
  *full* PV name including field (`.RBV`, `.VAL`, `.STOP`), and normalize
  a missing field to `.VAL` before matching, so `usxLAX:m1` and
  `usxLAX:m1.VAL` are the same rule.
- **Write constraints are per rule**: numeric `range`, `enum`, optional
  `rate_limit`, and `confirm` (a two-step put: the server returns a
  one-time token with the current value, the client resends it — this
  makes "the model called `pv_put` by accident" a two-call event, and it
  composes with AIDA's own confirmation dialog rather than replacing it).
- **`mode: read-only` is the default and the shipped example.** A
  deployment turns on writes by editing the file on disk, not by a tool
  argument. Consider `--policy-readonly-override` on the command line to
  force read-only regardless of file (useful for the `usaxs-user`
  workspace pointing at the same file as staff).
- **Audit log**: every put (and optionally every read) appended to a local
  file with timestamp, PV, old value, new value, client id — the server is
  the only place that reliably knows all four.

### 4.4 Tool surface

Deliberately tiny:

| Tool | Purpose |
|---|---|
| `pv_get(names)` | values for up to `max_pvs_per_call` PVs; each entry `{pv, value, units, severity, status, timestamp, connected, error}` — the `pv_status.py` shape |
| `pv_info(name)` | metadata: units, limits (`LOPR/HOPR`, `DRVL/DRVH`), enum strings, precision, description (`.DESC`) — lets the model explain a value without guessing |
| `pv_watch(names, seconds, interval)` | short bounded monitor (cap `seconds`, e.g. 30) returning a small time series; answers "is it still moving?" without a loop of `pv_get` calls |
| `pv_put(name, value, confirm_token=None)` | present only when `mode: read-write`; enforced by `writes:` rules |
| `policy_describe()` | the effective policy in plain text so the model (and the user) know what is allowed before trying |
| `pv_search(pattern)` | **only** over a static catalog file (`pv_catalog.txt`, one PV + description per line) — CA has no name search, and a catalog also gives the model human-readable names for cryptic records |

No `pv_list_all`, no `caput` of arrays, no `.PROC` by default, no
`camonitor` without a bound.

### 4.5 How it composes with AIDA

Three layers, each independent:

1. **Server policy** (hard): what can be read/written at all. Same file for
   every client of that server.
2. **AIDA `mcp.json`** (per install): `disabled_tools: [pv_put]` for the
   `usaxs-user` workspace's group; `confirm_tools: [pv_put]` for staff;
   `enabled_tools` once it exists; `EPICS_CA_ADDR_LIST` in `env`.
3. **Workspace/group** (per task): `instrument-status` group = aievaluator
   only; `instrument-staff` group = aievaluator + epics-mcp; users never
   see the generic server.

For remote use (§1.3) the server runs on `usaxscontrol` with
`--transport http`, bound to the LAN interface, bearer token from the
keychain on the client side. The policy file lives on the server host, so
a laptop client cannot widen it.

### 4.6 Suggested package structure

```
epics-mcp/
├── pyproject.toml                # console script: epics-mcp
├── src/epics_mcp/
│   ├── policy.py                 # load/validate YAML; match(name) -> Decision; importable by aievaluator
│   ├── ca_client.py              # pyepics behind a small interface (get/put/info/monitor), timeouts, cache
│   ├── audit.py                  # append-only log
│   ├── server.py                 # FastMCP tools; stdio + streamable-http
│   └── cli.py                    # `epics-mcp --policy policy.yaml [--transport http --port 8765] [--readonly]`
├── examples/
│   ├── policy_usaxs_readonly.yaml
│   ├── policy_usaxs_staff.yaml
│   └── pv_catalog_usaxs.txt
└── tests/                        # policy matching is pure and fully testable; CA behind a fake
```

`policy.py` is the piece with lasting value even if the MCP server were
never used: aievaluator's `epics_io` can call `policy.match()` too, and
`tests/` can prove that `12idb*` is never read anywhere.

---

## 5. Order of work

Suggested sequence; each step is useful on its own and none is undone by
the next.

1. **aievaluator: package + library split + YAML config + tests** (§3.1–3.3,
   3.6). No behaviour change; BeamlineAdvisor switches its three duplicated
   checks to imports.
2. **AIDA interim:** `usaxs-staff` example workspace with
   `python_interpreter` = aievaluator env, and a skill for the CLIs
   (§2.1 A). Verifies UC5 on the real machine with what exists today.
3. **`aievaluator-mcp`** (§2.2) + **`aida mcp add-aievaluator`** preset and
   the generalized preset mechanism (§2.3). `confirm_tools: [fitness_report]`.
4. **`enabled_tools`** in AIDA, and shipped `usaxs-user` / `usaxs-staff`
   workspaces + Quick Tasks + skills from `instructions/`.
5. **Scheduled fitness report** via Phase 10 workflow + schedule; retire
   `fitness_report.sh`/cron. Reconcile `PLAN.md` §1.2 with what Phase 10
   actually shipped.
6. **Thin active-user layer** in AIDA (§1.2).
7. **`epics-mcp` read-only** (§4) with the USAXS example policy; staff
   group only. Writes stay `mode: read-only` until there is a concrete
   write use case *and* the audit log has run for a while.
8. **Remote transport** in AIDA + `--transport http` on both servers, for
   staff laptops (§1.3 option 1).
9. **Decide on BeamlineAdvisor retirement** once staff have used AIDA for
   the same tasks for a cycle; then evaluate the web-frontend spike
   (§1.3 option 2) for users.

Open questions only the user can settle:

- Does anyone besides staff actually need the live-status tools? If users
  do, §1.3 option 2 moves up; if not, option 1 is enough and BA can retire
  sooner.
- Should `aievaluator` keep supporting BeamlineAdvisor as an importer
  during the transition (§3.1), or is BA frozen as-is until retirement?
- Which conda env hosts `epics-mcp` on `usaxscontrol` — aievaluator's or
  its own — and who owns the policy file there.
