# AIDA — AI Data Assistant

> **Status: 1.0.0.** In daily use at APS beamlines 12-ID/9-ID. All ten
> phases of the original plan are delivered, the test suite is 2,449 tests
> green on macOS, Windows and Linux, and the config formats and CLI
> commands are stable: a breaking change to either now means a new major
> version. Every change is dated in [`CHANGELOG.md`](CHANGELOG.md).
> Bug reports and rough edges are very much welcome —
> [open an issue](https://github.com/jilavsky/aida/issues).

AIDA is a local scientific agent workbench: a simple, reliable desktop GUI
(and CLI) for using AI agents in scientific work — conversation with local or
cloud LLMs, correct use of domain MCP servers (pyIrena, bait_mcp, ...),
correct display of rich tool results (especially PNG plots), reading and
producing documents, and controlled access to your own data folders.

It is built for pyIrena and USAXS-instrument users and is deliberately **not**
a general-purpose AI platform — see [`planning/DESIGN.md`](planning/DESIGN.md) §1
for the rationale.

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
- **Keep the documents you attach** — an attached paper is copied into the
  conversation's own folder with the text that was extracted from it, and
  deleted again with the conversation. Ask about a figure and the agent
  fetches that one by label rather than being handed every image in the
  file. Optional [Mistral OCR](docs/documents.md#optional-mistral-ocr) makes
  those labels reliable on multi-column journal PDFs — off by default, per
  workspace, and it asks before a document leaves your machine.
- **Keep conversations sorted** — label them by person on a shared beamline
  machine, or by project on a laptop, and filter the list by it. Labels are
  organization, not security.
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
`docs` (PDF/DOCX/XLSX/PPTX reading, image handling), `ocr` (the optional
Mistral OCR backend for figure extraction).

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

`environment.yml` already includes the `gui`, `docs` and `ocr` extras. If
`aida doctor` or `aida-gui` says PySide6 isn't installed on a headless
Linux machine even though the install above reported no errors, see
[GUI fails to import on headless Linux](docs/installation.md#gui-fails-to-import-on-headless-linux)
— it's usually a missing system Qt library, not a missing Python package.

## First run

1. `aida doctor` — confirms Python, config files, keychain, and folders.
2. Launch `aida-gui`. On a fresh install it offers to set up a provider
   profile and a first workspace; you can also do both from the **Configure**
   menu (**Providers…**, **Workspaces…**) or from the CLI
   (`aida workspace new`).
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

All ten phases are implemented: config and diagnostics, the provider layer
and agent loop, MCP with typed artifacts, persistence and workspaces, the
PySide6 GUI, documents and the safety model, MCP management UI, RAG,
coding/scripting, headless automation (`aida run`, stored workflows, the
in-app scheduler — see [`docs/workflows.md`](docs/workflows.md)), and
moving a setup between machines
([`docs/moving-and-sharing.md`](docs/moving-and-sharing.md)).

What is still open — manual acceptance checks that need a real beamline, a
conda-forge decision, full backup/restore — is in [`TODO.md`](TODO.md).
[`PLAN.md`](PLAN.md) holds the reasoning behind each item, and its §2 the
things considered and deliberately not committed. What has been delivered,
and why, is in [`planning/COMPLETED.md`](planning/COMPLETED.md); the
per-phase checklists are archived in
[`planning/completed/`](planning/completed/README.md).

Released and unreleased changes, version by version, are in
[`CHANGELOG.md`](CHANGELOG.md).

## Requirements

Python >= 3.11. Tested on macOS, Windows, and Linux.

## License

MIT — see [`LICENSE`](LICENSE).
