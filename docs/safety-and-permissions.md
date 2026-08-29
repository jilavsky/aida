# Safety and permissions

> **Status: beta (0.1.0b2).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [workspaces.md](workspaces.md) · [coding-and-scripting.md](coding-and-scripting.md) · [mcp-servers.md](mcp-servers.md)

## The mental model

Every native file operation, script run, and shell command goes through a
`SafetyGuard` before it touches anything. The guard's decisions come down to
one question first, then a second one:

1. **Is the path inside the "allowed folders" set?** That set is the union
   of:
   - the current workspace's own `source_folders` and `target_folder`
     (configuring a folder for a workspace is what makes it allowed for that
     workspace),
   - any folders listed in `~/.aida/config.yaml`'s `allowed_folders` (global,
     applies to every workspace), and
   - AIDA's own `~/.aida/artifacts/` folder — always allowed, in every
     workspace, regardless of config. That's where generated files (PNGs,
     etc.) land; it's AIDA's own output area, not your data. The *rest* of
     `~/.aida/` (`config.yaml`, secret references, the SQLite DB) is **not**
     in the allowed set and stays gated like any other outside path.
2. **If it's inside that set, what does the workspace's `safety` mode say?**

**Reads are never gated inside the allowed set**, in either mode — if a
folder is configured as a source/target folder (or globally allowed), the
agent can read files there without asking. Only *writes*, *deletes*, and
*script/command runs* are subject to the mode below. A read of a path
**outside** the allowed set still asks for confirmation, same as a write
would.

## `safety: confirm` vs `safety: relaxed`

Each workspace has a `safety` setting (`WorkspaceConfig.safety`) — the
default for new workspaces is `confirm`, and `~/.aida/config.yaml`'s
`default_safety_mode` sets that default. This only affects mutating actions
**inside** the workspace's allowed folders:

- **`confirm`** (default): every write, delete, or script/command run inside
  an allowed folder pops a confirmation prompt (a real terminal prompt in the
  CLI, a modal dialog in the GUI) before it happens. You approve or deny each
  one individually.
- **`relaxed`**: the same actions proceed without asking, as long as they're
  inside the allowed folders. Switching a workspace to `relaxed` shows a
  one-time warning the first time it's enabled, reminding you that deletes
  still go to `_trash` (recoverable) but overwrites are not undo-able — so
  make sure the folders you've relaxed are backed up.

Independently of the mode, `write_file`, `copy_file`, and `move_file` all
refuse to replace a file that already exists unless the call passes
`overwrite=true`. That refusal is about *clobbering*, not permissions: it
applies in `relaxed` mode too, because an overwritten file has no `_trash`
copy to recover from.

## What *always* asks, regardless of mode

This is the part that matters most, because it's independent of whether the
workspace is `relaxed` or `confirm`:

- **Any path outside the allowed-folders set** — a read, write, delete, or
  script run touching a path that isn't under a source/target folder, a
  globally-allowed folder, or `~/.aida/artifacts/` always asks first, even in
  a fully `relaxed` workspace.
- **`run_command` (raw shell commands)** — two conditions both have to hold
  for the workspace's mode to apply at all: the command's working directory
  must be inside the allowed folders, *and* the command itself must match
  the command allowlist (below). If either one fails, it always asks —
  regardless of mode. Only when both hold does `relaxed`/`confirm` govern it
  the normal way.
- **`fetch_url`** — every single call asks for confirmation, unconditionally,
  in every mode, for every workspace. There's no per-workspace toggle to turn
  this off: no folder concept applies to a URL, so this is the one place
  AIDA always surfaces the "this is about to leave your machine" moment.

(Running a script that already lives in an allowed folder, via
`run_python_script`, is different from `run_command` — see
[coding-and-scripting.md](coding-and-scripting.md) for that distinction and
when to use which.)

## Deletes go to `_trash`, not gone

When the agent deletes a file, it's moved into a `_trash` subfolder created
at the root of whichever allowed folder contains it — not permanently
removed. You can recover a deleted file from there. This can be turned off
per session (`trash_enabled=False`), which makes deletes permanent instead;
that's not something you'd normally configure by hand.

## The command allowlist

`run_command` also checks the command text itself against a **command
allowlist** — a short, user-editable list of command patterns considered
safe to run without extra scrutiny. Matching is deliberately simple: no
shell parsing, no globs or regexes, just a token-prefix comparison after
both the pattern and the command are split the way a shell would split them
(`shlex`):

- `git status` — an exact match. Only literally `git status` matches.
- `git log *` — a trailing `*` means "this prefix, plus any further
  arguments." `git log`, `git log -5`, and `git log --oneline main` all
  match; `git log --oneline -- file.py` also matches (everything after the
  fixed prefix is free).

A command that doesn't match any pattern simply isn't allowlisted — it isn't
an error, it just means `run_command` always asks for confirmation for that
invocation (per the always-confirm rule above).

The allowlist is configured in two places that get unioned together:

- globally, in `~/.aida/config.yaml`'s `command_allowlist` (applies to every
  workspace), and
- per-workspace, in that workspace's own `command_allowlist`.

To edit the per-workspace list, use `aida workspace edit NAME
--command-allowlist "git status,git log *"` (comma-separated patterns) or
the FolderDisplay panel in the GUI — see
[workspaces.md](workspaces.md) for the full editing workflow.

See [coding-and-scripting.md](coding-and-scripting.md) for guidance on
*when* to reach for the allowlist versus not: it's meant for a short list of
fixed, known-safe invocations (`git status`, a specific lint/test command,
etc.), not for ad hoc one-off shell commands. For "run some Python I don't
want to hand-approve every time," `run_python_script` is the better tool —
it isn't allowlist-gated at all (the file just needs to live in an allowed
folder, subject to the same relaxed/confirm rule as any other write).

## Practical guidance

- **Start new workspaces in `confirm` mode** (the default) until you trust
  what the agent tends to do with that workspace's folders.
- **Switch to `relaxed`** only for a workspace/folder you're genuinely
  comfortable letting the agent write to, move files in, or delete from
  without a prompt each time — e.g. a scratch analysis folder you don't mind
  it iterating on freely. Back up anything important there first, since
  overwrites in `relaxed` mode aren't recoverable the way deletes are.
- Keep the command allowlist short and specific. A wildcard pattern like
  `git log *` is fine for a read-only command family; avoid allowlisting
  anything that writes or deletes on its own.
- Remember that `fetch_url` will ask every time no matter what — that's by
  design, not a bug to work around.
