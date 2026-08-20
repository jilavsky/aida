"""RAG (retrieval-augmented generation) over documentation (Phase 8).

- ``chunking`` — split extracted document text into retrievable pieces.
- ``index`` — one SQLite file per knowledge base storing chunks + embeddings.
- ``ingest`` — walk a knowledge base's source folders, chunk, embed, store;
  incremental by file mtime.
- ``retrieval`` — embed a query and rank a knowledge base's stored chunks
  against it.

Never imports Qt (PLAN.md hard rule 1) — the GUI's Knowledge management
dialog only ever calls into this through plain functions/dataclasses.
"""
