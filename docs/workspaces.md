# Workspaces

> **Status: beta (0.1.0b2).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [safety-and-permissions.md](safety-and-permissions.md) · [mcp-servers.md](mcp-servers.md) · [coding-and-scripting.md](coding-and-scripting.md) · [knowledge-bases.md](knowledge-bases.md)

## What a workspace is

A workspace is a named bundle of everything AIDA needs to work on one
project: which provider profile to talk to, which folders it can read from
and write to, which MCP servers and skills are available, which knowledge
bases it can search, how cautious it should be about writes/deletes/commands,
and how it's allowed to run Python/shell scripts. Instead of re-specifying
all of that on every session, you pick one workspace by name and get the
whole environment at once.

Workspaces live in `~/.aida/workspaces.yaml`, keyed by name. You can manage
them with the `aida workspace` CLI or, for the parts that already have an
editor, from the toolbar in the GUI.

## Fields

| Field | Default | What it does |
|---|---|---|
| `profile` | `None` | Provider profile name from `providers.yaml` this workspace uses. |
| `source_folders` | `[]` | Folders this workspace may read from. |
| `target_folder` | `None` | Folder this workspace writes results into. |
| `sidecar_folder_name` | `"figures"` | Subfolder under `target_folder` where generated report images get copied (Obsidian-style attachments folder). |
| `mcp_group` | `"none"` | Named MCP server group from `mcp.json` enabled for this workspace — see [mcp-servers.md](mcp-servers.md). |
| `skills` | `[]` | Skill names available to this workspace. |
| `system_prompt` | `None` | Extra system prompt text appended for this workspace. |
| `safety` | `"confirm"` | `"confirm"` (ask before every write/delete) or `"relaxed"` (only asks for actions outside the workspace's allowed folders) — see [safety-and-permissions.md](safety-and-permissions.md). |
| `knowledge_bases` | `[]` | Names into `knowledge.yaml`'s knowledge bases this workspace can search — see [knowledge-bases.md](knowledge-bases.md). |
| `command_allowlist` | `[]` | Shell/Python invocations `run_command` may run without confirmation, unioned with `config.yaml`'s global list — see [safety-and-permissions.md](safety-and-permissions.md). |
| `python_interpreter` | `None` | Path to a conda/venv `python` executable used for `run_python_script`; `None` uses whatever AIDA itself runs under — see [coding-and-scripting.md](coding-and-scripting.md). |
| `scripting_enabled` | `True` | On/off switch for `run_python_script`/`run_command` in this workspace — see [coding-and-scripting.md](coding-and-scripting.md). |
| `templates_dir` | `None` | Folder of `.py` code templates offered to this workspace; `None` means no templates — see [coding-and-scripting.md](coding-and-scripting.md). |
| `saved_scripts_dir` | `None` | Where the Code Editor saves scripts; `None` defaults to `<target_folder>/saved_scripts` — see [coding-and-scripting.md](coding-and-scripting.md). |
| `quick_tasks` | `[]` | Up to ten named prompt templates for this workspace's routine jobs, shown in the GUI's **Quick Tasks** panel — see [gui-overview.md](gui-overview.md). Each entry is a `name`/`text` pair; managed from the panel's right-click menu, no CLI flag. |
| `script_timeout_seconds` | `30.0` | Seconds a `run_python_script`/`run_command` invocation gets before its subprocess is killed — a true per-workspace ceiling (a model-requested longer timeout is capped at this, not honored unbounded) — see [coding-and-scripting.md](coding-and-scripting.md). |

## CLI usage

```bash
aida workspace list                 # names + profile + mcp_group, one line each
aida workspace show <name>          # full field dump + validation warnings
aida workspace new <name> [flags]   # create — refuses if the name already exists
aida workspace edit <name> [flags]  # update — requires the name to already exist
```

`new` and `edit` share the same flags (`--profile`, `--source-folders`,
`--target-folder`, `--sidecar-folder-name`, `--mcp-group`, `--skills`,
`--knowledge-bases`, `--system-prompt`, `--safety`, `--command-allowlist`,
`--python-interpreter`, `--scripting-enabled`/`--no-scripting-enabled`,
`--templates-dir`, `--saved-scripts-dir`), but they behave differently for a
flag you don't pass: on `new`, an unset flag falls back to that field's real
default (e.g. `safety` becomes `"confirm"`, `scripting_enabled` becomes
`True`); on `edit`, an unset flag leaves the existing value untouched — only
the flags you actually pass get changed.

`script_timeout_seconds` and `quick_tasks` have no `new`/`edit` flag (and
`aida workspace show` doesn't print them). `new` therefore creates a
workspace with the defaults for both; `edit` **preserves** whatever they
were set to, along with any other field you didn't pass a flag for. Set
them from the GUI — the **Workspaces…** dialog for the timeout, the Quick
Tasks panel for the tasks — or by editing `workspaces.yaml` directly.

Example — create a workspace with a couple of flags, then tweak one field
afterward:

```bash
aida workspace new usaxs-review \
    --profile argo-claude \
    --source-folders "/Volumes/data/USAXS_2026_08" \
    --target-folder "~/Documents/Aida/usaxs-review" \
    --safety relaxed

# later, just change the safety mode — everything else stays as-is
aida workspace edit usaxs-review --safety confirm
```

## GUI usage

The toolbar's **workspace selector** dropdown lists every configured
workspace plus a `(no workspace)` option. Picking a different one always
starts a new conversation, and asks for confirmation first — switching
workspaces mid-conversation isn't supported.

The **Folders** panel next to it shows the active workspace's editable
settings:

- **Source folders** — one row per folder, each with its own **Remove**
  button; **Add Source Folder…** opens a native folder picker to add one.
- **Target folder** — shown with a **Change Target Folder…** button.
- **Sidecar folder name** — a plain text field.
- **Allowed commands** — a list of `run_command` patterns that need no
  confirmation, each with its own **Remove** button, plus a text field +
  **Add Command** button to add one.
- **Python interpreter** — a text field (with a **Browse…** picker) for the
  `python_interpreter` path used by `run_python_script`.

All of these edits happen in memory only — nothing is written to
`workspaces.yaml` until you click **Save to Workspace**. Switching away or
closing the app without saving discards the changes.

Separately, the toolbar's **Workspaces…** button opens the Workspace
Management dialog — Add…/Edit…/Remove… against the full list of saved
workspaces, persisted immediately (no "Save to Workspace" step). Its
Add/Edit form covers every field except `templates_dir`/`saved_scripts_dir`
(see the gap note below), including several the Folders panel above has no
control for at all: `profile`, `mcp_group`, `skills`, `knowledge_bases`,
`system_prompt`, `safety`, `scripting_enabled`, and the **Script/command
timeout** spinner (1-3600s) for `script_timeout_seconds`.

## Current gap: no GUI editor yet

Two fields are CLI/config-file only for now — neither the Folders panel nor
the Workspace Management dialog has a control for either:

- `templates_dir`
- `saved_scripts_dir`

Edit these with `aida workspace edit <name> --templates-dir ... --saved-scripts-dir ...`
or by hand-editing `workspaces.yaml`.

## Full example

See [`examples/config/workspaces.yaml`](../examples/config/workspaces.yaml)
for a fully commented, worked example with two workspaces.
