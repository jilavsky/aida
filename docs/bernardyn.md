# Bernardyn and AIDA

> **Status: 1.0.0.** In daily use at APS beamlines 12-ID/9-ID. Config
> formats and CLI commands are stable: from here, a breaking change to
> either means a new major version, and every change is dated in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`TODO.md`](../TODO.md) for what
> is still open.

**Related:** [pyirena.md](pyirena.md) · [mcp-servers.md](mcp-servers.md) · [workspaces.md](workspaces.md)

[Bernardyn](https://github.com/jilavsky/Bernardyn) is the publication-figure
workbench for the same scattering data pyIrena fits. Its MCP server
(`bernardyn-mcp`) exposes a deliberately small **five-tool** surface:

| Group | Tool | Use |
|---|---|---|
| Discovery | `bernardyn_inspect_data` | What's in one data file: scattering selections, saved results, warnings |
| Discovery | `bernardyn_list_plot_recipes` | The stable recipe IDs and what each is for |
| Create | `bernardyn_create_plot` | One portable plot package plus a PNG/JPEG/SVG preview |
| Package | `bernardyn_describe_package` | Graphs, curve semantics and recipe identity, without returning arrays |
| Package | `bernardyn_export_plot` | Re-render one stored graph to another format |

AIDA does **not** import Bernardyn. It launches `bernardyn-mcp` as a
subprocess and speaks MCP over stdio — which matters more here than for any
other integration, because Bernardyn pins PySide6 differently from AIDA and
two Qt builds must never share one process. Its own conda environment is the
recommended layout.

---

## Setting it up — the short version

```bash
pip install "bernardyn[mcp]"     # if you don't have it already
aida mcp add-bernardyn           # finds it and configures AIDA in one step
aida mcp test bernardyn          # confirms it starts and lists its five tools
```

In the GUI, the same thing is the **MCP Servers…** dialog's **Add
Bernardyn…** button. On the first-run onboarding screen there is a single
button that adds whatever it detected — *Add pyIrena + Bernardyn MCP
Tools…* when both are installed — still confirming each one separately
before anything is written.

If Bernardyn lives somewhere detection doesn't look, point at it directly:

```bash
aida mcp find-bernardyn    # report what would be found, change nothing
aida mcp add-bernardyn --command /path/to/envs/bernardyn/bin/bernardyn-mcp
```

Detection order is the same as pyIrena's (AIDA's own environment, `PATH`,
`python -m bernardyn.mcp.server`, then sibling conda/mamba envs) — see
[pyirena.md](pyirena.md#where-it-looks).

## What `add-bernardyn` actually configures

| Setting | Value | Why |
|---|---|---|
| `command` | The **absolute path** to `bernardyn-mcp` | A GUI app on macOS or Windows inherits no shell `PATH`, so a bare `bernardyn-mcp` fails to launch with a confusing error. |
| `groups` | `pyirena-analysis` **and** `bernardyn-plots` | Five tools is roughly 700 tokens of schema against pyirena-mcp's ~10,200, so adding them to the group an analysis workspace already enables costs almost nothing and saves a second setup step. `bernardyn-plots` is there for a figure-only workspace that wants plotting without pyIrena's full tool list in context. Override with `--groups`. |
| `env.PATH` | The server's own environment prefix, first | A cross-environment stdio server otherwise can't find its own shared libraries. On Windows that kills CPython at import with exit 127 and *no* traceback — the client sees only "Connection closed". |
| `BERNARDYN_OUTPUT_ROOT` | Only if you pass `--output-root DIR` | Where plot packages and preview images land. Without it, Bernardyn writes to a temporary `bernardyn-mcp` cache — fine for a one-off, easy to lose track of. The GUI button suggests the active workspace's **target folder**. |
| `BERNARDYN_DATA_ROOT` | Only if you pass `--data-root DIR` | Restricts every file the server may *read* to that subtree (plus the output root, so a package it just made can be inspected again). The GUI button suggests the active workspace's first **source folder**. |

No skills are attached: Bernardyn ships none, and the server's own MCP
instructions already tell the model the intended order (inspect → list
recipes → create).

Nothing is written until you say so — `add-bernardyn` is an explicit
command, and the GUI button shows exactly what it found and asks first. An
MCP server is code AIDA launches on your machine.

## Enabling it in a workspace

A configured server does nothing until a workspace enables its group:

```bash
aida workspace edit my-analysis --mcp-group pyirena-analysis   # pyIrena + Bernardyn
aida workspace edit my-figures  --mcp-group bernardyn-plots    # plotting only
```

## What good looks like

```
[mcp] bernardyn: 5 tool(s)
```

Then ask for something concrete:

> Inspect the newest `.h5` in my source folder, then make a unified-fit
> residuals plot from it.

The preview image comes back inline; the portable package stays on disk
under the output root for Bernardyn's desktop app to reopen.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `aida mcp test bernardyn` says "Connection closed" with no detail | The server's environment isn't on its `PATH` (typical on Windows, conda) | `aida mcp add-bernardyn --force` rewrites the `env.PATH` snapshot |
| `No module named 'mcp'` | Bernardyn installed without its MCP extra | `pip install "bernardyn[mcp]"` in *that* environment |
| Server configured, but the model has no plotting tools | The workspace's `mcp_group` doesn't include it | `aida workspace edit W --mcp-group pyirena-analysis` |
| Created plots are nowhere obvious | No `BERNARDYN_OUTPUT_ROOT`, so they went to a temp cache | `aida mcp add-bernardyn --output-root DIR --force` |
| `PathSecurityError` on a file that exists | It's outside `BERNARDYN_DATA_ROOT` | Widen it, or drop it from the server's `env` while debugging |
| A different Bernardyn than you expected | Several installs; detection picked the first | `aida mcp find-bernardyn`, then `add-bernardyn --command PATH --force` |
