# AIDA — AI Data Assistant

**A local scientific agent workbench.**

Master implementation plan. This document supersedes and incorporates
`local_scientific_ai_agent_proposal.md` (the prior planning session); where the two
differ, this document wins. Section 2 lists every deliberate change from the prior
proposal so nothing is lost silently.

Progress is tracked in per-phase checklist files under `planning/` — one file per
phase, each independently testable. See Section 10 for the phase map.

- Repo: `https://github.com/jilavsky/Aida`
- Package / import name: `aida` (PyPI name appeared unclaimed as of 2026-08-18 —
  **claim it early**, see Phase 1)
- License: MIT
- Language: Python (>= 3.11)
- Desktop GUI: PySide6 (Qt 6), following pyIrena conventions
- Distribution: git + pip now, PyPI later, conda-forge possibly after that

---

## 1. Objective

AIDA gives pyIrena and USAXS-instrument users a **simple, reliable GUI for using AI
agents in scientific work**: conversation with local or cloud LLMs, correct use of
domain MCP servers (pyIrena, bait_mcp, ...), correct display of rich tool results
(especially PNG plots), reading and producing documents, and controlled access to
the user's data folders.

It is deliberately **not** a general-purpose AI platform. Existing tools
(AnythingLLM, Witsy, Msty, Unsloth Desktop, ...) carry far more functionality than
needed and still fail at the details that matter here — above all, handling MCP
image results and working smoothly with our own MCP servers. AIDA implements a
smaller feature set and implements it well, under our control.

The one-sentence interaction model:

> Here is my workspace — these folders, these tools, this model. Help me do the
> work, show me what you produce, and save useful outputs back into my folders.

### Driving use cases (from simple to complex)

These are the acceptance scenarios the phases build toward. Each phase file names
which of these it advances.

- **UC1 — Ask with knowledge.** Talk to the agent; answers draw on selected skills
  files and (later) RAG over documentation. BeamlineAdvisor-style Q&A.
- **UC2 — Work on documents.** Give the agent one or more documents (PDF, MD,
  DOCX...), ask questions, request edits or a new derived document written to the
  user's target folder. Later: drag & drop onto the GUI.
- **UC3 — MCP analysis with rich output.** Enable pyIrena MCP (+ its skills), ask
  e.g. *"Find which data in folder X have Rg between 20 and 50 Å and plot them"* —
  the plot displays inline and/or lands in an output document with figures.
- **UC4 — Filtered analysis pipelines.** Use pyIrena MCP to find samples matching
  conditions, then graph or run further analysis on the matches.
- **UC5 — Instrument check/operate.** Run AIEvaluator scripts to check beamline
  status, drive the instrument through bait_mcp, read results back through
  pyIrena MCP.
- **UC6 — Generated reports on demand or on schedule.** Agent produces a report via
  MCP; optionally run by a simple scheduler or an external trigger. (Future.)

---

## 2. Decisions, and changes from the prior proposal

The prior proposal's core ideas are **kept**: GUI-independent agent core, typed MCP
results (never flatten images to text), event-based core↔GUI boundary, MCP as the
extension mechanism, native workspace file tools, SQLite persistence, diagnostics
as a feature, standard-style `mcp.json`.

Deliberate changes and additions:

