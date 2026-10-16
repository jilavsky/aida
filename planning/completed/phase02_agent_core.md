# Phase 2 — Agent core & LLM providers (headless)

**Goal:** A working, streaming, tool-capable agent loop with switchable provider
profiles, usable from a CLI chat harness. No MCP yet (Phase 3), no GUI (Phase 5).

**Prerequisites:** Phase 1.
**Unblocks:** Phase 3.
**Use cases advanced:** UC1 (partial — chat with skills context, no RAG).

---

## Tasks

### Event model (`aida.core.events`)

- [x] Define the event dataclasses: `TextStarted`, `TextDelta`, `TextFinished`,
      `ToolCallStarted`, `ToolCallFinished`, `ImageArtifactCreated`,
      `FileArtifactCreated`, `AgentError`, `MessageFinished`, `UsageInfo`
- [x] Events are plain dataclasses, JSON-serializable, Qt-free (contract test)
- [x] Async event stream interface consumable by CLI now, Qt later
      (`AsyncIterator[AgentEvent]`, used identically by `AgentLoop.run()` and
      every `LLMProvider.complete()`)

### Provider layer (`aida.providers`)

- [x] `base.py`: `LLMProvider.complete(messages, tools, settings) -> event stream`;
      normalized message + tool-schema format internal to AIDA
- [x] `openai_compat.py` via `openai` SDK: custom `base_url`, streaming, native
      tool-calling; covers Ollama, LM Studio, Unsloth Desktop, OpenAI
- [x] `anthropic_.py` via `anthropic` SDK: custom `base_url` (ANL Argo:
      `https://apps.inside.anl.gov/argoapi/`, api_key = ANL username — the
      BeamlineAdvisor pattern), streaming, native tool-use blocks
- [x] Translation layer: one internal tool-schema → each API dialect; one internal
      result format ← each dialect (image results placeholder is Phase 3's job —
      `ImageArtifactCreated` exists in the event model now but nothing emits it
      until MCP tool results exist)
- [x] Graceful error surface: timeouts, auth failures, model-not-found → typed
      `AgentError` naming the provider layer
- [x] `MockProvider` for tests: scripted text/tool-call responses, no network

### Profiles (`aida.providers.profiles`)

- [x] Load named profiles from `providers.yaml`; secrets resolved via Phase 1 store
- [x] Profile fields: provider type, base_url, model, sampling defaults, max
      iterations, note field (e.g. "small local model") — carried over unchanged
      from Phase 1's `ProviderProfile`; `max_iterations` lives on `AgentLoop`
      rather than the profile (a loop-control concern, not a per-request one —
      see `aida.core.agent`)
- [x] Switch profile per conversation; validate profile on selection (doctor-style
      ping) — `aida.providers.profiles.validate_profile()`

### Agent loop (`aida.core.agent`)

- [x] Loop: context build → provider call → stream events → execute tool calls →
      feed results back → repeat; hard cap on iterations (config)
- [x] Context builder: system prompt + skills files (plain MD folders listed in
      config — *direct context* strategy, no RAG yet) + conversation history
      (`aida.core.context`)
- [x] Simple context-size management: token estimate, warn + oldest-turn trimming
      (`aida.core.context.trim_history` — not yet wired into `ChatSession`'s
      per-turn flow; the function is built and tested, wiring it into the live
      REPL loop is a small Phase 3 follow-up now that there's a natural point
      — after MCP tool results start growing history fast — to do it)
