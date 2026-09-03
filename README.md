# AIDA — AI Data Assistant

> **Status: beta (0.1.0b3).** Everything described below works today and is
> in daily use. Config formats and CLI commands are stable enough to build
> on; breaking changes before 1.0 will be called out in
> [`CHANGELOG.md`](CHANGELOG.md). Bug reports and rough edges are exactly
> what this beta is for — [open an issue](https://github.com/jilavsky/aida/issues).

AIDA is a local scientific agent workbench: a simple, reliable desktop GUI
(and CLI) for using AI agents in scientific work — conversation with local or
cloud LLMs, correct use of domain MCP servers (pyIrena, bait_mcp, ...),
correct display of rich tool results (especially PNG plots), reading and
producing documents, and controlled access to your own data folders.

It is built for pyIrena and USAXS-instrument users and is deliberately **not**
a general-purpose AI platform — see [`PLAN.md`](PLAN.md) §1 for the rationale.

## What it does

- **Chat with any model you can reach** — Ollama / LM Studio / any
  OpenAI-compatible endpoint, OpenAI itself, Claude direct, or Claude through
  the ANL Argo proxy. Named profiles, switchable mid-conversation.
- **Use MCP servers properly** — a PNG a tool returns is decoded and shown as
  an image, not flattened to text. Servers are grouped so a small local model
  isn't drowned in 100+ tool schemas.
- **Work in your folders** — named workspaces bundle source folders, a target
  folder, a provider profile, an MCP group, and skills. The agent reads,
  writes, searches, and (optionally) runs scripts there under a safety model
  you configure.
- **Produce documents** — Markdown in Obsidian layout (images in a sidecar
  folder, linked relatively) or DOCX, written into your target folder.
- **Search your own documentation** — optional local RAG over folders you
  choose, with local or cloud embeddings.
- **Stay out of black boxes** — token counts and cost estimates per session,
  a tool-call log, a raw MCP result inspector, and `aida doctor`.

## Install

From PyPI (recommended):

```bash
pip install "aida-workbench[gui,docs]"
aida doctor
aida-gui
```

The PyPI distribution name is `aida-workbench`; the import package and the
console scripts are `aida` / `aida-gui`. Extras: `gui` (PySide6 desktop app),
`docs` (PDF/DOCX/XLSX/PPTX reading, image handling).

Already using pyIrena? Install both — in either order, in one environment or
two — and wire up its MCP tools with a single command:

```bash
pip install "aida-workbench[gui,docs]" "pyirena[all]"
aida mcp add-pyirena
```

See [`docs/pyirena.md`](docs/pyirena.md) for the compatibility details and
the GUI equivalent.

From a git checkout, for development:

```bash
git clone https://github.com/jilavsky/aida.git
cd aida
conda env create -f environment.yml   # or: pip install -e ".[dev,gui,docs]"
conda activate aida
aida doctor
```

## First run

1. `aida doctor` — confirms Python, config files, keychain, and folders.
2. Launch `aida-gui`. On a fresh install it offers to set up a provider
   profile and a first workspace; you can also do both from the toolbar
   (**Providers…**, **Workspaces…**) or from the CLI (`aida workspace new`).
3. Pick a workspace and profile in the toolbar and start typing.

[`docs/installation.md`](docs/installation.md) →
[`docs/providers-and-secrets.md`](docs/providers-and-secrets.md) →
[`docs/workspaces.md`](docs/workspaces.md) is the full path from nothing to a
working session.

## Documentation

Task-oriented setup and configuration guides — providers, workspaces, the
safety model, MCP servers, scripting, RAG, a GUI tour — live in
[`docs/`](docs/README.md). Fully-commented example config files are in
[`examples/config/`](examples/config/).

## Status and roadmap

Phases 1–10 of [`PLAN.md`](PLAN.md) are implemented: config and diagnostics,
the provider layer and agent loop, MCP with typed artifacts, persistence and
workspaces, the PySide6 GUI, documents and the safety model, MCP management
UI, RAG, coding/scripting, and headless automation — `aida run`, stored
workflows, and the in-app scheduler (see [`docs/workflows.md`](docs/workflows.md)).
Distribution (real PyPI/conda release automation) is what remains open.
Completed per-phase checklists live in [`planning/`](planning/); what is
still open is at the top of `PLAN.md`.

Released and unreleased changes, version by version, are in
[`CHANGELOG.md`](CHANGELOG.md).

## Requirements

Python >= 3.11. Tested on macOS, Windows, and Linux.

## License

MIT — see [`LICENSE`](LICENSE).