| # | Topic | Prior proposal | This plan |
|---|-------|----------------|-----------|
| 1 | Providers | Local-first; Unsloth Desktop + Muse-Glimmer as the initial and near-exclusive target | **Multi-provider from day one.** OpenAI-compatible endpoints (Ollama, LM Studio, Unsloth Desktop, OpenAI) via the `openai` SDK **and** Anthropic via the `anthropic` SDK, including custom `base_url` (ANL Argo proxy — the exact pattern BeamlineAdvisor already uses). Named provider profiles, quick switching from GUI. No Unsloth-specific assumptions anywhere. |
| 2 | GUI | PySide6 recommended, NiceGUI optional prototype | **PySide6 from the start; no web prototype.** Phases before the GUI are headless (CLI harness). NiceGUI/web demoted to a possible future alternative frontend riding on the same event API. |
| 3 | RAG | Deferred until direct file access proves insufficient | **Mid-priority phase (Phase 8).** UC1 is served earlier by skills files loaded directly into context (BeamlineAdvisor's "direct context" strategy); RAG arrives as its own phase for larger corpora, with local *or* cloud embedding providers configured like LLM providers. |
| 4 | Workspaces | Single workspace root per conversation | **Named workspaces** — a workspace bundles provider profile, source folder(s), target folder, MCP group, skills, and system prompt. Users pick "use pyIrena" or "perform reviews" and get the whole environment. |
| 5 | MCP | Flat server list | **MCP groups** (named sets of servers, switchable together — pyIrena's large tool list overloads small local models when not needed) and **skills linked to MCP servers**: enabling a server auto-includes its associated skills files in context. |
| 6 | Safety | Confirm every destructive action | **Allowed-folders model with relaxed mode.** Folders are declared allowed; within them the agent may read/write/modify without per-action approval (folders are backed up — user's explicit choice). Clear warning at setup, confirmations reserved for actions *outside* allowed folders, non-allowlisted shell commands, and sending data out. |
| 7 | Outputs | Generic file artifacts | **Markdown-in-Obsidian-structure is the default output format**: MD files with images attached by link, stored in a user-nameable sidecar folder. Target folder is user-defined and easily changed in the GUI. |
| 8 | CLI | "CLI possible" someday | **CLI is first-class and comes before the GUI** (it is how phases 2–4 are tested), and later grows `aida run` for scripted/pipeline use and named stored workflows. |
| 9 | Coding | Not covered | **Small-scale coding support** (BeamlineAdvisor parity): template-based generation of instrument functions, code editor widget, saving scripts, executing Python (AIEvaluator) with subprocess + timeout. Large programming stays in VS Code. |
| 10 | Persistence location | `~/.local-scientific-agent/` | **`~/.aida/`** for app state/config/DB; human-readable conversation records under `~/Documents/Aida/` (configurable); conversation browsing + cleanup built in. |
| 11 | Name | "scientific-agent" placeholder | **AIDA — AI Data Assistant**; repo `jilavsky/Aida`, package `aida`. |
| 12 | Agent engine | Own loop (implied) | **Confirmed: own thin loop, no agent framework.** The `openai` + `anthropic` SDKs are the only LLM dependencies. Frameworks (LangChain, pydantic-ai...) would reintroduce exactly the opaque-abstraction problem AIDA exists to escape. |

Deferred/parked (see `planning/phase_future.md`): voice STT/TTS, per-user beamline
credentials, external-trigger report generation, remote (HTTP) MCP servers,
alternative web frontend.

---

## 3. Architecture

Unchanged in spirit from the prior proposal; extended with providers, workspaces,
skills, and groups.

```text
        ┌────────────────────────────────────────────┐
        │                 Frontends                  │
        │  PySide6 desktop app   │   CLI (aida ...)  │
        │  (thin, replaceable)   │   (scriptable)    │
        └──────────────┬─────────────────┬───────────┘
                       │   event stream  │
                       │   + commands    │
        ┌──────────────▼─────────────────▼───────────┐
        │                Agent Core                  │
        │  conversation · agent loop · context mgmt  │
        │  permissions · events · workflow runner    │
        └───┬──────────┬──────────┬──────────┬───────┘
            │          │          │          │
   ┌────────▼───┐ ┌────▼─────┐ ┌──▼───────┐ ┌▼──────────────┐
   │ Providers  │ │ MCP      │ │ Workspace│ │ Knowledge     │
   │ LLM:       │ │ Manager  │ │ files    │ │ skills files  │
   │  openai-   │ │ stdio    │ │ search   │ │ RAG (Phase 8) │
   │  compat +  │ │ groups   │ │ allowed  │ │ embeddings    │
   │  anthropic │ │ skills   │ │ folders  │ │ providers     │
   │ Embeddings │ │ linkage  │ │ documents│ └───────────────┘
   └────────────┘ └──────────┘ └──────────┘
            │          │
            ▼          ▼
     Ollama / LM     pyirena-mcp · bait_mcp · future MCPs
     Studio / Unsloth│(each optionally shipping skills files)
     OpenAI / Claude │
     ANL Argo proxy  │
                     ▼
        ┌────────────────────────────┐
        │ Persistence (~/.aida)      │
        │ SQLite · artifacts · logs  │
        │ Records (~/Documents/Aida) │
        └────────────────────────────┘
```

Hard rules (enforced by tests, mirroring pyIrena's layering invariants):

1. `aida.core`, `aida.providers`, `aida.mcp`, `aida.workspace`, `aida.knowledge`,
   `aida.persistence` **never import Qt**. `aida.ui` depends on core; core never
   depends on ui. Verified by a grep-style contract test.
2. All Qt imports in `aida.ui.qt` go through a single `_qt.py` shim (pyIrena
   pattern), enabling PySide6/PyQt6 normalization and a contract test.
3. **Typed results throughout.** MCP `ImageContent` becomes `ImageArtifact(bytes,
   mime_type)` immediately; files become `FileArtifact`; JSON stays structured.
   The GUI never guesses whether a long string is an image.
4. Core↔frontend communication is an **event stream** (`TextDelta`,
   `ToolCallStarted`, `ImageArtifactCreated`, `AgentError`, ...). Any frontend —
   Qt, CLI, future web — subscribes to the same events.
5. The agent loop has a configurable max-iterations guard and a user Stop.
6. `aida.api`-style surfaces return JSON-serializable data only.

### Agent loop

```text
user message → build context (system prompt + skills + retrieved docs
             + conversation) → provider.complete(messages, tools)
             → stream text deltas to UI
             → on tool call: resolve (native workspace tool | MCP tool)
                → permission check → execute → typed artifact
                → artifact to UI + artifact store; result back to LLM
             → repeat until final text or iteration cap
```

### Repository structure

```text
Aida/
├── PLAN.md                      # this file
├── planning/                    # per-phase checklists (phase01...phase10, future)
├── pyproject.toml               # package `aida`, extras: gui, rag, docs, dev
├── environment.yml              # conda env (aida)
├── src/aida/
│   ├── core/                    # agent.py, conversation.py, events.py, context.py,
│   │                            # permissions.py, workflows.py
│   ├── providers/               # base.py, openai_compat.py, anthropic_.py,
│   │                            # embeddings_base.py, profiles.py
│   ├── mcp/                     # manager.py, server.py, groups.py, results.py, config.py
│   ├── workspace/               # workspace.py, files.py, search.py, safety.py
│   ├── documents/               # readers (pdf/docx/xlsx/pptx/md/txt/code),
│   │                            # writers (md_obsidian.py, docx.py ...)
│   ├── knowledge/               # skills.py, rag/ (index, extraction, retrieval)
│   ├── artifacts/               # base.py, image.py, file.py, table.py, store.py
│   ├── persistence/             # database.py, records.py, cleanup.py
│   ├── config/                  # settings.py, secrets.py, paths.py
│   ├── coding/                  # templates.py, runner.py (subprocess + timeout)
│   ├── cli/                     # chat.py, run.py, doctor.py, config_cmds.py
│   └── ui/qt/                   # _qt.py, main_window.py, chat_panel.py,
│                                # workspace_bar.py, mcp_panel.py, settings_dialog.py,
│                                # code_panel.py, conversations_panel.py
├── skills/                      # example/starter skills shipped as *samples* only
├── examples/                    # scripted usage of the core (no GUI)
└── tests/                       # incl. contract tests for layering + _qt + events
```

---

## 4. Providers and configuration

### LLM providers

Two provider classes cover everything currently needed:

- `OpenAICompatProvider` (`openai` SDK, custom `base_url`): Ollama, LM Studio,
  Unsloth Desktop, llama.cpp server, OpenAI itself, other compatible services.
- `AnthropicProvider` (`anthropic` SDK, custom `base_url`): Claude direct **and**
  Claude via ANL Argo proxy (`base_url=https://apps.inside.anl.gov/argoapi/`,
  api_key = ANL username — as in BeamlineAdvisor).

Both implement one interface: `complete(messages, tools, settings) → event stream`,
including native tool-calling translation for each API dialect. Embedding
providers follow the same pattern (`OpenAICompatEmbeddings`,
local/Ollama embeddings, Argo cloud embeddings) but are not needed until Phase 8.

### Provider profiles

Users configure **multiple named profiles** and switch between them routinely, from
the GUI (toolbar dropdown) or CLI (`--profile`). A profile = provider type, base
URL, model name, secret reference, sampling defaults, capability notes (e.g.
"small local model — prefer lean MCP groups").

### Configuration layout — on device, never in the repo

```text
~/.aida/
├── config.yaml          # general settings, paths, safety mode, UI prefs
├── providers.yaml       # profiles (NO secrets inline — secret refs only)
├── mcp.json             # standard-style MCP server defs (portable from
│                        #   Claude Desktop etc.) + aida extras per server:
│                        #   skills: [...], groups: [...]
├── workspaces.yaml      # named workspace bundles
├── skills/              # user's skills files (md), per-skill folders
├── workflows/           # stored named workflows (Phase 10)
├── aida.db              # SQLite: conversations, messages, tool calls, artifacts meta
├── artifacts/           # binary artifacts (PNGs etc.) — files, not DB blobs
└── logs/

~/Documents/Aida/        # (configurable) human-readable conversation records,
                         # exported transcripts; safe to browse, safe to delete
```

Secrets (API keys, ANL username) go to the **OS keychain via `keyring`** (already a
pyIrena dependency), with environment-variable override for headless/pipeline use.
`~/.aida` is never inside a repo; nothing secret is ever written to YAML/JSON.

### Workspaces

```yaml
# workspaces.yaml (illustrative)
workspaces:
  use-pyirena:
    profile: argo-claude          # or ollama-qwen, switchable
    source_folders: ["/Volumes/data/USAXS_2026_08"]
    target_folder: "~/Documents/Aida/analysis"
    sidecar_folder_name: "figures"     # Obsidian-style attachments folder
    mcp_group: pyirena-analysis
    skills: [saxs-basics, pyirena-usage]
    system_prompt: prompts/pyirena.md
    safety: relaxed                    # relaxed | confirm
  perform-reviews:
    profile: argo-claude
    source_folders: ["~/Documents/reviews/incoming"]
    target_folder: "~/Documents/reviews/out"
    mcp_group: none
    skills: [review-checklist]
    safety: confirm
```

### MCP groups and skills linkage

- `mcp.json` stays close to the standard ecosystem format so configurations port
  from Claude Desktop / other clients. AIDA-specific keys (`groups`, `skills`) live
  in a parallel section or per-server extension block that other clients ignore.
- A **group** is a named set of servers enabled/disabled together. Rationale:
  pyIrena's tool list is large and needlessly overloads small local models when
  those tools aren't needed.
- Each MCP server may declare associated **skills files**; enabling the server (via
  group or individually) automatically includes those skills in the system context.
  Skills are plain Markdown folders — authorable by hand, shippable next to an MCP.

---

## 5. Safety model

Premise (user decision): working folders are backed up; disaster recovery is
"restore backup". Therefore per-action approval everywhere is wrong for this tool.

- **Allowed folders**: user declares folders where the agent may list, read, write,
  and modify freely. Source folders may be network mounts (appear as local paths —
  no special handling expected; treat slow/missing mounts gracefully).
- **Relaxed mode** (per workspace, default for analysis workspaces): no per-action
  confirmation inside allowed folders. A clear one-time warning explains the deal.
- **Confirm mode**: per-action confirmation for writes/deletes, for cautious users
  or sensitive folders.
- **Always confirmed regardless of mode**: any path outside allowed folders; shell
  commands not on the command allowlist; anything that sends local content to a
  network destination other than the configured LLM provider.
- Command allowlist: a short, user-editable list of safe shell/python invocations
  runnable inside allowed folders (Phase 9).
- Deletions inside allowed folders prefer a `_trash/` move over true deletion where
  cheap to do.

---

## 6. Documents and outputs

- **Read** (agent input): code/text, MD, PDF, DOCX, XLSX, PPTX, CSV, JSON, images.
  Structured extraction where feasible rather than blind text-flattening. HDF5 is
  *not* read natively — that is pyIrena MCP's job — unless a concrete need appears.
- **Write** (agent output): default is **Markdown in Obsidian structure** — MD file
  in the target folder, images stored in a user-nameable sidecar folder and
  referenced by link. Also: plain MD/TXT, and DOCX where users need Office output.
- Target folder is workspace-level, visible and changeable in the GUI at all times.
- Generated files surface in the conversation as artifacts with Open / Reveal
  actions.

---

## 7. Testing philosophy

Every phase ends with a **demonstrable, testable milestone** — its phase file lists
the acceptance checklist. Standing rules:

- pytest suite from Phase 1; headless (no display) like pyIrena's; contract tests
  guard layering, `_qt` usage, and event/JSON serializability.
- A `MockProvider` (scripted responses + tool calls) makes the agent loop testable
  without any model; a `mock-mcp` fixture server (returns text, image, JSON,
  errors) makes MCP handling testable without pyIrena.
- Real-model smoke tests (Ollama small model; Argo Claude when reachable) are
  manual/optional, documented per phase, never required by CI.
- CI: GitHub Actions — ruff + pytest on 3.11/3.13, macOS + Windows + Linux runners
  once the GUI exists (Qt import smoke test only; no display tests).

---

## 8. Dependency policy

Match pyIrena's discipline: small core, extras for the rest, no additions without
demonstrated need.

| Component | Choice | Notes |
|---|---|---|
| LLM SDKs | `openai`, `anthropic` | both already in pyIrena's gui extra |
| MCP | official `mcp` Python SDK | client side; version-range chosen for coexistence with `pyirena[mcp]` in one env |
| GUI | `PySide6` (extra: `gui`) | via `_qt.py` shim |
| Persistence | `sqlite3` stdlib | no ORM |
| Config | `PyYAML` + std `json` | |
| Secrets | `keyring` | env-var fallback |
| Markdown render (GUI) | Qt rich text first; add a md lib only if needed | |
| PDF read | `pymupdf` (extra: `docs`) | BeamlineAdvisor precedent |
| DOCX/XLSX/PPTX | `python-docx`, `openpyxl`, `python-pptx` (extra: `docs`) | read + docx write |
| RAG (Phase 8) | start minimal: chunking + embeddings + `sqlite-vec` or ChromaDB — decide in Phase 8 with a benchmark, don't pre-commit | LlamaIndex only if minimal path proves insufficient |
| Code editor (Phase 9) | Qt plain-text editor + syntax highlighting; consider `QScintilla` only if needed | |

pyIrena is **not** an import dependency — AIDA talks to it only through
`pyirena-mcp` (stdio subprocess, which may live in its own conda env; the `command`
path in `mcp.json` points at that env's executable, so environments need not be
shared).

---

## 9. Distribution

- Now: git clone + `pip install -e .`, conda `environment.yml`.
- Phase 1: claim the `aida` name on PyPI with a minimal placeholder release
  (0.0.1) — the name appears unclaimed and that will not last.
- Phase 10: real PyPI releases (following pyIrena's publish workflow pattern:
  version in `pyproject.toml` authoritative, tag-checked), conda-forge feedstock
  considered after PyPI is stable.
- Native app bundles (PyInstaller/Briefcase) are a Phase 10 *investigation*, not a
  commitment — `pip install aida[gui]` + `aida-gui` entry point is acceptable for
  the target audience (they already install pyIrena this way).

---

## 10. Phase map

Each phase has a checklist file in `planning/`. A phase is *done* when every box in
its acceptance section is checked. Phases 2–4 are headless on purpose — the GUI
(Phase 5) lands on a core that already demonstrably works.

| Phase | File | Delivers | Testable outcome (short form) | UCs |
|---|---|---|---|---|
| 1 | `phase01_foundation.md` | Repo scaffold, config system, secrets, logging, CI, PyPI name claim | `aida doctor` validates a real config; tests green in CI | — |
| 2 | `phase02_agent_core.md` | Provider layer (OpenAI-compat + Anthropic/Argo), streaming agent loop, profiles, CLI chat | CLI chat streams from a local model **and** Argo Claude; switch via `--profile` | UC1 (partial) |
| 3 | `phase03_mcp.md` | MCP manager (stdio), typed artifacts, groups, skills linkage | **The keystone test:** model calls pyirena-mcp, PNG comes back decoded as `ImageArtifact`, saved to disk from CLI | UC3 (headless) |
| 4 | `phase04_persistence_workspaces.md` | SQLite persistence, conversation resume/cleanup, workspace bundles, records folder | Resume yesterday's CLI conversation; `--workspace use-pyirena` loads the whole environment | — |
| 5 | `phase05_gui.md` | PySide6 app v1: chat, streaming, inline images, profile/workspace switchers, conversation browser | Reproduce the Claude-Desktop-style pyIrena interaction fully in AIDA's own GUI | UC1, UC3 |
| 6 | `phase06_documents.md` | Native workspace tools, allowed-folders safety, document readers, Obsidian-style MD output, drag & drop | UC2 end-to-end: drop a PDF, ask questions, get a new MD (+figures sidecar) in the target folder | UC2, UC3 |
| 7 | `phase07_mcp_management.md` | MCP management UI: servers, groups, per-tool permissions, logs, raw result inspector | Add/enable/disable servers & groups entirely from GUI; diagnose a failing tool from the log panel | UC3, UC4 |
| 8 | `phase08_rag.md` | RAG: ingestion, local/cloud embedding profiles, index management, retrieval into context | UC1 full: documentation folder indexed; answers cite retrieved passages; index rebuild from GUI | UC1 |
| 9 | `phase09_coding_scripting.md` | Code editor widget, templates, script save/run (AIEvaluator), command allowlist, web search | UC5: check beamline status via AIEvaluator script + bait_mcp from an AIDA workspace | UC5 |
| 10 | `phase10_automation_distribution.md` | `aida run` headless CLI, stored named workflows, simple scheduler, PyPI/conda packaging | A stored workflow runs from a shell script with no GUI; `pip install aida` works | UC6 |
| — | `phase_future.md` | Parked: voice STT/TTS, beamline credentials, external triggers, HTTP MCP, web frontend | (idea log, not a commitment) | UC6+ |

Dependencies are linear except: Phase 6 and 7 can proceed in parallel after 5;
Phase 8 needs only 5 (+ embeddings config from 2); Phase 9 needs 6 (safety model).

---

## 11. Working agreements

- Update the phase checklist file in the same commit as the work it tracks.
- New capability = new checkbox in a phase file (or a line in `phase_future.md`),
  not silent scope growth.
- Anything that changes a decision in Section 2's table gets a dated note appended
  to that section — the reconciliation history stays in one place.
- Scientific users are the audience: error messages must say *which* layer failed
  (provider, MCP client, MCP server, tool, UI) — diagnostics are a feature.
