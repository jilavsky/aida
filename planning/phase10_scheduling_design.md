# Phase 10 — how automation, workflows and scheduling should actually work

**Written 2026-09-02**, because the checklist in
[`phase10_automation_distribution.md`](phase10_automation_distribution.md)
says *what* to build and not *which* of several possible shapes to build it
in. Potential users have named scheduling/workflows as their top priority,
and the open question is the trigger mechanism: three operating-system
schedulers, or one scheduler inside AIDA that only runs while AIDA is open?

This document answers that. It is a decision document, not a checklist —
the checklist stays in the phase file and gets updated once the shape below
is agreed.

---

## 1. First: two different wants are being conflated

When a beamline user says "I want scheduling", they usually mean one of two
things, and the two have wildly different costs.

**Want A — "run this again without retyping it."** A named, parameterised
sequence of prompts: *reduce today's data, plot it, write the summary.*
Triggered by a human, right now, from a picker or the CLI. No clock
involved.

**Want B — "have it waiting for me."** The same thing, but at 07:00, or
every four hours, with nobody present.

Want A is most of the value and a fraction of the work. It needs the
workflow file format, a runner, and a GUI picker — no clock, no OS
integration, no unattended-confirmation policy, no missed-run semantics. It
is also a strict prerequisite for Want B: a scheduler with nothing to
schedule is not a feature.

**Recommendation: ship Want A first and completely, then find out how many
people actually needed Want B.** My guess is that "a button that replays
this analysis on a new folder" satisfies more of the stated demand than a
clock does, and Quick Tasks (B14) already hints that this is the shape
users reach for. Ask two or three of them the concrete question — *"would
you rather press a button when the run finishes, or have a report appear at
7am?"* — before building any OS integration at all.

Everything below is structured so that stopping after §5 is a coherent
release.

---

## 2. The layer cake

Four layers, each one a thin thing on top of the one below. This matters
because it is what keeps the OS-scheduler question small: it only touches
the top layer.

```
  4. TRIGGER      in-app timer   |   launchd / Task Scheduler / systemd
                        \        |        /
  3. SCHEDULE     schedules.yaml: which workflow, how often, catch-up policy
                                 |
  2. WORKFLOW     workflows/NAME.yaml: workspace + ordered prompt steps
                                 |
  1. RUN          one headless turn in one ChatSession  ← everything is this
```

Layer 1 is `aida run`. Layer 2 is "call layer 1 N times in one session,
stop on failure". Layer 3 is a data file. Layer 4 is the only place the
three-operating-systems question exists — and by then it is *"who calls
`aida workflow run NAME`"*, which is a genuinely small question.

The single most important design rule: **the in-app trigger and the OS
trigger must call the same code path.** In-app it is an in-process
coroutine; from the OS it is a subprocess running the same entry point.
If those diverge, every bug gets found twice.

---

## 3. Layer 1 — `aida run`, and the two things that make it hard

The mechanics are easy: parse args, `start_session(...)`, `await
session.send(prompt)`, drain events, `aclose()`, exit code. Perhaps 250
lines including `--json`. Two things are not easy.

### 3.1 Confirmation with nobody there

`ConfirmCallback` defaults to `deny_all`, which is correct and stays
correct. The question is what a headless caller may opt into. The phase
file already decided *fail-with-message by default, `--yes-in-allowed`
never a blanket `--yes`* — I would keep that and add one clarification the
phase file does not make:

- **Workspace safety mode governs, the flag only narrows it.** A workspace
  in `confirm` mode plus `--yes-in-allowed` means: writes and deletes
  inside the workspace's allowed roots proceed without asking; anything
  that would have escaped those roots fails the step, it does not get
  auto-approved. A workspace in `relaxed` mode needs no flag. There is no
  combination that reaches outside allowed roots without a human.
- **MCP per-tool `confirm_before_run` is separate and stricter.** Those
  flags exist because someone marked a specific tool as dangerous.
  `--yes-in-allowed` must not silently clear them. A headless run that hits
  one fails with *"tool `server__tool` requires confirmation; add it to
  this workflow's `preapproved_tools:` if you intend it to run
  unattended"*. Per-workflow pre-approval is an explicit, auditable,
  reviewable list — which is the same argument §2.6 of `PLAN.md` makes for
  MCP allowlists over denylists, arrived at from the other direction.
- **Never hang.** A headless confirmation request with no callback must
  fail immediately. A scheduled run blocked on an invisible modal at 3am is
  the worst outcome in this whole design.

### 3.2 Secrets without an interactive session

This is the part that will actually cost a day, and it is the same problem
on all three platforms, so it is worth stating once here rather than three
times in §6.