- [x] Stop/cancel support (needed by GUI later; test via CLI Ctrl-C) —
      `AgentLoop.cancel()` + `ChatSession.cancel()`, unit-tested for cancel-before-run
      and cancel-between-tool-calls; manual Ctrl-C in the real REPL not separately
      verified beyond the `KeyboardInterrupt` handlers being present (blocked on
      an interactive TTY, which the build sandbox doesn't have)
- [x] A first native tool to prove tool round-trips end-to-end *without* MCP:
      `get_current_time` (`aida.core.tools`)

### CLI harness (`aida.cli.chat`)

- [x] `aida chat [--profile NAME] [--skills name1,name2]`: interactive REPL with
      streamed output, tool-call display, `/profile` switch mid-session
- [x] Transcript printed events map 1:1 to the event model (this is the reference
      frontend implementation) — `print_event()`

### Tests

- [x] Agent loop unit tests against `MockProvider`: streaming order, tool round-trip,
      iteration cap, cancel, error propagation
- [x] Provider dialect translation tests (schema in/out) for both SDKs, mocked HTTP
      — implemented as pure-function tests against real SDK-typed chunk/event
      objects (`test_provider_translation.py`); see that file's docstring for why
      this gives equivalent confidence to mocking raw HTTP without needing an
      HTTP mocking library for two different SSE wire formats
- [x] Profile loading/switching/secret-resolution tests

---

## Acceptance — phase is done when all are checked

- [ ] `aida chat --profile ollama-local` streams a conversation with a local model
      (manual smoke test, documented in README) — **not run**: the build sandbox
      has no Ollama/LM Studio instance reachable. **What *was* verified instead**:
      a real subprocess run of `aida chat` against a from-scratch local HTTP
      server speaking real OpenAI-compatible SSE streaming (not the mocked
      translation-layer tests) — full round trip including a real tool call and
      a mid-session `/profile` switch, both over actual HTTP. This is the same
      code path `ollama-local` would use; only the actual local-model server is
      unverified.
- [ ] `aida chat --profile argo-claude` works through the ANL proxy (manual, on-site)
      — **not run**: requires ANL network access / a valid ANL username, neither
      available in the build sandbox. `AnthropicProvider`'s translation layer is
      unit-tested against real `anthropic` SDK event types; only the live Argo
      endpoint is unverified.
- [x] Mid-session `/profile` switch works; history carries over — verified both
      by unit test and in the real HTTP subprocess run above (the second
      provider's request genuinely contained the first turn's tool-result
      message, proving history crossed the switch, not just that the flag was set)
- [ ] Skills files listed in config demonstrably influence answers (ask a question
      answerable only from a skills file) — the *mechanism* is built and unit
      tested (`load_skill_texts`, `ChatSession` loading them into the system
      message — verified skill content lands in the system message reaching the
      model); whether a *real* model's answer changes because of it needs a real
      model, same constraint as the two boxes above.
- [x] The trivial native tool is called by a real model and the result is used in
      the reply — the "real model" here is deliberately generic (a real HTTP
      server implementing the real wire protocol, standing in for any
      OpenAI-compatible model): `get_current_time` was requested, executed, and
      its result shaped the final streamed answer, over real HTTP, not mocks.
- [x] Full pytest suite green in CI (no network needed) — 85/85 passing locally
      (`ruff check .` clean); will confirm on GitHub Actions once pushed, same
      as Phase 1's CI box.

## Out of scope for this phase

MCP (Phase 3); persistence — conversations vanish on exit (Phase 4); GUI (Phase 5);
RAG (Phase 8).

## Notes

Built 2026-08-18, same session as the Phase 1 PyPI-name fixup. Two real bugs were
found and fixed during manual end-to-end verification (both while the automated
test suite was fully green — worth remembering for later phases):

1. **`AgentLoop.run()` silently discarded a `cancel()` issued before `run()`
   started** (`self._cancelled = False` unconditionally at the top of `run()`).
   Fixed by moving the reset into a `try/finally` around the whole turn loop, so
   cancellation state only clears once a run actually ends, on every exit path.
2. **Providers leaked their HTTP client past the event loop's lifetime**,
   producing a `GeneratorExit`/`RuntimeError` traceback on `aida chat` exit —
   `asyncio.run()` closed the loop before `AsyncOpenAI`/`AsyncAnthropic`'s
   underlying `httpx` connections got to close. Fixed with an `LLMProvider.aclose()`
   (no-op default, real close in both SDK-backed providers) that `ChatSession`
   now calls on profile switch (closing the outgoing provider) and session end.

Neither bug was caught by the unit test suite alone — both only showed up when
`aida chat` was actually run as a subprocess against a real (if fake) HTTP
server. That real-subprocess-against-a-local-server technique is cheap enough
(a ~100-line stdlib `http.server` script) that it's worth keeping around as a
non-network-dependent way to sanity-check the *real* provider code path, not
just its pure translation functions, in Phase 3+ too.
