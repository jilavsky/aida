# Phase 2 — Agent core & LLM providers (headless)

**Goal:** A working, streaming, tool-capable agent loop with switchable provider
profiles, usable from a CLI chat harness. No MCP yet (Phase 3), no GUI (Phase 5).

**Prerequisites:** Phase 1.
**Unblocks:** Phase 3.
**Use cases advanced:** UC1 (partial — chat with skills context, no RAG).

---

## Tasks

### Event model (`aida.core.events`)

- [ ] Define the event dataclasses: `TextStarted`, `TextDelta`, `TextFinished`,
      `ToolCallStarted`, `ToolCallFinished`, `ImageArtifactCreated`,
      `FileArtifactCreated`, `AgentError`, `MessageFinished`, `UsageInfo`
- [ ] Events are plain dataclasses, JSON-serializable, Qt-free (contract test)
- [ ] Async event stream interface consumable by CLI now, Qt later

### Provider layer (`aida.providers`)

- [ ] `base.py`: `LLMProvider.complete(messages, tools, settings) -> event stream`;
      normalized message + tool-schema format internal to AIDA
- [ ] `openai_compat.py` via `openai` SDK: custom `base_url`, streaming, native
      tool-calling; covers Ollama, LM Studio, Unsloth Desktop, OpenAI
- [ ] `anthropic_.py` via `anthropic` SDK: custom `base_url` (ANL Argo:
      `https://apps.inside.anl.gov/argoapi/`, api_key = ANL username — the
      BeamlineAdvisor pattern), streaming, native tool-use blocks
- [ ] Translation layer: one internal tool-schema → each API dialect; one internal
      result format ← each dialect (including image results placeholder for Phase 3)
- [ ] Graceful error surface: timeouts, auth failures, model-not-found → typed
      `AgentError` naming the provider layer
- [ ] `MockProvider` for tests: scripted text/tool-call responses, no network

### Profiles (`aida.providers.profiles`)

- [ ] Load named profiles from `providers.yaml`; secrets resolved via Phase 1 store
- [ ] Profile fields: provider type, base_url, model, sampling defaults, max
      iterations, note field (e.g. "small local model")
- [ ] Switch profile per conversation; validate profile on selection (doctor-style
      ping)

### Agent loop (`aida.core.agent`)

- [ ] Loop: context build → provider call → stream events → execute tool calls →
      feed results back → repeat; hard cap on iterations (config)
- [ ] Context builder: system prompt + skills files (plain MD folders listed in
      config — *direct context* strategy, no RAG yet) + conversation history
- [ ] Simple context-size management: token estimate, warn + oldest-turn trimming
- [ ] Stop/cancel support (needed by GUI later; test via CLI Ctrl-C)
- [ ] A first native tool to prove tool round-trips end-to-end *without* MCP:
      `get_current_time` or similar trivial tool

### CLI harness (`aida.cli.chat`)

- [ ] `aida chat [--profile NAME] [--skills name1,name2]`: interactive REPL with
      streamed output, tool-call display, `/profile` switch mid-session
- [ ] Transcript printed events map 1:1 to the event model (this is the reference
      frontend implementation)

### Tests

- [ ] Agent loop unit tests against `MockProvider`: streaming order, tool round-trip,
      iteration cap, cancel, error propagation
- [ ] Provider dialect translation tests (schema in/out) for both SDKs, mocked HTTP
- [ ] Profile loading/switching/secret-resolution tests

---

## Acceptance — phase is done when all are checked

- [ ] `aida chat --profile ollama-local` streams a conversation with a local model
      (manual smoke test, documented in README)
- [ ] `aida chat --profile argo-claude` works through the ANL proxy (manual, on-site)
- [ ] Mid-session `/profile` switch works; history carries over
- [ ] Skills files listed in config demonstrably influence answers (ask a question
      answerable only from a skills file)
- [ ] The trivial native tool is called by a real model and the result is used in
      the reply
- [ ] Full pytest suite green in CI (no network needed)

## Out of scope for this phase

MCP (Phase 3); persistence — conversations vanish on exit (Phase 4); GUI (Phase 5);
RAG (Phase 8).
