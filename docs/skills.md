# Skills

A skill is a set of written instructions, saved as a Markdown file, that
tells the agent *how* to do something: a procedure, your conventions, the
layout of your data. When a skill is active, AIDA adds its text to what the
model sees at the start of every turn. The system prompt says who the
agent is; a skill says how to do the work.

A skill can also bring its own **code and reference files**. That is useful
when local models struggle with a task: rather than asking the model to
write the same parsing or plotting code every time, you give it a tested
script and tell it when to run it.

This page covers where skills live, the two shapes a skill can take, how
one gets switched on, how to ship code with a skill, and what the agent is
allowed to do with it.

## Where skills live

All skills live in one folder, **`~/.aida/skills/`** (on Windows,
`C:\Users\<you>\.aida\skills\`). The folder is hidden in Finder and
Explorer; in the GUI, **File → Open Skills Folder** opens it.

Keep skills and their code here, not in your records folder
(`~/Documents/Aida/` by default). The records folder holds conversation
transcripts and is safe to clean up; the skills folder is part of your
setup. Export and import carry it to another machine (see
[moving-and-sharing.md](moving-and-sharing.md)).

## Two shapes of skill

| Shape | On disk | Use it when |
|---|---|---|
| Single file | `~/.aida/skills/saxs-basics.md` | The skill is only instructions. |
| Folder | `~/.aida/skills/usaxs-overview/SKILL.md`, plus any other files | The skill comes with scripts, templates or reference notes. |

The skill's **name** is the file name without `.md`, or the folder name.
That name is what you list in a workspace's `skills:`, an MCP server's
`skills`, or `--skills`. If both `name.md` and `name/SKILL.md` exist, the
single file wins, so don't keep both.

A folder skill can hold anything next to `SKILL.md`, for example:

```
~/.aida/skills/usaxs-overview/
├── SKILL.md              the instructions (required)
├── scripts/
│   ├── collect_blocks.py
│   └── plot_overview.py
├── templates/
│   └── report_template.md
└── reference/
    └── record_format.md
```

## When a skill is active

A skill is only seen by the model when it is active in the current chat. It
becomes active in any of these ways, and they add up:

1. **The workspace lists it**: `skills: [saxs-basics, usaxs-overview]` in
   `workspaces.yaml`, or the Skills field in the workspace dialog. See
   [workspaces.md](workspaces.md).
2. **An MCP server attaches it**: a server's `skills` list in `mcp.json`
   is loaded whenever that server is running, for example
   `pyirena-usage` with pyIrena. See [mcp-servers.md](mcp-servers.md).
3. **You switch it on for this chat**: the Skills panel in the GUI, or
   `--skills a,b` on `aida chat` / `aida-gui`. **Save to Workspace** in the
   panel keeps the choice.

You can change skills during a chat. The model is told which were added or
removed on its next turn. Edits you make to a skill file reach a running
chat when you next change its skill selection (or click Reload in the
Skills panel); a new chat always reads the current files.

Every active skill costs context on every turn, so keep skills focused, and
switch off the ones a chat doesn't need. See
[context-and-limits.md](context-and-limits.md).

## Shipping code with a skill

Put the scripts in the skill's folder and refer to them in `SKILL.md`
through the **`{skill_dir}`** placeholder:

```markdown
## Building the overview

1. Run `{skill_dir}/scripts/collect_blocks.py` with the cycle folder as its
   argument. It writes `blocks.csv` into the current folder.
2. Run `{skill_dir}/scripts/plot_overview.py blocks.csv` for the figure.
3. Fill in `{skill_dir}/templates/report_template.md` and save the report
   in the target folder.
```

When the skill loads, AIDA replaces `{skill_dir}` with the folder's real
path on this machine, so the same skill works for every user and on macOS,
Windows and Linux, with nothing to configure. Other braces in the skill
(code samples, JSON) are left alone.

You don't have to list the files yourself: for a folder skill that holds
more than `SKILL.md`, AIDA adds a short note above the skill's text naming
the folder and its files (up to 30, ignoring `__pycache__` and hidden
files), and saying the folder is read-only.

### How the scripts run

The agent runs a skill's script with `run_python_script`, like any other
script:

- **Working folder.** It runs in the workspace's **target folder** (or the
  first source folder, or the scratch folder if there is neither), never in
  the skill folder, so the files it writes land with your results. Write
  scripts to take input paths as arguments and write outputs to the current
  folder or to a path they are given.
- **Python environment.** It uses the workspace's `python_interpreter`, or
  the Python AIDA runs in if that is not set. Whatever the script imports
  (numpy, h5py, pyirena...) must be installed in that environment. Say so
  at the top of `SKILL.md`. See
  [coding-and-scripting.md](coding-and-scripting.md#python_interpreter--which-python-runs-your-scripts).
- **Needs a workspace with scripting on.** `run_python_script` exists only
  in a chat with a workspace whose `scripting_enabled` is true (the
  default). In a chat without a workspace the agent can still read the
  skill's files but cannot run them.

### What the agent may do with skill files

The skills folder is **read-only** for the agent:

| Action in `~/.aida/skills/` | `safety: relaxed` | `safety: confirm` |
|---|---|---|
| Read a file (`read_file`, `list_directory`, ...) | allowed | allowed |
| Run a skill's script (`run_python_script`) | allowed | asks |
| Write, edit or delete a file | asks | asks |

Writes always ask so that the agent cannot quietly rewrite its own
instructions or code. If you want the agent to improve a skill, approve the
write when it asks, or edit the file yourself. See
[safety-and-permissions.md](safety-and-permissions.md).

## Writing a good skill

- **Start with when to use it.** One or two sentences: "Use this when the
  user asks for a review of a USAXS run block."
- **Give the map before the steps.** Where the data is, how files are
  named, what to trust first. This is what saves the model from
  rediscovering the layout every time.
- **Name the scripts and their arguments exactly**, with `{skill_dir}`,
  and say what each one writes.
- **Prefer scripts to long instructions** for anything mechanical
  (parsing, counting files, plotting). Instructions are for judgement.
- **Keep it short.** A skill is sent on every turn. Put long reference
  material in a separate file in the skill's folder and tell the model to
  read it when needed.

## Sample skills

AIDA ships a few starter skills (`saxs-basics`, `pyirena-usage`,
`review-checklist`, and others, in the repo's [`skills/`](../skills/)
folder). `aida mcp add-pyirena` installs the ones pyIrena uses. A skill
already in `~/.aida/skills/` is never overwritten by an install or an
upgrade: once it is there, it is yours. The same rule applies to folder
skills, which are copied whole.

## Troubleshooting

- **"skill file(s) not found (will be skipped)"**: the name in the
  workspace doesn't match a file or folder. Check the spelling, and that a
  folder skill has a file called exactly `SKILL.md`.
- **The agent asks before reading a skill's file**: the path it used is
  outside `~/.aida/skills/` (for example a copy elsewhere). Point the skill
  at `{skill_dir}`.
- **The script fails with `ModuleNotFoundError`**: the workspace's Python
  environment lacks a package the script needs. Install it there, or set
  `python_interpreter` to an environment that has it.
- **Output files are missing**: they are in the target folder, not in the
  skill folder. If the workspace has no target folder, look in the scratch
  folder (**File → Open Scratch Folder**).
