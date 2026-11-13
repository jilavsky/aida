# AGENTS.md — working on the AIDA code base

Instructions for coding agents (and new contributors) working in this repo.
Human-facing contribution rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md);
this file is the short map and the rules that are easy to break.

AIDA is a local scientific agent workbench: a chat agent with file, script,
MCP, knowledge-base and workflow tools, used at APS beamlines 12-ID/9-ID.
PyPI name `aida-workbench`, import package `aida`, CLI `aida`, GUI `aida-gui`.

## Where things are

| Need | Look in |
|---|---|
| What is open | `TODO.md`, then `PLAN.md` §1 for the reasoning |
| Why AIDA is shaped this way | `planning/DESIGN.md` (§3 layering, §5 safety, §8 dependencies) |
| What shipped, and why | `CHANGELOG.md`, `planning/COMPLETED.md` |
| User documentation | `docs/`: a Sphinx/MyST site on Read the Docs (`docs/index.md` is the table of contents; sections `getting-started/`, `guides/`, `reference/`; `conf.py` is the family template, edit only its PER-PACKAGE block) |
| Sample skills (bundled into the wheel) | `skills/` |
| Where the GUI's Documentation button goes | `src/aida/_docs.py` (`docs_url()`; no Qt) |

| Package (`src/aida/`) | Holds |
|---|---|
| `core/` | `session.py` (the composition root: `start_session`, `ChatSession`), `agent.py` (the tool loop), `context.py` (system prompt, skills), `workflows.py`, `scheduler_runtime.py` |
| `providers/` | OpenAI-compatible and Anthropic clients, embeddings, `mock.py` for tests |
| `mcp/` | `server.py` (one server handle), `manager.py` (all servers, tool namespacing), one-click setups |
| `workspace/` | `safety.py` (`SafetyGuard`: allowed, read-only and confirm rules), file and web tools |
| `coding/` | `run_python_script` / `run_command` tools and the subprocess runner |
| `knowledge/rag/` | SQLite chunk index and retrieval |
| `persistence/` | conversation database, migrations, transcript recorder |
| `config/` | `settings.py` (all config files), `paths.py` (`~/.aida` layout), secrets |
| `documents/`, `artifacts/`, `portability/` | attachments and OCR, tool outputs, export/import bundles |
| `cli/` | the `aida` command, `doctor` |
| `ui/qt/` | the PySide6 GUI; `bridge.py` runs the session on a background asyncio loop |

## Commands

```bash
pip install -e ".[dev,gui,docs,ocr,science]"     # or: conda env create -f environment.yml
ruff check . && ruff format --check .
pytest --ignore=tests/ui                          # non-GUI suite
QT_QPA_PLATFORM=offscreen pytest tests/ui         # GUI suite, run separately (see ci.yml)
pip install -r docs/requirements.txt && sphinx-build -W --keep-going -b html docs docs/_build/html
```

The `docs` extra is the document-reading libraries (PDF, Word, ...), not the
Sphinx toolchain; that lives only in `docs/requirements.txt`.

Run the two pytest invocations separately, as CI does: combined in one
process, Qt object churn has caused rare native crashes.

## Rules that are enforced or easy to break

- **Layering.** Nothing outside `aida.ui` imports Qt, and inside `aida.ui.qt`
  only `_qt.py` imports PySide6 (`tests/test_contract_layering.py`). Core,
  providers, MCP, workspace, knowledge and persistence stay Qt-free and
  matplotlib-free.
- **Dependencies.** Do not add one without asking. NumPy is an optional soft
  import in retrieval only; the `science` extra exists for agent scripts, not
  for AIDA code.
- **Saved formats.** Config files (`~/.aida/*.yaml`, `mcp.json`), the SQLite
  databases and exported bundles must stay backwards compatible. Schema
  changes go through a new numbered migration (`persistence/migrate.py`
  runs them transactionally). Every config dataclass loader supplies a
  default for every field.
- **Safety.** Tool calls go through `SafetyGuard`. Don't add a tool that
  touches files or runs processes without an `authorize_*` call. The skills
  folder is read-only for the agent.
- **Engine output.** The engine never prints; startup problems go into
  `StartupNotices` and each frontend shows them.
- **Comments state the current invariant**, not the history of how a bug was
  found. History belongs in git and `planning/COMPLETED.md`.
- **Every user-visible change** gets a bullet under `[Unreleased]` in
  `CHANGELOG.md`, and docs in `docs/` are updated in the same change.
- **Docs pages.** A new page is listed by hand in its section's `index.md`
  and the site builds with zero warnings (`-W`, as on Read the Docs). Only
  user-facing pages go in `docs/`; talk slides and planning notes go in
  `planning/`. Links from `CHANGELOG.md` or `README.md` into `docs/` are
  absolute Read the Docs `latest` URLs, because those files are read both
  on GitHub and (the CHANGELOG) inside the site.

## Deliberate deviations from the author's other Python packages

- Build backend is `hatchling` with a `src/` layout; `requires-python` is
  `>=3.11`. Both predate the house standard and are kept because the wheel
  bundles `skills/` via `force-include` and CI covers 3.11.
- Ruff also selects `SIM`, and does not ignore `E741`.
- Variable names such as `I`, `Q`, `l` are physics notation where they
  appear; don't rename them.
