# Coding and scripting

> **Status: pre-alpha.** Config formats and CLI commands may change without
> notice until Phase 5. See [`PLAN.md`](../PLAN.md) for the full roadmap.

**Related:** [safety-and-permissions.md](safety-and-permissions.md) · [workspaces.md](workspaces.md)

## The pieces

A workspace controls whether the agent can run code at all, and how, through
five `WorkspaceConfig` fields:

| Field | Default | What it does |
|---|---|---|
| `scripting_enabled` | `True` | Master on/off switch for `run_python_script`/`run_command` in this workspace. |
| `python_interpreter` | `None` | Path to the `python` executable `run_python_script` runs with. `None` uses whatever AIDA itself runs under. |
| `templates_dir` | `None` | A flat folder of `.py` files whose docstrings get surfaced to the model as house-convention templates. |
| `saved_scripts_dir` | `None` | Where the Code Editor's Save/Save As write to. `None` defaults to `<target_folder>/saved_scripts`. |
| `command_allowlist` | `[]` | Shell command patterns `run_command` may run without asking — see [safety-and-permissions.md](safety-and-permissions.md). |

## `scripting_enabled` — the master switch

`scripting_enabled` defaults to `True`. Set it to `False` for a workspace
that shouldn't be able to run anything at all — when it's off,
`run_python_script` and `run_command` aren't just refused, they aren't even
registered as tools for that session, so the model never sees them offered.

```bash
aida workspace edit <name> --no-scripting-enabled   # turn scripting off
aida workspace edit <name> --scripting-enabled      # turn it back on
```

There's no GUI control for this field yet — it's CLI/config-file only (edit
`workspaces.yaml` directly, or use the flags above).

## `python_interpreter` — which Python runs your scripts

`python_interpreter` is a **direct path to a conda or venv environment's own
`python` executable** — for example:

```
~/miniconda3/envs/aievaluator/bin/python
```

It is deliberately **not** a conda environment *name*. Pointing AIDA at a
name would mean shelling into `conda activate` first, which is fragile and
platform-dependent; a direct path to the interpreter binary works the same
way everywhere and needs no shell activation step. Leave it unset (`None`)
and `run_python_script` uses whatever interpreter AIDA's own process is
running under (`sys.executable`) — fine for quick checks, but usually not
where your instrument/analysis packages are actually installed.

Set it with:

```bash
aida workspace edit <name> --python-interpreter ~/miniconda3/envs/aievaluator/bin/python
```

or, in the GUI, the **Python interpreter** text field (with a **Browse…**
picker) in the toolbar's Folders panel — see
[workspaces.md](workspaces.md) for the full panel. Both the Code Editor's
Run button and the model's own `run_python_script` calls use this same
interpreter.

## `run_python_script` vs `run_command` — which to use

This is the part that causes real confusion, so it's worth stating plainly:

**`run_python_script` is not gated by the command allowlist at all.** It only
goes through the normal folder-safety check (is the script inside an
allowed folder, and does the workspace's `confirm`/`relaxed` mode allow
running it) — the same rule that governs any other write or delete. It runs
a real subprocess (`asyncio.create_subprocess_exec`, never a shell) with a
timeout, using the workspace's `python_interpreter`.

**`run_command` is gated by both** folder containment *and* the command
allowlist (see [safety-and-permissions.md](safety-and-permissions.md) for
the full allowlist syntax) — both conditions have to hold before the
workspace's `confirm`/`relaxed` mode even applies; if either fails, it
always asks first.

So:

- **Use `run_python_script`** for ad hoc, one-off Python — checking data,
  running an analysis snippet, probing what's importable. Each of these is
  different every time, so pre-allowlisting every variant doesn't make
  sense; this tool exists precisely so the model doesn't need a command
  allowlisted for every one-liner it wants to try.
- **Use `run_command`** (plus the allowlist) for a short, fixed, reusable
  list of shell invocations you're comfortable pre-approving —
  `git status`, `git log *`, a named test/lint/analysis script. It is not
  meant to launder arbitrary Python through the allowlist: trying to
  allowlist every `python3 -c "..."` invocation you might want doesn't
  scale, and `run_python_script` is the tool built for that case instead.

AIDA tells the model this directly at session start (it lists the
configured interpreter and the current allowlist in the system context) so
the model doesn't resort to ad hoc shell probes — a bug seen in practice
before this context existed, where the model would try `run_command`
with a raw `python3 -c "..."` just to find out what interpreter/packages
were available, triggering a confirmation prompt for something
`run_python_script` could have answered without one.

## `templates_dir` — house conventions for generated scripts

`templates_dir` points at a flat folder of plain `.py` files (not recursive
— files directly under the folder). For each file, AIDA reads its module
docstring and surfaces a compact list of **name + docstring** (not the full
source) to the model, so it follows your house conventions when writing
instrument functions. A template file that fails to parse is silently
skipped rather than breaking the whole session.

There's no GUI editor for this field yet — it's CLI/config-file only:

```bash
aida workspace edit <name> --templates-dir ~/bits-usaxs/templates
```

or edit `workspaces.yaml` by hand.

## `saved_scripts_dir` — where the Code Editor saves

`saved_scripts_dir` is where the Code Editor's Save/Save As write scripts.
Leave it unset and it defaults to `<target_folder>/saved_scripts`. Set it
explicitly if you want saved scripts to live somewhere other than under the
workspace's target folder:

```bash
aida workspace edit <name> --saved-scripts-dir ~/bits-usaxs/scripts
```

Like `templates_dir`, there's no GUI control for this field yet.

## The Code Editor dialog

Open it two ways:

- **Toolbar → "Code Editor…"** — opens a blank editor.
- **"</> Open in Editor"** button on a chat message — appears under any
  assistant message that contains a fenced code block, and pre-fills the
  editor with that block's content (only the first fenced block in the
  message, if there's more than one).

Once open:

- **Save** writes to the current file if one's already been chosen;
  otherwise it behaves like Save As.
- **Save As…** opens a native file picker, defaulting to the workspace's
  resolved `saved_scripts_dir`.
- **Run** saves your latest edits first, then executes the script with the
  workspace's configured `python_interpreter` as a real subprocess (with a
  timeout — 30 seconds by default), streaming exit code, stdout, and stderr
  back into the output pane below the editor.
- **Kill** stops a script that's currently running.

Running from the Code Editor uses the same execution path
(`run_python_script`'s underlying runner) as the model's own tool calls, so
what you see running here behaves the same way it would in a chat turn.
