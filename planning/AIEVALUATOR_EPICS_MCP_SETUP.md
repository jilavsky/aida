# Wiring aievaluator-mcp and epics-mcp into Aida — setup + readiness check

**Status: 2026-09-03, night-before-testing checklist.** Both repos are far
further along than the plan from earlier today assumed — this isn't a "here's
the design" document, it's "here's what's actually built, here's the mcp.json
to add, and here's the exact list of things that still need doing before PV
access will work tomorrow."

I reviewed both repos' current code (not just docs) — `aievaluator` at
commit `6cfcc59`, `epics-mcp` at commit `dd3d0c9`. I could not run anything
against your real conda environments or the instrument from here (this
session's shell reaches your files but not your actual `aievaluator`/
`epics-mcp` conda environments or `usaxscontrol`), so the "run tomorrow"
commands below are the verification I couldn't do for you.

## 1. Bottom line

Both packages are **code-complete for a stdio, single-machine, read-mostly
test tomorrow.** Nothing structural is missing. What's actually left is:
(a) two setup steps neither README currently spells out that will silently
break the connection if skipped, (b) filling in your real
`EPICS_CA_ADDR_LIST`, (c) the mcp.json entries below, and (d) the
verification commands to run before opening Aida, so a failure shows up as
a clear CLI error instead of a confusing tool-call failure inside a chat.

## 2. What's actually implemented (I read the code, not just the plan)

### aievaluator (0.1.0)

Fully built: `checks/{beam,energy,flux,tunes,fitness}.py` as library
functions, `epics_io.py` (single CA interface), YAML config
(`~/.aievaluator/instrument.yaml`, auto-seeded), `cli.py` (9 subcommands),
`aievaluator-mcp` (7 tools: `check_beam_status`, `check_energy_match`,
`check_flux`, `check_tunes`, `fitness_report`, `read_pvs`,
`describe_checks`), `doctor.py`, three skill files, a test suite that runs
with no IOC. `tools/*.py` and `fitness_report.sh` are kept as deprecation
shims, so nothing that already depends on them broke.

