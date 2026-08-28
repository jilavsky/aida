# MCP servers

> **Status: beta (0.1.0b1).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in the release notes.
> See [`PLAN.md`](../PLAN.md) for what is still planned.

**Related:** [workspaces.md](workspaces.md) · [safety-and-permissions.md](safety-and-permissions.md)

An **MCP server** is an external process (pyIrena's tool server, an
instrument-control server, a web-search server, ...) that AIDA talks to over
the [Model Context Protocol](https://modelcontextprotocol.io/) to get extra
tools beyond AIDA's small set of built-ins. Servers are configured in
`~/.aida/mcp.json`, either by hand, via `aida mcp ...`, or via the
**MCP Servers…** dialog in the GUI.

## `mcp.json` shape

`mcp.json` starts from the same `{"mcpServers": {...}}` shape Claude Desktop
and other MCP clients use, so a config you already have elsewhere is
portable — see [Importing an existing config](#importing-an-existing-config-including-claude-desktop).
Each server entry carries the standard `command`/`args`/`env` (how to launch
it as a stdio subprocess), plus four AIDA-specific extensions that other
clients simply ignore:

| Field | Standard/AIDA | Meaning |
|---|---|---|
| `command` | standard | Executable to launch (stdio transport) |
| `args` | standard | Command-line arguments, in order |
| `env` | standard | Environment variables passed to the subprocess. A value of `keyring:NAME` or `secret:NAME` defers to the OS keychain instead of a plaintext value — see [Storing secrets in the OS keychain](#storing-secrets-in-the-os-keychain) |
| `groups` | AIDA | Named group(s) this server belongs to (see [Groups](#groups)) |
| `skills` | AIDA | Skill file names to load into context when this server is active |
| `disabled_tools` | AIDA | Tool names hidden from the model entirely (see [Per-tool permissions](#per-tool-permissions)) |
| `confirm_tools` | AIDA | Tool names that always require confirmation before a call |

```json
{
  "mcpServers": {
    "pyirena-mcp": {
      "command": "/opt/conda/envs/pyirena/bin/pyirena-mcp",
      "args": [],
      "env": {},
      "groups": ["pyirena-analysis"],
      "skills": ["pyirena-usage"]
    }
  }
}
```

Any key AIDA doesn't model itself (a Claude-Desktop export routinely has
`disabled`, `autoApprove`, `type`, `cwd`, etc.) is preserved verbatim in an
internal `extra` bucket rather than discarded — loading a real-world config
never errors, and saving it back out afterward (an edit via CLI or GUI)
never silently drops fields the file already had.

## Adding a server via CLI

```bash
aida mcp server add pyirena-mcp \
  --command /opt/conda/envs/pyirena/bin/pyirena-mcp \
  --groups pyirena-analysis \
  --skills pyirena-usage
```

`--arg` and `--env` are repeatable flags for servers that need command-line
arguments or environment variables:

```bash
aida mcp server add bait-mcp \
  --command /opt/conda/envs/bait/bin/bait-mcp \
  --arg --config --arg /etc/bait/instrument.yaml \
  --env BAIT_LOG_LEVEL=INFO \
  --groups instrument-control
```

`--groups` and `--skills` each take a comma-separated list. Other useful
subcommands:

```bash
aida mcp server list                 # one line per configured server
aida mcp server show pyirena-mcp     # full config for one server
aida mcp server edit pyirena-mcp --groups pyirena-analysis,pyirena-fitting
aida mcp server remove pyirena-mcp   # prompts unless --yes is given
```

`aida mcp server edit` only changes the flags you pass — an omitted flag
leaves that field as-is. One CLI-only limitation: because `--arg`/`--env`
are repeatable flags, there's no way to explicitly clear a server's `args`
or `env` back to empty via `edit` (only to leave them unchanged, or replace
them with one or more new values) — do that from the GUI's form instead,
where zero lines simply means empty.

### Importing an existing config (including Claude Desktop)

```bash
aida mcp import ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

This merges the file's servers into your existing `mcp.json` **without
clobbering anything** — a server name already configured in AIDA is left
untouched by default, and reported as skipped. To replace specific
already-configured servers with the imported version instead, name them
explicitly:

```bash
aida mcp import ~/path/to/mcp.json --overwrite pyirena-mcp,bait-mcp
```

### Testing a server

```bash
aida mcp server test pyirena-mcp
```

This actually launches the server, initializes the connection, and lists
its tools — `OK — 12 tool(s), 0.34s` (or `FAILED — <error>`) rather than
just checking that the config entry exists.

### Storing secrets in the OS keychain

Any `env` value can be written as `keyring:NAME` or `secret:NAME` (both
accepted — `keyring:` reads more naturally standalone, `secret:` mirrors
provider profiles' own `secret_ref` terminology) instead of a plaintext
value. AIDA resolves it via the same `aida.config.secrets` store the model
providers use — the OS keychain, or an `AIDA_SECRET_<NAME>` environment
variable as a fallback — right before launching the server subprocess, so
the real value never sits in `mcp.json` at all. A reference to a name with
nothing stored under it fails that one server's startup with a clear
message rather than launching a subprocess that's silently missing a
credential it needs.

```bash
aida config secret set pyirena_api_key
```

then in `mcp.json`:

```json
{"env": {"PYIRENA_API_KEY": "keyring:pyirena_api_key"}}
```

The GUI's Add/Edit Server form has a **Store Value in Keychain…** button
next to the Env field: pick which `KEY=VALUE` line to convert, name it in
the keychain, enter the value once, and the form swaps that line for
`KEY=keyring:name` for you.

## Adding/editing via the GUI

The **MCP Servers…** toolbar action opens the `McpManagementDialog`, a
full front end over the same `mcp.json` the CLI edits (changes are saved
immediately, no separate "Save" step for most actions).

- **Add Server…** opens a form with fields for name, command, args
  (one per line), env (`KEY=VALUE` per line, with a "Hide values" toggle to
  mask them on screen, and a **Store Value in Keychain…** button — see
  [Storing secrets in the OS keychain](#storing-secrets-in-the-os-keychain)),
  and checkable lists for **groups** (with an inline "add a new group" box)
  and **skills**. **Edit…** opens the same form pre-filled for the selected
  server (name isn't editable once created).
- **Start / Stop / Restart** control the server's live subprocess for the
  current session, without restarting the whole app. Starting multiple
  servers at once (e.g. at workspace launch) launches them concurrently
  rather than one after another, so N servers cost roughly the slowest
  single handshake, not the sum of all of them; one server failing to
  start still leaves the rest running normally.
- **Test Connection** does the same initialize-and-list-tools check as
  `aida mcp server test`, reporting tool count and timing (or the error).
- **Import mcp.json…** file-picks a config and merges it the same
  "skip conflicts unless you say otherwise" way as `aida mcp import` — if
  imported names collide with existing servers, the dialog asks whether to
  overwrite them.
- **Tools tab** lists every tool the server exposes (discovered by
  starting or testing the server) with two checkboxes per tool: **Enabled**
  and **Confirm before run** — the GUI equivalent of `disabled_tools` and
  `confirm_tools`. Click **Save Tool Permissions** to persist. Note: if the
  server is currently running, a permission change here only takes effect
  once the server is restarted (the dialog does this for you automatically
  after saving) — a live session already has the old tool set merged in.
- **Log tab** shows recent tool calls for the selected server (tool name,
  ok/error, duration); double-click a row to see the raw JSON (arguments,
  full result content, error message) in a copyable inspector.
- **Skills…** opens a browser over `~/.aida/skills/`: list, preview
  (rendered Markdown), open in your external editor, or create a new skill
  from a blank template.
- **Groups…** opens an editor listing every group name currently in use
  and its member servers, with **Add Group…**, **Rename…**, and
  **Delete…** — see below.

## Groups

A group is **not** a separately stored list — it's just a name that one or
more servers happen to list in their own `groups` field. `aida mcp group
list` (or the GUI's Groups… dialog) simply scans every configured server
and reports which group names are referenced and by whom. Renaming or
deleting a group (`aida mcp group rename <old> <new>` / `aida mcp group
delete <name>`) rewrites that name in every server's `groups` list — there
is no registry entry to update separately.

Because a group is purely derived from who references it, a group with
zero members can't be represented at all — there's nothing to "create" in
isolation. So creating one always means naming it *and* picking at least
one existing server for it in the same step:

```bash
aida mcp group add pyirena-analysis --servers pyirena-mcp,bait-mcp
```

`--servers` is a comma-separated list of already-configured server names;
an unknown name is rejected rather than silently skipped. Naming an
existing group adds the given servers to it instead of erroring. In the
GUI, the Groups… dialog's **Add Group…** button opens a small picker (a
name field plus a checklist of every configured server) — this is also
reachable one server at a time from that server's own Add/Edit form via
its inline "add a new group" box, but the Groups… dialog's version handles
several servers at once. Picking a name that already exists asks for
confirmation before adding the checked servers to it.

A [workspace](workspaces.md) picks exactly one group via
`WorkspaceConfig.mcp_group`; every server listing that group name is
started and offered to the model for that workspace's sessions. The
sentinel value `"none"` (the default) means no MCP servers/tools at all for
that workspace, not a group literally named "none".

The rationale for grouping rather than "just turn on every configured
server everywhere": a fully-loaded pyIrena server can expose 100+ tools,
which overloads a small local model's context and tool-selection ability
when most of those tools are irrelevant to the task at hand. Grouping lets
each workspace opt into only the servers/tools it actually needs.

## Per-tool permissions

Independent of groups, each server has two tool-level lists:

- **`disabled_tools`** — the model never even sees these tools' schemas.
  A disabled tool is invisible, not merely refused if called.
- **`confirm_tools`** — every call to one of these tools requires an
  explicit confirmation first, regardless of the workspace's safety mode
  (`relaxed` or `confirm` — see [safety-and-permissions.md](safety-and-permissions.md)).
  Useful for a tool whose risk isn't about the filesystem at all, e.g. a
  write to instrument hardware.

CLI: `aida mcp server disable-tool <server> <tool>` / `enable-tool` /
`confirm-tool` / `unconfirm-tool`. GUI: the Tools tab's two checkboxes per
tool, described above.

## Setting up web search

There is deliberately **no built-in `web_search` tool** in AIDA. Instead,
point a workspace's `mcp_group` at a community web-search MCP server —
Brave Search, Tavily, or similar, using your own account/API key — exactly
the same way you'd add and group any other MCP server described above. The
exact `--command`/`--arg` values depend on which search server you install
(check its own docs), but the shape is the same as any other server, e.g.:

```bash
aida mcp server add brave-search \
  --command npx \
  --arg -y --arg <the search server's package name> \
  --env BRAVE_API_KEY=your-key-here \
  --groups web-search
```

Then set `mcp_group: web-search` on the workspace(s) that should have it
(see [workspaces.md](workspaces.md)).

This is different from `fetch_url`, which **is** a small built-in tool (no
MCP server needed) that fetches a page's readable text — but it always
requires confirmation on every single call, with no per-workspace on/off
switch, since sending a URL out to the network is exactly the kind of
action [safety-and-permissions.md](safety-and-permissions.md) always asks
about.

## Full example

See [`examples/config/mcp.json`](../examples/config/mcp.json) in the repo
for a fully commented, complete `mcp.json` covering a pyIrena analysis
server and an instrument-control server, each in their own group.
