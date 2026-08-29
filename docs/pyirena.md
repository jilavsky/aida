# pyIrena and AIDA

> **Status: beta (0.1.0b2).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [installation.md](installation.md) · [mcp-servers.md](mcp-servers.md) · [workspaces.md](workspaces.md) · [context-and-limits.md](context-and-limits.md)

[pyIrena](https://github.com/jilavsky/pyirena) is the small-angle scattering
analysis package AIDA was built to drive. It ships an MCP server
(`pyirena-mcp`) exposing ~70 tools: discovery and reading of NXcanSAS HDF5
results, parameter aggregation across a folder, headless plotting, and
interactive fitting sessions for Unified Fit, Size Distribution, Simple
Fits, Modeling, and WAXS Peak Fit.

AIDA does **not** import pyIrena. It launches `pyirena-mcp` as a subprocess
and speaks MCP over stdio. That is why the two can live in one environment
*or* in separate ones, whichever suits your machine.

---

## Setting it up — the short version

```bash
pip install "pyirena[mcp]"      # if you don't have it already
aida mcp add-pyirena            # finds it and configures AIDA in one step
aida mcp test pyirena           # confirms it starts and lists its tools
```

Then point a workspace at it:

```bash
aida workspace edit my-analysis --mcp-group pyirena-analysis
```

In the GUI, the same thing is the **MCP Servers…** dialog's **Add pyIrena…**
button (also offered on the first-run onboarding screen when pyIrena is
detected). `aida doctor` reports the state of both halves — installed or
not, configured or not — and names the command that fixes whichever is
missing.

## What `add-pyirena` actually configures

| Setting | Value | Why |
|---|---|---|
| `command` | The **absolute path** to `pyirena-mcp` | A GUI app on macOS or Windows inherits no shell `PATH`, so a bare `pyirena-mcp` fails to launch with a confusing error. This is the single most common MCP misconfiguration. |
| `groups` | `pyirena-analysis` | pyirena-mcp exposes ~70 tools — measured at ~10,200 tokens of schema JSON sent on *every* request (see [context-and-limits.md](context-and-limits.md)). A group means they're enabled per workspace instead of drowning a small local model in schemas on every conversation. |
| `skills` | `saxs-basics`, `pyirena-usage` | Auto-included whenever the server is enabled. Both are installed into `~/.aida/skills/` at the same time, if they aren't there already — an existing file of yours is never overwritten. |
| `PYIRENA_MAX_ARRAY_POINTS` | `500` | pyIrena's own default, written out explicitly because it's the one knob controlling how much context a single tool result can consume. |
| `PYIRENA_DATA_ROOT` | Only if you pass `--data-root DIR` | Restricts every file pyirena-mcp will touch to that subtree. pyIrena's docs call it strongly recommended when the server is exposed to an AI agent; your workspace's source folder is usually the right value. The GUI button suggests it automatically from the active workspace. |

Nothing is written until you say so: `add-pyirena` is an explicit command,
and the GUI button shows exactly what it found and asks before saving. An
MCP server is code AIDA launches on your machine.

### Where it looks

In order, best first:

1. **AIDA's own environment** — the `pip install aida-workbench pyirena[mcp]`
   into one env case.
2. **`PATH`** — the active conda env when AIDA was started from a terminal.
3. **`python -m pyirena.mcp.server`** with AIDA's interpreter, as a fallback
   when `pyirena` imports but the console script isn't on disk (an editable
   install whose entry points were never linked).
4. **Sibling conda/mamba environments** — `~/miniconda3/envs/*`,
   `~/anaconda3/envs/*`, `~/miniforge3/envs/*`, `~/mambaforge/envs/*`,
   `~/.conda/envs/*`, the Homebrew miniconda location, and whatever
   `CONDA_PREFIX` is next to.

`aida mcp find-pyirena` prints what it would find, changing nothing. If your
install is somewhere else, skip detection:

```bash
aida mcp add-pyirena --command /path/to/envs/pyirena/bin/pyirena-mcp
```

---

## One environment or two?

