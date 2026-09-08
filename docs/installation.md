# Installation

> **Status: beta (0.1.0b6).** Phases 1–10 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [providers-and-secrets.md](providers-and-secrets.md) (next step after install) · [gui-overview.md](gui-overview.md)

## Install from PyPI

```bash
pip install --pre "aida-workbench[gui,docs]"
aida doctor
aida-gui
```

`--pre` is required: every release so far is a `0.1.0bN` pre-release, and
pip never installs a pre-release over a stable one unless asked — omit it
and you silently get the long-abandoned `0.0.1` snapshot instead of the
current beta, with no error to say so.

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
- The `gui` extra: PySide6 not installed is reported OK (a CLI-only install
  is a normal, deliberate choice), but PySide6 *installed and still failing
  to import* is flagged — see
  [below](#gui-fails-to-import-on-headless-linux) if you hit that
- `~/.aida/`, `~/.aida/logs/`, `~/.aida/artifacts/`, and the configured
  records folder are all writable
- Every configured provider profile is actually reachable (a real
  network/endpoint check, not just "is it configured")

A clean `aida doctor` run means the app is ready to configure further —
it doesn't yet mean you have a working chat session, since that also
needs at least one provider profile (next: `providers-and-secrets.md`).

### GUI fails to import on headless Linux

`aida-gui` (and `aida doctor`'s `gui` check) says PySide6 isn't installed,
but `pip install "aida-workbench[gui]"` reports nothing wrong. PySide6's
wheel installed fine — it's failing to *import*, for one of two different
reasons that look identical (both are an `ImportError`) but need opposite
fixes. `aida doctor` and `aida-gui` both name the real underlying error so
you can tell which one you have; read the error message rather than
guessing.

**A missing system Qt library** (the error names a `.so` file, e.g.
`libGL.so.1: cannot open shared object file`) — a minimal or headless
server (a beamline control machine with no desktop environment installed,
a container, a CI runner) typically lacks the shared libraries Qt loads at
runtime even though it never draws to a real display. On Debian/Ubuntu,
this usually fixes it:

```bash
sudo apt-get install libgl1 libegl1 libxkbcommon0 libxcb-cursor0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
    libxcb-render-util0 libxcb-shape0 libdbus-1-3
```

Package names differ on other distros (`mesa-libGL`, `libxkbcommon` on
Fedora/RHEL, etc.). To see exactly which shared library is missing rather
than guessing from the list above:

```bash
python -c "from PySide6.QtWidgets import QApplication"
```

**A glibc too old for the installed PySide6 wheel** (the error is
`` `libc.so.6: version `GLIBC_X.YZ' not found` ``) — newer PySide6 releases
are built against a newer glibc baseline than older, long-lived Linux
installs provide (RHEL8-class control machines, common at APS beamlines,
ship glibc 2.28; some recent PySide6 releases now need 2.32+). No system
package can supply a missing glibc symbol — installing `libgl1` etc. does
nothing here. Check your system's glibc with `ldd --version`, then pin
PySide6 down to an older release built against a compatible baseline:

```bash
pip install "PySide6==<a version you know works>"
```

If pyIrena's GUI already runs on this machine, `pip show PySide6` in
*its* environment names a version already confirmed to work here — install
that same version into AIDA's environment. Otherwise, step back one
PySide6 release at a time (`pip index versions PySide6` lists what's
available) until one imports.

### Storing a secret fails with `KeyringLocked` on headless Linux

Saving a provider profile's secret — from the GUI's Providers dialog, or
`aida config secret set` — reports it couldn't be stored, naming the OS
keychain error rather than crashing. On a bare console/SSH login to a
Linux control machine (no desktop session), the `keyring` package's Secret
Service backend needs a running, *unlocked* keyring daemon (gnome-keyring
or kwallet) behind a D-Bus session — and there is usually no daemon running
at all, since nothing ever logged into a desktop to start one. No system
package fixes this: unlocking is normally done by a desktop login prompt,
which doesn't exist here.

The supported workaround, already the documented order of precedence in
[providers-and-secrets.md](providers-and-secrets.md#secrets), is to skip
the keychain entirely and set the secret as an environment variable instead
— it's checked before the keychain on every lookup:

```bash
export AIDA_SECRET_ARGO_CLAUDE=<your-ANL-username-or-API-key>
```

(the name is the profile's `secret_ref`, upper-cased with `-` → `_`,
prefixed `AIDA_SECRET_`). Put the `export` in the shell profile that starts
`aida-gui`/`aida` on this machine so it's set on every login. A profile
saved through the GUI while this error appears is still saved — only the
secret itself didn't make it into the keychain, so the environment variable
picks up exactly where it left off.

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