Genuinely open (from the repo's own PLAN.md, not my speculation):

- **The PV names and thresholds in `instrument_usaxs.yaml` have never been
  checked against the live instrument.** They were carried over verbatim
  from the old `tools/*.py` scripts while the instrument was offline for
  commissioning. PLAN.md calls this out explicitly as a gate that still
  has to happen "before this package is used for a real fitness report or
  a real 'is the beam OK' answer." **This is what tomorrow's test actually
  validates** — go in expecting to find at least one stale PV name or
  threshold, not expecting everything to just work.
- BeamlineAdvisor hasn't been switched over to import `aievaluator.checks`
  yet (still has its own duplicate PV logic) — not a blocker for you
  testing aievaluator-mcp from Aida, just still open.
- `--transport http` prints "not implemented" and exits — irrelevant
  tomorrow, since Aida launches this as a local stdio subprocess.

### epics-mcp (0.1.0.dev0)

Also fully built, further than its own README currently admits — **the
README's top line still says "Status: pre-alpha. No implementation yet,"
which is now false; everything below exists in `src/epics_mcp/`:**
`policy.py` (539 lines — the deny/allow/write engine, glob + `re:` regex,
deny-shadowing checks, confirm tokens), `ca_client.py` (pyepics + a
`FakeBackend` for testing without EPICS), `catalog.py`, `audit.py`,
`ratelimit.py`, `server.py` (6 tools: `epics_pv_get`, `epics_pv_info`,
`epics_pv_watch`, `epics_policy_describe`, `epics_pv_search`, and
`epics_pv_put` — registered only when the policy allows writes at all),
`cli.py`, `doctor.py`. Two example policies
(`policy_usaxs_readonly.yaml`, `policy_usaxs_staff.yaml`) and a PV catalog.
Tests cover the policy engine, CA client, catalog, audit, rate limiter,
CLI, and a full stdio session against `FakeBackend`.

Genuinely open:

- **Never exercised against a live IOC or even a `softIoc`.** The write
  path (confirm tokens, rate limiting, audit) is verified end-to-end
  against `FakeBackend` only. Tomorrow, with real PV access, is the first
  time any of this touches a real Channel Access server.
- The staff policy's write rules beyond `usxLAX:userCalc*.A` are marked
  `TODO(verify)` in the file itself — PV names taken from a design doc,
  not a live `caget`. **Don't deploy `policy_usaxs_staff.yaml` as-is; start
  read-only.** (Detail below.)
- `--transport http` has no bearer-token auth yet — irrelevant tomorrow,
  same reason as aievaluator.
- **Fix the README's status line** — five-minute doc fix, but worth doing
  so nobody re-derives "is this built yet?" from a stale sentence again.

## 3. Two setup steps that will silently break things if skipped

Neither of these is a bug — they're just not obvious from either README,
and both fail *quietly* (a PV that should connect just won't, or a skill
that should load just won't be there) rather than with a clear error.

**A. Aida does not pass your shell's environment through to MCP
subprocesses.** I checked `src/aida/mcp/server.py`: it builds an explicit
`env` dict for each server subprocess (scratch `TMPDIR`/`TEMP`/`TMP` plus
whatever `mcp.json`'s own `env` block says) and hands that to the MCP
SDK's `StdioServerParameters`, which does **not** inherit your terminal's
full environment the way a normally-launched `aievaluator-mcp` from a
terminal would. Concretely: **if `EPICS_CA_ADDR_LIST` isn't set explicitly
in the server's `env` block in `mcp.json`, the subprocess Aida launches
won't have it**, even though `aievaluator doctor` run by hand in the same
terminal works fine. The symptom would be every PV coming back
`"connected": false` from inside Aida while the CLI tools work standalone
— confusing to debug blind. The mcp.json below sets this explicitly; you
need to fill in the real value(s) for your network (marked below).

**B. The PV catalog file has to sit next to the policy file you copy, not
just next to the example.** `epics-mcp`'s policy loader resolves
`catalog: pv_catalog_usaxs.txt` relative to the *copied* policy file's own
directory (`policy.py`'s `Policy.load`), not the repo's `examples/`
directory. The README's copy command only copies the policy file:

```bash
cp examples/policy_usaxs_readonly.yaml ~/.epics-mcp/policies/usaxs-user.yaml
```

Without also copying the catalog, `epics_pv_search` silently returns
nothing (the server logs a warning and starts anyway — it does not
refuse to start, so this is easy to miss). Copy both:

```bash
mkdir -p ~/.epics-mcp/policies
cp examples/policy_usaxs_readonly.yaml ~/.epics-mcp/policies/usaxs-user.yaml
cp examples/pv_catalog_usaxs.txt      ~/.epics-mcp/policies/pv_catalog_usaxs.txt
# staff policy, if/when you use it:
cp examples/policy_usaxs_staff.yaml   ~/.epics-mcp/policies/usaxs-staff.yaml
cp examples/pv_catalog_usaxs.txt      ~/.epics-mcp/policies/pv_catalog_usaxs.txt   # same file, already there
```

**C. (smaller) Skills have to be copied into `~/.aida/skills/`, not
referenced from the aievaluator repo path.** Aida resolves a workspace's
`skills: [...]` names against `~/.aida/skills/<name>.md` (`aida.core.context.
skill_path`) — it does not read them from wherever aievaluator happens to
be checked out.

```bash
mkdir -p ~/.aida/skills
cp ~/GitHub/aievaluator/skills/aievaluator.md        ~/.aida/skills/
cp ~/GitHub/aievaluator/skills/usaxs-instrument.md   ~/.aida/skills/
```

`epics-mcp` doesn't ship a skill file at all yet (each tool's own docstring
is reasonably thorough, since FastMCP surfaces those to the model — this
is a nice-to-have, not a blocker for tomorrow).

## 4. mcp.json entries to add

Merge this into your real `~/.aida/mcp.json` (I can't write to that path
from here — it's outside the folders connected to this session; the
folders I can reach are the three repo checkouts). Adjust the two
`command` paths if your actual conda env prefixes differ from what your
shell showed me (`/opt/miniconda3/envs/aievaluator`,
`/opt/miniconda3/envs/epics-mcp`), and **fill in the real
`EPICS_CA_ADDR_LIST`** — I don't have your beamline's actual CA gateway
address, and a wrong or placeholder value will connect to nothing.

```json
{
  "mcpServers": {
    "aievaluator-mcp": {
      "command": "/opt/miniconda3/envs/aievaluator/bin/aievaluator-mcp",
      "args": [],
      "env": {
        "EPICS_CA_ADDR_LIST": "FILL_IN_YOUR_CA_GATEWAY",
        "EPICS_CA_AUTO_ADDR_LIST": "NO"
      },
      "groups": ["instrument-status", "instrument-staff"],
      "skills": ["aievaluator", "usaxs-instrument"],
      "confirm_tools": ["fitness_report"]
    },
    "epics-mcp-user": {
      "command": "/opt/miniconda3/envs/epics-mcp/bin/epics-mcp",
      "args": ["--policy", "usaxs-user"],
      "env": {
        "EPICS_CA_ADDR_LIST": "FILL_IN_YOUR_CA_GATEWAY",
        "EPICS_CA_AUTO_ADDR_LIST": "NO"
      },
      "groups": ["instrument-status"],
      "skills": [],
      "disabled_tools": []
    },
    "epics-mcp-staff": {
      "command": "/opt/miniconda3/envs/epics-mcp/bin/epics-mcp",
      "args": ["--policy", "usaxs-staff"],
      "env": {
        "EPICS_CA_ADDR_LIST": "FILL_IN_YOUR_CA_GATEWAY",
        "EPICS_CA_AUTO_ADDR_LIST": "NO"
      },
      "groups": ["instrument-staff"],
      "skills": [],
      "confirm_tools": ["epics_pv_put"]
    }
  }
}
```

Notes on the choices above:

- **Two `epics-mcp` server entries, not one.** A single `mcp.json` entry
  is one fixed `command`/`args`, and the policy is chosen by
  `--policy NAME` on the command line — there's no way to expose both a
  read-only and a write-capable mode from one entry. `epics-mcp-user`
  (read-only) and `epics-mcp-staff` (write-enabled) are two separate
  subprocesses, two separate groups, so a `usaxs-user` workspace can point
  `mcp_group` at `instrument-status` and structurally never see
  `epics_pv_put`.
- **`confirm_tools: ["epics_pv_put"]` on the staff server is a second,
  client-side layer on top of the confirm-token protocol
  `epics_pv_put` already has built in** (a non-`confirm: true` write rule,
  like `usxLAX:userCalc*.A`, writes on the first call otherwise). Given
  neither the write path nor the instrument have ever been tested
  together, I'd keep this on even after things are working — it's cheap
  insurance for a tool that changes hardware state.
- **`confirm_tools: ["fitness_report"]`** on aievaluator matches the
  hand-off table the aievaluator repo's own PLAN.md wrote for AIDA — it's
  the one tool in that server that writes anything (to the Obsidian
  vault).
- **I put `aievaluator-mcp` in both groups** since its tools are read-only
  regardless of who's asking; only the `epics-mcp-*` split needs to differ
  between the two groups.
- `EPICS_CA_AUTO_ADDR_LIST: "NO"` is a guess at your usual convention
  (explicit address list, no auto-discovery) — set it to match however
  you normally run `caget` on this network; if you don't know, leave it
  unset and only set `EPICS_CA_ADDR_LIST`.

You don't have to fold these into workspaces to test connectivity — `aida
mcp server test <name>` (step 5 below) works against the raw server entry.
Workspaces (`usaxs-user` / `usaxs-staff` pointing `mcp_group` at
`instrument-status` / `instrument-staff`) are worth creating once the
servers themselves are confirmed working — happy to draft
`workspaces.yaml` entries for those next, once tomorrow's test tells us
whether the group split above is actually the shape you want.

## 5. Exact commands to run before opening Aida tomorrow

In order — each one gates the next, and each failure tells you exactly
what's still missing rather than surfacing as a vague tool-call error
inside a chat session.

```bash
# 1. Confirm both console scripts actually exist and import cleanly —
#    catches an incomplete `pip install -e` before anything else does.
conda run -n aievaluator aievaluator --help
conda run -n epics-mcp   epics-mcp --version

# 2. aievaluator's own reachability check (config, pyepics, one PV,
#    Tiled, the Obsidian mount) — run this FIRST, it's the fastest way
#    to find a wrong PV name or an unreachable network mount.
conda run -n aievaluator aievaluator doctor

# 3. Policy setup, if not already done (see §3.B above) — then validate
#    the policy file loads and shows the rules you expect, with no live
#    PV access yet.
conda run -n epics-mcp epics-mcp --policy usaxs-user --check

# 4. epics-mcp's own reachability check — same shape as #2, plus
#    catalog + audit-log-writable checks specific to epics-mcp.
conda run -n epics-mcp epics-mcp doctor --policy usaxs-user

# 5. A real PV read from each package's own CLI, bypassing Aida and MCP
#    entirely — the cleanest possible signal that Channel Access itself
#    works from this machine before adding Aida's subprocess-env layer
#    (§3.A) on top.
conda run -n aievaluator aievaluator check-beam --pretty
conda run -n epics-mcp   epics-mcp --policy usaxs-user --check   # rules only
# then, once EPICS_CA_ADDR_LIST is exported in this shell:
conda run -n aievaluator python -c "from aievaluator.epics_io import EpicsIO; io = EpicsIO(timeout=5.0); print(io.read('XFD:srCurrent')); io.close()"

# 6. Only once 1-5 are all green: add the mcp.json entries from §4 (with
#    the real EPICS_CA_ADDR_LIST filled in), then from Aida:
aida mcp server test aievaluator-mcp     # expect: OK — 7 tool(s)
aida mcp server test epics-mcp-user      # expect: OK — 5 tool(s) (no pv_put, read-only)
aida mcp server test epics-mcp-staff     # expect: OK — 6 tool(s) (pv_put present)
```

If step 2 or 4 fails on the Tiled or Obsidian checks specifically, that's
network/mount reachability from wherever you're running this (a laptop vs.
`usaxscontrol` will differ), not a code problem — worth knowing before you
read too much into it.

## 6. If PV access itself isn't available tomorrow

Both servers work today with the read-only checks disabled and everything
else exercised: `epics-mcp --backend fake` serves canned values with no
EPICS installed at all (built for exactly this), and every aievaluator
test already runs against `FakeEpicsIO`. So even without real PV access
tomorrow you can still validate: both console scripts run, both `--check`/
`doctor` commands behave sensibly, `aida mcp server test` lists the right
tool counts for each of the three entries, and — for `epics-mcp-user`,
which is the one that most needs to be right before staff ever sees it —
that `epics_pv_put` really is absent from the tool list Aida sees. That
last check *is* the read-only boundary working; it doesn't need beam.

## 7. Priority order if you only have time for some of this

1. §3.A and §3.B (env passthrough, catalog copy) — skip these and nothing
   above will work, and the failure will look like an instrument problem.
2. §5 steps 1–5, standalone, no Aida involved — isolates "is Channel
   Access working from this machine at all" from "is Aida configured
   right."
3. §4's mcp.json, with the real `EPICS_CA_ADDR_LIST`.
4. §5 step 6 — `aida mcp server test` for all three entries.
5. Only after all of the above are green: try the staff write path
   (`usxLAX:userCalc*.A`, the one verified-safe write rule) — through
   `epics-mcp --policy usaxs-staff` on its own CLI-adjacent doctor first
   if one existed, otherwise directly through `epics_pv_put` in a Aida
   chat, expecting the `confirm_required` round-trip on any rule marked
   `confirm: true`, and no round-trip at all (single call, immediate
   write) for `usxLAX:userCalc*.A`, which has `confirm: false`.
6. Everything else in §2 ("genuinely open") is real work but isn't gating
   tomorrow's test — including the live-instrument PV/threshold
   validation, which tomorrow's test *starts* rather than something that
   has to be finished first.
