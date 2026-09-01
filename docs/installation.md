# Installation

> **Status: beta (0.1.0b3).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [providers-and-secrets.md](providers-and-secrets.md) (next step after install) · [gui-overview.md](gui-overview.md)

## Install from PyPI

```bash
pip install "aida-workbench[gui,docs]"
aida doctor
aida-gui
```

The **PyPI distribution name is `aida-workbench`**, not `aida` — PyPI's
automated name-confusion protection blocked the bare name (see PLAN.md §2).
The import package and the console scripts are unchanged: `import aida`,
`aida`, `aida-gui`.

Extras:

| Extra | Brings | Needed for |
|---|---|---|
| `gui` | PySide6 | the desktop app (`aida-gui`) — the CLI works without it |
| `docs` | pymupdf, python-docx, openpyxl, python-pptx, Pillow | reading PDF/DOCX/XLSX/PPTX, writing DOCX, downscaling images for vision |

Nothing else is optional: the LLM SDKs, the MCP client, YAML, and keyring are
core dependencies.

Python **>= 3.11** is required.

## Install from a git checkout (development)

```bash
git clone https://github.com/jilavsky/aida.git
cd aida
conda env create -f environment.yml   # or: pip install -e ".[dev,gui,docs]"
conda activate aida
aida doctor
```

`environment.yml` already includes the `gui` and `docs` extras.

## First run

Once `aida doctor` is clean, you still need one provider profile and
(usually) one workspace before a session will start:

- **GUI:** launch `aida-gui`. On a fresh install it offers to set up a
  provider profile and a first workspace; you can reopen either later from
  the toolbar's **Providers…** and **Workspaces…** buttons.
- **CLI:** see [providers-and-secrets.md](providers-and-secrets.md) for the
  `providers.yaml` format and `aida config secret set` for API keys, then
  `aida workspace new` for a workspace.

The GUI reopens the workspace and profile you last used, so this is a
one-time setup.

If you use pyIrena, add its MCP tools at the same time — one command, or one
button on the onboarding screen:

```bash
pip install "pyirena[mcp]"    # if you don't have it already
aida mcp add-pyirena
```

`aida doctor` reports whether pyIrena is installed and whether AIDA is
configured to use it. See [pyirena.md](pyirena.md), which also covers
installing both packages into one environment.

## `aida doctor`

Run this after install, and any time something seems misconfigured. It
checks, and reports pass/fail for each:

- Python version (`>= 3.11`)
- `~/.aida/config.yaml`, `providers.yaml`, `workspaces.yaml`, `mcp.json`
  parse and load correctly
- The OS keychain backend is available (needed for provider secrets —
  see [providers-and-secrets.md](providers-and-secrets.md))
- `~/.aida/`, `~/.aida/logs/`, `~/.aida/artifacts/`, and the configured
  records folder are all writable
- Every configured provider profile is actually reachable (a real
  network/endpoint check, not just "is it configured")

A clean `aida doctor` run means the app is ready to configure further —
it doesn't yet mean you have a working chat session, since that also
needs at least one provider profile (next: `providers-and-secrets.md`).

## Where AIDA keeps its files

```text
~/.aida/                  # app state — config, DB, artifacts, logs
├── config.yaml           # general app settings (see the other docs files)
├── providers.yaml        # LLM + embedding provider profiles (no secrets)
├── workspaces.yaml       # named workspace bundles
├── mcp.json              # MCP server definitions
├── knowledge.yaml        # RAG knowledge base configs
├── skills/               # your own skills markdown files
├── artifacts/            # binary tool outputs (PNGs, etc.) — always
│                         #   writable by the agent, no confirmation needed
├── logs/                 # rotating log files
└── aida.db               # SQLite: conversations, messages, artifact metadata

~/Documents/Aida/          # human-readable conversation records / exports
                           # (configurable — config.yaml's records_dir)
```

Everything under `~/.aida/` is created with safe defaults the first time
you run `aida` — you don't need to hand-create any of these files.
`~/.aida/config.yaml`, `providers.yaml`, `workspaces.yaml`, and `mcp.json`
are the ones you'll edit (by hand, via CLI, or via the GUI); the rest
(`aida.db`, `artifacts/`, `logs/`) are managed by AIDA itself.

Secrets (API keys, the ANL Argo username) never live in any of these
files — see [providers-and-secrets.md](providers-and-secrets.md).

### Overriding the location

Set the `AIDA_HOME` environment variable to point `~/.aida/` somewhere
else (used by AIDA's own test suite so tests never touch a real install;
also useful if you want a second, isolated AIDA config for testing):

```bash
export AIDA_HOME=/path/to/alternate-aida-home
aida doctor
```

## Fully-commented example configs

[`examples/config/`](../examples/config/) in the repo has illustrative,
fully-commented versions of `config.yaml`, `providers.yaml`,
`workspaces.yaml`, and `mcp.json` — **not** auto-copied anywhere, just
reference material the other docs in this folder link to instead of
repeating every field inline.
