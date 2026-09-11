# Moving and sharing a setup

Everything AIDA knows about *how you work* — provider profiles, workspaces,
MCP servers, knowledge-base definitions, schedules, workflows, skills and
prompt files — can be written to a single file and imported on another
machine. Use it to stand up a second computer, to hand a working setup to a
colleague, or to move between beamline workstations.

```
aida config export ~/aida-setup.zip          # on the machine you have set up
aida config import ~/aida-setup.zip          # on the new one
```

In the GUI: **File → Export Setup…** and **File → Import Setup…**.

## No secrets, ever

**No API key, token or password is written to a bundle.** Provider secrets
live in the OS keychain, which is not exportable; and AIDA also scans each
MCP server's `env` block — the one place a key can sit in plain text — and
strips anything that looks like a credential before writing the bundle.

What the bundle carries instead is the *names*. After importing, the report
tells you exactly what to set:

```
SECRETS TO SET (none travel in a bundle):
  aida config secret set argo
  aida config secret set openrouter
  mcp.json: brave-search needs env BRAVE_API_KEY filled in
```

Run those (or use **Providers…** for a profile's key and **MCP Servers…**
for a server's environment) and the imported setup is complete.

## Paths are translated, not copied

Your folders are not in the same place on the other machine, so AIDA does
not copy them literally. On export, three kinds of path are replaced with a
token, and on import each token is resolved against the target machine:

| Token | Becomes |
|---|---|
| `${HOME}` | that machine's home directory |
| `${AIDA_HOME}` | that machine's `~/.aida` |
| `${CONDA_ENV:pyirena}` | wherever that machine keeps its `pyirena` conda environment |

The conda one is what saves the most hand-editing: an `mcp.json` full of
`/opt/miniconda3/envs/pyirena/bin/pyirena-mcp` entries resolves by itself,
including across platforms — the same entry finds
`…\envs\pyirena\Scripts\pyirena-mcp.exe` on Windows. If the environment
isn't there, AIDA looks for the bare command on `PATH`; failing that it
leaves the token in place and says so in the report, rather than writing a
plausible-looking path that doesn't exist:

```
COULD NOT LOCATE ON THIS MACHINE — fix before use:
  MCP server 'bait-mcp': conda env 'bait' not found on this machine
```

Relative paths (`prompts/pyirena.md`), bare commands (`npx`) and system
paths (`/etc/bait/instrument.yaml`) are already portable and are left
untouched. `{user}` placeholders survive too — see
[`organizing-conversations.md`](organizing-conversations.md).

## What a bundle contains

| Included | Not included |
|---|---|
| Provider and embedding profiles | Secrets of any kind |
| Workspaces | Conversations, transcripts, attachments |
| MCP server definitions | Artifacts and knowledge-base indexes |
| Knowledge-base definitions | Window size/position, font size, layout |
| Schedules and stored workflows | Personal context and workspace notes *(unless you ask)* |
| Skills and prompt files | |

A bundle is a plain zip — open it and read it before sending it to anyone.
`README.txt` inside says what it holds, `secrets.json` lists the secret
names it expects, and `paths.json` lists every path that was translated.

### Personal fields

Your personal context, user names and private workspace notes are **left
out by default**, so a bundle is always safe to email. When you are moving
to your own second machine and want them:

```
aida config export ~/aida-setup.zip --include-personal
```

(In the GUI, the "Include my personal context and private workspace notes"
checkbox.)

## Importing part of a bundle

To see what a bundle holds without importing anything:

```
aida config import ~/aida-setup.zip --list
```

Every line it prints doubles as a selector. To take just one workspace:

```
aida config import ~/aida-setup.zip --only workspace:analysis
```

**Whatever that workspace needs comes along automatically** — its provider
profile, its skills, its knowledge bases, its prompt file, and every MCP
server in its `mcp_group`. A workspace imported without those would pass
through cleanly and then fail the moment you tried to use it, so the import
brings the closure and tells you what it added:

```
Pulled in as dependencies of what you selected:
  MCP server: pyirena-mcp
  profile: ollama-best
  skill: pyirena-usage
```

Selectors are forgiving about spelling — `workspace:`, `workspaces:`,
`server:`, `mcp-server:`, `kb:` and `embedding:` all work — and can be
repeated or comma-separated. `--no-deps` imports literally what you named
and nothing else; expect the result to fail validation unless the rest is
already configured here.

In the GUI, the import dialog's **Contents** tab is the same thing as a
checkable tree: tick a workspace and its dependencies tick themselves, with
a line underneath saying which ones and why.

## Previewing, and fixing paths

`--check` runs the whole import and writes nothing:

```
aida config import ~/aida-setup.zip --check
```

It prints what *would* land, and then the paths that do not resolve here:

```
Paths that do not resolve on this machine:

  ${CONDA_ENV:bait}/bin/bait-mcp   [executable]
      conda env 'bait' not found on this machine
      used by: MCP server 'bait-mcp' command
```

If a folder or program is simply somewhere else on this machine, say so
rather than editing the config afterwards:

```
aida config import ~/aida-setup.zip \
    --map '${HOME}/Experiments=/data/usaxs' \
    --map '${CONDA_ENV:bait}/bin/bait-mcp=/usr/local/bin/bait-mcp'
```

A mapping **matches by prefix**, so the first one above also redirects
`${HOME}/Experiments/2026/scan1` to `/data/usaxs/2026/scan1` — one entry
moves a whole tree. When two mappings both match, the more specific one
wins. Leave a path unmapped to import it unchanged and fix it later.

The GUI's **Paths** tab is the same table: it appears only when something
does not resolve, has an editable "Use instead" column with a Browse
button, and shrinks as you fill it in.

## Importing

An import **never deletes anything**. If a name already exists here, the
default is to keep yours and list the collision in the report:

```
aida config import ~/aida-setup.zip                        # skip what exists (default)
aida config import ~/aida-setup.zip --on-conflict overwrite # bundle wins
aida config import ~/aida-setup.zip --on-conflict rename    # keep both, as "name (imported)"
```

Any config file that already had content is copied to
`<name>.bak-<timestamp>` before it is rewritten, next to the original in
`~/.aida/`.

### General settings are opt-in

The bundle carries your safety mode, allowed folders, iteration and context
caps, and records/scratch folders — but importing does **not** apply them
unless asked, so importing a colleague's workspaces cannot switch your
install to their safety mode:

```
aida config import ~/aida-setup.zip --app-settings
```

Per-screen settings (window geometry, font size, column widths, the last
workspace you used) are never exported at all — a window position from a
two-monitor desk is wrong everywhere else.

## What a bundle cannot carry

A bundle moves *configuration*. It cannot move the things that
configuration points at, and you will need to install those separately on
the new machine:

- **conda environments and the MCP servers in them** — `pyirena`,
  `aievaluator`, `epics-mcp`, and so on
- **Node/`npx`** and any npm-based MCP servers (Playwright, web search)
- **Ollama and its models**
- **your data folders**, and any external templates repository
- **network reachability** — an `EPICS_CA_ADDR_LIST` only resolves on the
  beamline LAN

The import report names the ones this particular bundle expects. The usual
sequence on a new machine is preview, fix, import, check:

```
aida config import ~/aida-setup.zip --check     # what would land, what won't resolve
aida config import ~/aida-setup.zip --map ...   # for real, with any paths corrected
aida doctor                                     # what is still missing on this machine
```

## Conversations and knowledge indexes

Deliberately not part of a bundle. Your conversation history lives in
`~/.aida/aida.db` with its artifacts in `~/.aida/artifacts/` and transcripts
under `~/Documents/Aida/`; a knowledge-base index lives in
`~/.aida/knowledge/`. To move those today, copy the folders directly, and
rebuild any knowledge base whose source folders moved
(`aida kb build <name>`). A supported full-backup command that also fixes
the absolute paths recorded inside the database is planned — see
[`planning/portability.md`](../planning/portability.md).
