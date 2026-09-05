# Automation: `aida run`, workflows, and schedules

> **Status: beta (0.1.0b4), Phase 10.** Implemented on top of the same
> session engine every interactive chat uses — a headless run or a
> scheduled job is not a separate code path, it's a different *driver* of
> `start_session`/`ChatSession.send`. See
> [`planning/phase10_scheduling_design.md`](../planning/phase10_scheduling_design.md)
> for the design rationale (why an in-app scheduler rather than
> launchd/Task Scheduler/cron, why "deferred, not skipped" when you're
> using AIDA at the time).

**Related:** [workspaces.md](workspaces.md) · [safety-and-permissions.md](safety-and-permissions.md) · [providers-and-secrets.md](providers-and-secrets.md)

## The four layers

1. **`aida run`** — one prompt, one workspace, one turn, no GUI, an exit
   code a shell script can branch on. No stored state.
2. **Workflows** — a named, saved sequence of prompts against one
   workspace, replayable by name with different `--var` values.
3. **Schedules** — a workflow plus a time ("daily at 07:00", "every 4h"),
   persisted in `schedules.yaml`.
4. **A trigger** — what actually calls the scheduler on a clock. Today
   that's the in-app scheduler only: it runs as a background task for as
   long as the GUI is open, or standalone via `aida schedule watch` on a
   headless machine. OS-level triggers (launchd/Task Scheduler/systemd)
   were deliberately not built — see "Why no OS scheduler" below.

Each layer only adds on top of the previous one. You can use `aida run`
without ever creating a workflow, and a workflow without ever scheduling
it.

## `aida run` — a single headless turn

```bash
aida run --workspace use-pyirena "reduce and report new data in /data/run42" --json
```

```
aida run --workspace W PROMPT
  --profile NAME              provider profile (default: the workspace's own)
  --skills a,b                skill names to load
  --mcp-group NAME            named MCP server group (default: the workspace's own)
  --mcp a,b                   MCP server names, bypassing groups
  --input FILE                image attachment (repeatable)
  --yes-in-allowed            auto-approve writes/deletes inside the workspace's allowed folders
  --preapprove-tool SERVER__TOOL   approve one MCP tool despite its confirm-before-run flag (repeatable)
  --json                      machine-readable result summary instead of plain text
```

If `PROMPT` is omitted, `aida run` reads it from stdin — so it composes
with a pipeline:

```bash
echo "summarize today's runs" | aida run --workspace use-pyirena --json
```

Exit codes: `0` ok, `1` the turn errored, `2` config/validation error
(unknown workspace, profile, or MCP server — checked before anything
runs). There is no separate "confirmation declined" exit code: a denied
confirmation shows up as an ordinary failed tool call in `--json`'s
`tool_calls` list, exactly like any other tool error the model can see and
react to.

### `--json` shape

```json
{
  "ok": true,
  "reply": "...",
  "stop_reason": "end_turn",
  "tool_calls": [{"tool_name": "write_file", "is_error": false}],
  "error": null,
  "conversation_id": "..."
}
```

## Headless confirmations — never hangs, never widens safety

Nobody is watching a terminal for a scheduled or piped run, so a
confirmation that would normally pop a dialog must resolve immediately one
way or the other instead of blocking forever. `aida run`, `aida workflow
run`, and every schedule fire go through the same policy
(`aida.core.headless.build_headless_confirm_callback`):

