# Inputs for the documentation screenshots

Small tracked files the screenshot script (`scripts/make_doc_screenshots.py`)
seeds into a scratch `AIDA_HOME`. Nothing here is read by AIDA itself.

| File | What it is |
|---|---|
| `conversation-*.json` | Conversations written into the scratch database through AIDA's persistence API (`_package.seed_conversation`). Invented text; no real users, hosts or results. |
| `pyirena-plot-iq.png` | The returned "plot" in the seeded pyIrena exchange: pyIrena's `docs/tools/img/data-explorer/data-explorer-02-graph.png` (three files from `validationData/unified/` with their Unified models), cropped to the graph. |