AIDA reads API keys from the OS keychain (`aida.config.secrets`). A
keychain read from a *non-interactive* process behaves differently from one
in the app:

- **macOS.** The login keychain must be unlocked (it is, while the user is
  logged in) *and* the calling binary must be on the item's ACL. The first
  read from a new binary raises a GUI "allow access?" prompt — which a
  scheduled job cannot answer. Fix: the user clicks **Always Allow** once,
  or the schedule supplies the key via environment instead.
- **Windows.** Credential Manager is per-user and works fine in a logged-on
  session; a task configured to run whether-or-not-logged-on cannot reach
  it. So: only ever register "run only when user is logged on".
- **Linux.** Secret Service / gnome-keyring needs an unlocked session
  keyring. A cron job outside a graphical session generally does not have
  one.

**Therefore:** `aida run` must support an env-var fallback for every secret
(`AIDA_SECRET_<REF>` or the provider's own conventional variable), `aida
doctor` must grow a "can this profile's secret be read non-interactively?"
check, and the documentation for scheduling must lead with this rather than
bury it. For a beamline control machine the practical answer is usually a
local Ollama profile or an env-var-supplied Argo key, neither of which
touches a keychain.

---

## 4. Layer 2 — workflows

Keep the format as dumb as the phase file promised. No branching, no
conditionals, no visual builder. A workflow is a workspace plus an ordered
list of prompts.

```yaml
# ~/.aida/workflows/daily-usaxs-report.yaml
name: daily-usaxs-report
description: Reduce yesterday's USAXS scans and write the summary.
workspace: use-pyirena
profile: argo-claude          # optional; workspace default otherwise
mcp_group: pyirena            # optional; workspace default otherwise

vars:                         # defaults, overridable with --var
  folder: /data/usaxs/today
  rg_min: "20"
  rg_max: "50"

preapproved_tools:            # MCP tools whose confirm flag this workflow
  - pyirena__reduce_scan      # accepts responsibility for, unattended only

steps:
  - prompt: >
      Reduce every unreduced USAXS scan in {folder}.
  - prompt: >
      Plot the reduced curves with Rg between {rg_min} and {rg_max} Å.
    expect_files: ["*.png"]   # optional; step fails if nothing matches
  - prompt: >
      Write a Markdown summary of what you just did into the target folder.
    expect_files: ["*.md"]
```

Decisions worth recording:

- **One session for all steps, not one per step.** Step 3 must be able to
  say "what you just did" — that is the whole point of a multi-step
  workflow rather than three `aida run` calls in a shell script. It also
  means MCP servers start once. Context compaction (§1.3, shipped) already
  handles a workflow that runs long.
- **Placeholders are `str.format`-style on the prompt text only**, resolved
  before the step is sent. Missing variable = validation error at
  `aida workflow validate`, not a runtime surprise.
- **`expect_files` is the only assertion.** Resist anything richer. It
  covers the real failure — the model said it wrote a report and did not —
  without becoming an expression language.
- **Stop on first failure**, report which step, why, and what the session
  produced up to that point. Partial output stays; it is usually the most
  useful diagnostic.
- **Every run produces a normal conversation row**, tagged `origin =
  'workflow'`/`'schedule'` (one nullable column, migration 3). It shows up
  in the conversations sidebar and opens like any other conversation. This
  is a large usability win for nearly no code: the user's mental model of
  "what did the 7am run do?" becomes "scroll the transcript", not "read a
  log file".
- **The reproducibility manifest (`PLAN.md` §2.6) is a by-product here.**
  A workflow run already knows workspace, profile, model, tool calls,
  artifacts and timings. Writing `run-<timestamp>.aida.json` next to the
  output costs almost nothing at this point and is the single most
  scientifically valuable thing in Phase 10. Build it here, not later.

GUI side: "Save this conversation as a workflow" writes the user-turn
prompts out as steps and opens the YAML in the existing code-editor dialog
for tidying. A workflow picker runs one into a normal conversation view —
the same `ChatBridge` path as a typed turn, just fed from a list.

Stopping here is a complete, shippable, genuinely useful release. Nothing
above involves a clock.

---

## 5. Layer 3 — the schedule store

One file, independent of who reads it:

```yaml
# ~/.aida/schedules.yaml
schedules:
  - name: morning-report
    workflow: daily-usaxs-report
    when: "07:00"             # or every: "4h"
    trigger: in-app           # or: system
    catch_up: true            # fire once on next start if the slot was missed
    vars: {folder: /data/usaxs/yesterday}
    enabled: true
```

Last-run state (`last_fired_at`, `last_status`, `last_conversation_id`,
`last_error`) goes in SQLite, not in this YAML — the YAML is
user-editable configuration and should not be rewritten by the machine
every hour. That distinction has already paid off elsewhere in AIDA and
should hold here.

The `trigger:` field is the whole point: **the schedule is defined once,
and the trigger is a property of it, not a different feature.** Switching a
schedule from in-app to system is a one-word edit plus an install step, not
a re-authoring.

---

## 6. Layer 4 — the actual question: who wakes it up

### Option A — in-app scheduler only

A single asyncio task in the running app, waking each minute, comparing
`now` against each enabled schedule's next-due time.

- **Cost:** small. One module, roughly 200 lines, plus GUI status rows.
  Fully testable against a fake clock — no OS, no CI matrix problem.
- **Works everywhere identically.** No launchd, no XML, no crontab quoting.
- **Runs in a warm, authenticated context.** Secrets already read, MCP
  servers already up, workspace already resolved. Every problem in §3.2
  evaporates.
- **Limit:** fires only while AIDA is open.

On the beamline control machine that limit is close to theoretical — that
machine is logged in and running continuously, and AIDA sitting open on it
is the expected deployment. On a personal laptop that closes at night, it
is a real limit.

### Option B — OS scheduler only

`aida schedule install NAME` generates and registers the native artefact:

| | mechanism | missed runs | notes |
|---|---|---|---|
| macOS | LaunchAgent plist in `~/Library/LaunchAgents`, `launchctl bootstrap gui/$UID` | fires once on wake, does not backfill each occurrence | `StartCalendarInterval` or `StartInterval`; needs the user logged in |
| Windows | Task Scheduler, `schtasks /Create /XML` | `StartWhenAvailable` — XML only, not reachable via the plain CLI flags | register "run only when user is logged on"; never store credentials |
| Linux | systemd user timer (`.service` + `.timer`, `OnCalendar=`, `Persistent=true`) | `Persistent=true` genuinely backfills after boot | `loginctl enable-linger` to run while logged out; cron as fallback, which has neither environment nor missed-run handling |

- **Cost:** three template generators plus three install/uninstall/status
  shells. Perhaps 150–250 lines each, and each one has to be tested on the
  real OS because none of it can be meaningfully mocked. Call it the
  largest single chunk of work in Phase 10.
- **Survives AIDA being closed** — but read the table's fine print. On all
  three platforms the reliable configuration is *"runs in the logged-in
  user's session"*. Running while logged out means storing Windows
  credentials (no), enabling systemd lingering (fine on a control machine,
  surprising on a laptop), or accepting that a macOS LaunchAgent simply
  will not fire. **So the guarantee this option buys is "AIDA may be
  closed", not "the user may be logged out"** — a much narrower win than it
  first appears, and on a 24/7 beamline machine, nearly the same guarantee
  Option A already gives.
- **Every problem in §3.2 is live**, plus one more: the registered command
  must be the absolute path to the `aida` console script inside the right
  conda environment. `aida` on `PATH` is exactly what a scheduled context
  does not have. Generate `/opt/miniconda3/envs/aida/bin/aida`, resolved
  from `sys.executable` at install time, and re-verify it in `aida doctor`
  because a rebuilt environment silently breaks every installed schedule.

### Option C — an AIDA daemon

A third long-lived process. Rejected: it reimplements what the OS already
does, adds a lifecycle and a log rotation and a "is it running?" problem,
and "server/daemon deployment" is explicitly out of scope for this phase.
Revisit only if AIDA is ever genuinely deployed as shared infrastructure on
`usaxscontrol`, which is §2.1 territory.

### Option D — external only

Document the recipes, build nothing. `aida workflow run NAME` exists; the
user writes their own crontab. This is already AIDA's stated position on
external event triggers (`PLAN.md` §2.2), and it is not unreasonable — but
it pushes the §3.2 secrets problem and the conda-path problem onto the
user, unassisted, and those are the two things most likely to make it fail
silently.

### Recommendation: A now, B later, D documented throughout

1. **Build Option A.** It is cheap, it is cross-platform for free, it is
   unit-testable, and on the deployment that matters most it is nearly
   equivalent to Option B.
2. **Ship the recipes (Option D) alongside it**, in `docs/workflows.md` —
   a worked launchd plist, a worked `schtasks` XML, a worked systemd timer,
   each with the absolute-interpreter-path and secrets caveats spelled out.
   A user who needs Option B before it is built can have it by hand, and
   writing the docs is how the templates for step 3 get validated anyway.
3. **Then Option B**, as `aida schedule install/uninstall/status`, one
   platform at a time — macOS first (it is the development machine),
   Windows second (it is the beamline machine), Linux third (systemd
   timers, cron only as a documented fallback). Each platform is
   independently shippable. If demand never materialises, stopping after
   macOS is fine.

This is not a compromise for its own sake: the layering in §2 means Option
B is *purely additive*. Nothing built for Option A is thrown away or
refactored when it arrives.

---

## 7. The things that will actually bite

Collected here because they are easy to skip in a checklist and expensive
to retrofit.

- **Overlap.** A run still going when the next slot arrives must be
  skipped, with a logged warning and a visible `last_status: skipped`.
  Never stack. One process-wide "scheduled run in progress" guard.
- **Catch-up must not backfill.** Missing eight hourly slots means firing
  *once*, not eight times. `catch_up: true` means "the last slot was
  missed, run now"; there is no mode that replays history.
- **A scheduled run must not touch the user's live session.** It gets its
  own `ChatSession` and its own `McpManager`. This is not optional — it is
  the same class of hazard as the profile-switch and compaction races the
  external review found (`REVIEW.md` P1, fixed in `59a4b92`), and the fix
  is the same: do not mutate a session someone else is using.
- **Two AIDA instances sharing `~/.aida`** is already an unguarded case
  (`PLAN.md` §2.1). An OS-triggered `aida workflow run` while the GUI is
  open makes it *routine* rather than exceptional. Option B needs the lock
  file that §2.1 currently files under "would at least make the failure
  mode explicit" — it stops being optional at that point.
- **MCP startup cost per run.** A subprocess-per-schedule pays full MCP
  startup every fire. Fine hourly, wasteful every five minutes. Document
  the floor (say, no interval below 15 minutes for `system` triggers) or
  just let Option A own the short intervals, since it keeps servers warm.
- **Where the output goes.** The workspace target folder, as ever — but a
  daily report needs a date in the filename or it overwrites yesterday's.
  `unique_destination` already exists; make sure the workflow runner uses
  it rather than letting the model choose.
- **Notification.** When the app is open, a non-modal notice plus the new
  conversation appearing in the sidebar. When it is not, the file on disk
  *is* the notification. Do not build email. A failed run should be loud
  the next time the GUI opens — a persistent banner, not a log line.
- **A silently broken schedule is the worst failure mode.** Every schedule
  keeps `last_fired_at` and `last_status`; `aida doctor` and the GUI both
  report "expected to have fired N hours ago, did not". Nobody notices a
  report that stops arriving until they need it.

---

## 8. CLI surface

```
aida run --workspace W "prompt"            # layer 1
    [--profile P] [--input FILE ...] [--json] [--yes-in-allowed]
    [--timeout SECONDS]                    # prompt from stdin if omitted

aida workflow list | show NAME | validate NAME
aida workflow run NAME [--var k=v ...] [--json] [--yes-in-allowed]

aida schedule add NAME --workflow W (--at 07:00 | --every 4h)
                       [--trigger in-app|system] [--var k=v ...]
aida schedule list | remove NAME | enable NAME | disable NAME
aida schedule run NAME                     # fire once, now, for testing
aida schedule install NAME | uninstall NAME | status   # option B only
```

Exit codes: `0` success; `1` a step failed; `2` configuration or validation
error; `3` a confirmation was required and could not be granted. Distinct
codes matter — a pipeline needs to tell "the analysis found nothing" from
"the credential expired".

`aida schedule run NAME` is small and disproportionately useful: it is how
a user checks their schedule actually works without waiting until 07:00.

---

## 9. Testing

- Layer 1: `aida run` end-to-end with `MockProvider` + the existing mock
  MCP subprocess, in CI, on all three OSes. Exit codes, `--json` schema,
  each headless confirmation outcome including the "requires confirmation,
  refused" path.
- Layer 2: parse, validate, placeholder substitution, missing variable,
  `expect_files` satisfied and unsatisfied, stop-on-failure leaves partial
  output, manifest contents.
- Layer 3: pure fake-clock unit tests — due/not-due, catch-up fires once
  and only once, overlap skips, disabled schedules never fire, DST and a
  clock jumped backwards.
- Layer 4 Option A: same fake clock, driven through the bridge.
- Layer 4 Option B: generation of each platform's artefact is unit-testable
  against a golden file; *registration* is a manual per-OS check and goes
  in `PLAN.md` §1.4 with the other verification owed. Do not pretend
  otherwise.

---

## 10. Out of scope, deliberately

Branching or conditional workflows; a visual workflow builder; retries and
backoff; a daemon; multi-user scheduling; cluster or queue submission;
email or chat notification; any workflow step that is not a prompt.

Every one of these is a reasonable thing to want and none of them is what
was asked for. If a workflow needs branching, the answer is that the agent
is supposed to be doing the branching — that is what it is for.