- **Filesystem/command safety** (writes, deletes, commands outside the
  workspace's allowed folders): declined by default. `--yes-in-allowed`
  approves anything *inside* the workspace's own allowed folders — it
  never widens the workspace's own safety mode, and never approves
  anything outside those folders.
- **MCP "confirm before run" tools**: declined by default regardless of
  `--yes-in-allowed`. Each one needs explicit, per-invocation opt-in via
  `--preapprove-tool SERVER__TOOL` (the namespaced name shown in
  `mcp.json`) — a workflow or schedule can bake this in once
  (`preapproved_tools:` in its own file) so you don't have to pass it every
  time.

This is the same reasoning as `--yes-in-allowed` on `aida run`: unattended
execution should only ever be able to do what the workspace already
permits, never more.

## Non-interactive secrets

A keychain prompt can't be answered by a process with no terminal
attached. Every provider profile's `secret_ref` already falls back to an
environment variable before touching the keychain (`AIDA_SECRET_<PROFILE>`,
uppercased profile name) — set that in whatever environment runs `aida run`
or `aida schedule watch` unattended (a cron-launched shell, a systemd unit,
a login item), and it authenticates without ever touching the OS keychain.

Check a profile is actually set up for this before relying on it:

```bash
aida doctor
```

reports, per provider profile, whether it resolves via the env var
(safe unattended) or would fall through to an interactive keychain prompt
(fine for the GUI, a problem for `watch`/cron).

## Stored workflows

A workflow is one YAML file per workflow under `~/.aida/workflows/NAME.yaml`
— a workspace reference plus an ordered list of prompt steps, all run as
turns of a single shared session (so later steps see earlier steps' output,
same as a normal conversation). There's deliberately no `aida workflow add`
CLI or visual builder: you either hand-write the YAML, or produce one from
the GUI ("Save Conversation as Workflow…", below) and then hand-edit it if
needed.

```yaml
name: daily-report
description: Reduce new USAXS data and write a summary report
workspace: use-pyirena
profile: null            # null = the workspace's own profile
mcp_group: null           # null = the workspace's own mcp_group
vars:
  folder: "/data/latest"
preapproved_tools: []
steps:
  - prompt: "Reduce and process all new files in {folder}."
    expect_files: ["*.h5"]
  - prompt: "Write a one-page Markdown summary of what changed."
```

`{folder}` and any other `{placeholder}` in a step's prompt is resolved
from `vars:` in the file, overridden per run by `--var key=value`. A
missing placeholder is a validation error, not a run-time surprise.
`expect_files` is the only assertion a step can make: a list of glob
patterns checked against the workspace's target folder after the step
completes — an empty list means "no assertion, whatever the agent did is
accepted."

```bash
aida workflow list
aida workflow show daily-report
aida workflow validate daily-report --var folder=/data/latest
aida workflow run daily-report --var folder=/data/latest --json
```

`validate` checks the workspace exists, there's at least one step, and
every placeholder resolves — without running anything. `run` takes the
same `--yes-in-allowed`/`--preapprove-tool`/`--json` flags as `aida run`.

**Failure semantics:** a failed or errored step stops the workflow
immediately — later steps do not run, and whatever partial output the
failed step already produced is left in place, not rolled back. The result
always reports which step failed and why.

**Reproducibility manifest:** every run — success or failure — writes
`run-<workflow-name>-<timestamp>.aida.json` next to the workflow's output,
recording the workspace, profile, model, every tool call, and timings, so a
result can always be traced back to exactly what produced it.

### From the GUI

- **File → Save Conversation as Workflow…** turns the current
  conversation's user prompts into an editable step list, pre-filled with
  the active workspace — opens the same Add-workflow form the Workflows…
  dialog uses, so you can rename steps or add `expect_files` before saving.
- **Workflows…** toolbar button opens the full management dialog:
  Add/Edit/Remove against every stored workflow, and a **Run** button that
  executes one into a normal conversation view (so you watch it happen,
  same as typing the prompts yourself) — the conversation is tagged so it's
  visually distinguishable in the sidebar from an ordinary chat.

## Schedules

A schedule is a workflow plus a time, stored in `~/.aida/schedules.yaml`
(one shared file, unlike workflows). Last-run status lives in SQLite, not
in this file, so the file itself is never rewritten by the scheduler.

```bash
aida schedule add nightly-report --workflow daily-report --at 07:00
aida schedule add hourly-check   --workflow quick-check   --every 4h \
    --var folder=/data/latest --yes-in-allowed

aida schedule list
aida schedule enable nightly-report
aida schedule disable nightly-report
aida schedule remove nightly-report
aida schedule run nightly-report      # fire it right now, regardless of whether it's due
```

`--at HH:MM` fires once a day at that local wall-clock time; `--every`
takes a duration (`30m`, `4h`, `24h`). Semantics: **catch-up-once, never
skip-silently, never overlap.** If AIDA wasn't running when a daily time
passed, it fires once the next time the scheduler ticks — it does not
replay every missed day. Only one fire of a given schedule runs at a time,
enforced both in-process and (see below) across processes.

### Running it

**In the GUI:** nothing to start — `MainWindow` runs the scheduler as a
background task for the life of the app. The **Schedules…** toolbar button
opens the management dialog (Add/Edit/Remove/Enable/Disable, plus **Run
Now** to force an immediate fire) and shows last-run time/status per
schedule. A failed run shows a **⚠ N schedule failures** button in the
status bar; clicking it opens the Schedules dialog and clears the count.

**Headless, no GUI:**

```bash
aida schedule watch --poll-seconds 30
```

Blocks in the terminal, printing each run as it fires, until Ctrl-C. This
runs the *identical* `scheduler_loop` coroutine the GUI drives on its
background thread — a beamline control machine can run this instead of the
GUI and get the same scheduling behavior with no window at all.

A GUI instance and a `watch` process pointed at the same `~/.aida` won't
double-fire a schedule: both take a non-blocking advisory lock
(`~/.aida/scheduler.lock`) before executing a due run, so whichever gets
there first on a given tick wins and the other skips that tick (and
retries next tick, same "deferred, not skipped" behavior described below).

### Deferred, not skipped — the scheduler and you at the same time

The scheduler will not fire a job on top of you while you're using AIDA.
By default, a due job waits until:

- **no turn is currently running**, and
- **the input box is empty**, and
- **it's been at least 5 minutes** since your last message, keystroke, or
  turn finished.

None of that is a new "pending" state — a held-back job simply isn't
marked as having fired, so it stays due and is retried on the very next
tick. If you're still busy an hour later (configurable — see **Settings**),
the softer two conditions are waived and it runs anyway; a turn that is
*actually streaming* is never interrupted, at any wait time. An explicit
**Run Now** from the Schedules dialog always fires immediately, deferral
skipped entirely — it's you asking for it now.

While anything is held back, the status bar shows **⏳ N jobs waiting**
with a tooltip naming each job and why; click it to open the Schedules
dialog, where **Run Now** lets one through immediately if you don't want
to wait.

**Settings → Scheduler: wait for me** / **Scheduler: run anyway after**
control the quiet period and the cap, in minutes; either can be set to 0
(**Never wait** / **Wait indefinitely**).

This only applies to the GUI, which has a user session to check.
`aida schedule watch` has no user to defer to — a schedule always fires
there once it's due, so treat a headless `watch` box and an interactively
used GUI as alternatives, not something you run against each other
unattended.

## Why no OS-level scheduler

launchd, Task Scheduler, and systemd timers were considered and
deliberately deferred rather than built now. The in-app scheduler and
`aida schedule watch` already cover "AIDA may not be running" on a
beamline machine that's normally on 24/7; the three installers would each
add real platform-specific surface (plist/XML/unit-file generation,
absolute interpreter paths, per-OS secret access quirks) mainly to buy "the
user may be logged out," which is a much narrower gap on that kind of
machine. Nothing built here is thrown away if they're added later — a
schedule's `trigger: in-app` field exists specifically so a future
`trigger: system` value is a config change, not a schema change. See
[`planning/phase10_scheduling_design.md`](../planning/phase10_scheduling_design.md)
for the full reasoning, and hand-written cron/launchd/`schtasks` recipes if
you want to invoke `aida run`/`aida workflow run` from an OS scheduler
yourself in the meantime — that path works today with no AIDA-side
changes.