**Both work.** Pick on other grounds:

- **One shared environment** — simplest, and what most people should do on a
  laptop. `pip install "aida-workbench[gui,docs]" "pyirena[all]"` in either
  order.
- **Two environments** — better when pyIrena's heavier stack (VTK/PyVista
  for the 3D viewer, Dans-Diffraction, xraydb) is something you'd rather
  keep away from AIDA, or when pyIrena is already installed somewhere you
  don't want to touch. AIDA reaches it by absolute path over stdio, and
  `add-pyirena` finds sibling conda envs automatically.

### Shared-environment compatibility

The two packages have been verified to install together, in **either
order**, with `pip check` clean:

| Package | pyIrena requires | AIDA requires | Result |
|---|---|---|---|
| Python | `>=3.10` | `>=3.11` | **Use 3.11+.** A 3.10 environment can hold pyIrena but not AIDA; pip refuses the AIDA install with a clear message rather than breaking anything. |
| PySide6 | `>=6.4,!=6.7.*,!=6.10.*` | `>=6.6,!=6.7.*,!=6.10.*` | AIDA deliberately mirrors pyIrena's exclusions so no resolution order can land on a release pyIrena has ruled out. |
| `mcp` | `>=1.0,<2.0` | `>=1.28,<2.0` | Both exclude mcp 2.x, which removed the `mcp.server.fastmcp` API pyirena-mcp is built on. |
| `anthropic` / `openai` / `keyring` | pyIrena's `gui` extra | AIDA core | Compatible ranges; whichever is installed second leaves the other's version in place. |
| numpy, scipy, h5py, matplotlib | pyIrena | not used by AIDA | No interaction. |

**One caveat, on older pyIrena releases.** pyIrena 1.0.1 does not cap `mcp`,
so `pip install "pyirena[mcp]"` on its own pulls mcp 2.x and `pyirena-mcp`
then fails at import with a `mcp.server.fastmcp` error. Installing AIDA into
the same environment *fixes* this as a side effect — AIDA's `mcp<2.0`
requirement pulls mcp back to a 1.x release — and pyIrena 1.1.0 onward caps
it itself. If you hit that error with pyIrena alone:

```bash
pip install "mcp<2"
```

### If pip warns about a conflict

Installing the two in separate commands means pip resolves each one without
looking at the other's requirements; it warns afterward rather than
preventing the install. Re-resolving both together fixes it:

```bash
pip install "aida-workbench[gui,docs]" "pyirena[all]"
```

If that can't be satisfied, use two environments — nothing about AIDA's
design requires a shared one.

---

## What good looks like

Once configured and enabled in a workspace, a session starts with something
like:

```
[mcp] pyirena: 68 tool(s)
```

and pyIrena's own usage instructions (shipped in its MCP `initialize`
handshake) are folded into the model's system context automatically — you
don't have to describe its workflow yourself.

Ask for something concrete to check it end to end:

> Summarize what's in my source folder, then plot I(Q) for the three most
> recent scans.

The plot comes back as an inline image, not a wall of text — if it doesn't,
see [mcp-servers.md](mcp-servers.md) for the tool-call log and raw result
inspector.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `aida doctor` says "installed but NOT configured" | `mcp.json` has no pyirena entry | `aida mcp add-pyirena` |
| Server configured but the model has no pyIrena tools | The workspace's `mcp_group` doesn't include it | `aida workspace edit W --mcp-group pyirena-analysis` |
| `command not found` when starting the server | A bare `pyirena-mcp` in `mcp.json` instead of an absolute path | `aida mcp add-pyirena --force` rewrites it with the absolute path |
| `No module named 'mcp.server.fastmcp'` | mcp 2.x with a pyIrena release that doesn't cap it | `pip install "mcp<2"` |
| Tool calls fail with `PathSecurityError` | The file is outside `PYIRENA_DATA_ROOT` | Widen it, or drop it from the server's env while debugging |
| A different pyIrena than you expected | Several installs; detection picked the first | `aida mcp find-pyirena` to see them all, then `add-pyirena --command PATH --force` |
