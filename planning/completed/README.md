# Completed planning documents — archive

Everything in this folder describes work that is **delivered**. It is kept
unchanged as the record of *why* each piece is shaped the way it is; it is
not a to-do list and nothing here should be edited to reflect later
changes. When a decision recorded here is revised, the note goes in
[`../DESIGN.md`](../DESIGN.md) §2 or [`../../CHANGELOG.md`](../../CHANGELOG.md),
not here.

Moved here 2026-10-16, when the 1.0 review confirmed that the
implementation side of every phase was done. A handful of `[ ]` boxes
survive in the phase files below: without exception they are *manual
acceptance checks* no sandbox could perform (a live beamline demo, a
clean-machine install, CI on three OSes). They are tracked centrally in
[`../../TODO.md`](../../TODO.md) rather than per phase, so there is one
place to look.

## Where to look for what

| Looking for | Read |
|---|---|
| The narrative account of everything delivered, with rationale | [`../COMPLETED.md`](../COMPLETED.md) |
| What shipped in which release | [`../../CHANGELOG.md`](../../CHANGELOG.md) |
| The enduring design and its hard rules | [`../DESIGN.md`](../DESIGN.md) |
| What is still open | [`../../TODO.md`](../../TODO.md), [`../../PLAN.md`](../../PLAN.md) |

## Contents

### Phase checklists — the ten phases AIDA was built in

| File | What it covers |
|---|---|
| [`phase01_foundation.md`](phase01_foundation.md) | Config, paths, secrets, `aida doctor`, packaging skeleton |
| [`phase02_agent_core.md`](phase02_agent_core.md) | Provider layer, the agent loop, events, skills in context |
| [`phase03_mcp.md`](phase03_mcp.md) | MCP client, typed artifacts, groups, image results |
| [`phase04_persistence_workspaces.md`](phase04_persistence_workspaces.md) | SQLite persistence, conversation records, named workspaces |
| [`phase05_gui.md`](phase05_gui.md) | The PySide6 desktop app on the core's event API |
| [`phase06_documents.md`](phase06_documents.md) | Document reading/writing, attachments, the safety model |
| [`phase07_mcp_management.md`](phase07_mcp_management.md) | The MCP management dialog and per-tool permissions |
| [`phase08_rag.md`](phase08_rag.md) | Folder RAG, embedding profiles, knowledge bases |
| [`phase09_coding_scripting.md`](phase09_coding_scripting.md) | Code editor, script runner, templates |
| [`phase10_automation_distribution.md`](phase10_automation_distribution.md) | `aida run`, stored workflows, the scheduler, distribution |

### Design notes for work that shipped

| File | What it covers |
|---|---|
| [`phase10_scheduling_design.md`](phase10_scheduling_design.md) | The scheduler's deferral policy and headless-secret analysis |
| [`context_management.md`](context_management.md) | Context windows, trimming, compaction |
| [`documents_implementation.md`](documents_implementation.md) | The document reader/writer stack as built |
| [`document_images.md`](document_images.md) | Figure indexing, `get_document_figure`, the optional Mistral OCR backend |
| [`gui_implementation_guide.md`](gui_implementation_guide.md) | How the Qt layer is put together, and its conventions |
| [`multiuser_plan.md`](multiuser_plan.md) | Conversation labelling — the "user" organization axis |

### Review rounds, closed

| File | What it covers |
|---|---|
| [`improvement_plan_2026-08.md`](improvement_plan_2026-08.md) | The 2026-08 review round — all 35 items closed |
| [`improvement_plan_2026-09.md`](improvement_plan_2026-09.md) | The 2026-09 review round — all 17 items closed |

### Historical

| File | What it covers |
|---|---|
| [`local_scientific_ai_agent_proposal.md`](local_scientific_ai_agent_proposal.md) | The original proposal AIDA grew out of. `../DESIGN.md` §2 records what was kept and what was changed |
| [`phase_future.md`](phase_future.md) | The first parked-ideas list, superseded 2026-08-28 by `PLAN.md` §2 |
