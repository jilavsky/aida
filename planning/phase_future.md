# Future ideas — parked, not committed

An idea log. Nothing here blocks the 10 phases; each item graduates by getting its
own phase file (and a note in PLAN.md §2) when its time comes. Keep adding items
here instead of growing phase scope.

---

## Voice

- [ ] **STT (speech-to-text) input** — last-phase option, if at all. On macOS,
      OS-wide dictation already covers it; Windows/Linux would need something like
      local Whisper (adds a heavy dependency). Decision criterion: real user demand
      at the beamline. If done: a mic button feeding the normal input box, nothing
      deeper.
- [ ] **TTS output** — unclear what it should even mean for this tool (read
      summaries aloud?). Explicitly not worried about now; revisit only after STT
      exists and earns its keep.

## Beamline user credentials

- [ ] Per-user identity as in BeamlineAdvisor (username selection → per-user chats
      and saved scripts). Useful at a shared beamline computer; overhead is real:
      multi-user data separation in the DB, records folders per user, secret
      handling per user. Revisit when AIDA is actually deployed on usaxscontrol;
      likely a thin "active user" layer over persistence rather than real auth.
      Keep the door open now by storing a `user` column (nullable) in Phase 4's
      schema — cheap insurance, no behavior.

## Triggered / event-driven reports

- [ ] External triggers (something happened at the instrument → generate report)
      remain **external code invoking `aida run`** (Phase 10 gives the hook). If a
      pattern repeats often, consider a small watcher (folder-watch → workflow)
      inside AIDA.

## Transport & integration

- [ ] Remote MCP servers over HTTP/SSE transport (instrument-side MCPs reachable
      from an office machine). The MCP manager was designed transport-pluggable;
      add when a concrete remote server exists.
- [ ] MCP Apps / rich interactive tool outputs, if the ecosystem standardizes them.
- [ ] Interactive plots (pyqtgraph pane fed by structured data artifacts) as an
      upgrade over static PNGs — coordinate with pyIrena MCP capabilities first.

## Frontends

- [ ] Alternative web frontend (NiceGUI or similar) on the same event API — for
      browser access on beamline LAN machines, the BeamlineAdvisor deployment
      pattern. Only worthwhile once the event API has proven stable through the
      PySide6 app.
- [ ] Extra GUI niceties: per-display font/scaling profiles, additional dockable
      widgets.

## Knowledge

- [ ] RAG over past conversations ("what did we conclude last week?").
- [ ] Reranking model in retrieval if Phase 8's benchmark quality plateaus.
- [ ] Automatic knowledge-base refresh (folder watcher).

## Misc

- [ ] HDF5/NeXus native reading — only if a concrete non-pyIrena need appears
      (currently pyirena-mcp owns this, deliberately).
- [ ] Conversation sharing/export bundles (zip of transcript + artifacts) for
      sending an analysis session to a colleague.
- [ ] pynika / other package MCPs as they appear — should "just work" via Phase 7
      management UI; add starter skills files alongside.
