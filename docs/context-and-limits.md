# Context window and limits

> **Status: beta (0.1.0b4).** Phases 1–10 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [providers-and-secrets.md](providers-and-secrets.md) · [mcp-servers.md](mcp-servers.md) · [pyirena.md](pyirena.md)

Every model has a finite context window — the total number of tokens it can
read in one request, covering the system prompt, every MCP tool's schema,
the whole conversation so far, and the reply it's about to generate. A long
pyIrena analysis session (tens of tool calls, each returning dense numeric
results) is exactly the workload that runs into that wall, and running out
mid-analysis used to be the worst failure in AIDA: the conversation was
simply dead, with New Chat as the only way out. This page covers what fills
the window, how AIDA manages it, and what to do when a conversation gets
long.

## What actually fills the window

It's not just the visible conversation:

- **The conversation history** — every message sent so far, tool calls and
  tool results included.
- **Every enabled MCP server's tool schemas**, sent fresh on *every single
  request*, not just the first. This is easy to underestimate: pyirena-mcp
  alone exposes around 68 tools, whose JSON schemas measure out to roughly
  **10,200 tokens on every turn** — see
  [mcp-servers.md](mcp-servers.md#groups) on using a lean group instead of
  enabling every server's every tool.
- **Vision images** — plot PNGs a tool returns, or an image you attach in
  the GUI, each costing real tokens once actually sent to the model.
- **The reply about to be generated** — reserved ahead of time so the
  model always has room to finish its answer instead of getting cut off
  mid-sentence.

AIDA estimates all of this without a real tokenizer (deliberately — the
tokenizer that matters differs per model, and getting it wrong would be
false precision for no real benefit). Dense content — tool arguments, tool
results, JSON in general — is estimated at a different, more conservative
rate than ordinary prose, since numeric/JSON data tokenizes more densely
than English text.

## Setting the model's real window

`config.yaml`'s `max_context_tokens` is the *global* default (`120000` out
of the box) — the budget AIDA works from until a profile says otherwise.
Set `context_window` on a specific profile in `providers.yaml` (or in the
Providers… dialog's "Context window" field) once you actually care about
that model's real number:

```yaml
profiles:
  local-qwen:
    kind: openai_compat
    base_url: "http://localhost:11434/v1"
    model: "qwen2.5:32b"
    context_window: 128000   # this model's real total window
```

This matters most in two directions: a small local model (128k-class) can
be genuinely unsafe on the 120k global default once tool schemas are
counted in, while a 1M-token cloud model wastes the vast majority of its
own window sitting on that same conservative default. `aida doctor` warns
when a configured profile has no `context_window` set, and when the global
default is larger than a profile's known real window.

**Don't confuse this with `max_tokens`** (see
[providers-and-secrets.md](providers-and-secrets.md#provider-profile-fields)):
`max_tokens` caps the *output* a single reply generates; `context_window` is
the model's *total* window that the output, the history, and the tool
schemas all have to fit inside together. Setting `max_tokens` to your
model's full context size — a natural-sounding but wrong reading of "max
tokens" — leaves no room for history at all and clamps every turn's budget
to a bare 8000-token floor regardless of `context_window`; see the callout
in providers-and-secrets.md for exactly what that looks like in the logs
and how to fix it. `aida doctor` catches this specific mistake.

## Compaction

When history no longer fits, AIDA doesn't just discard the oldest turns —
it first asks the active model to **summarize** them into a compact set of
facts (files and folders touched, parameter values and fit results with
their exact numbers, decisions made and why), then replaces those turns
with that one summary instead of dropping them outright. Filenames and
numeric results are explicitly asked to survive verbatim, since those are
exactly what a pyIrena session needs to keep. If the summarization call
itself fails for any reason, AIDA falls back to plain trimming (today's
prior behavior) rather than failing your turn — compaction is a strict
improvement, never a new way to break.

This happens automatically once a conversation goes over budget, and you
can also trigger it yourself at a natural task boundary rather than
mid-thought:

- **CLI**: type `/compact` in the chat REPL.
- **GUI**: File → **Compact Conversation**.

Either way, AIDA reports what happened — "summarized N old turns into ~M
tokens" when compaction succeeded, or "trimmed N old turns" if it had to
fall back to a plain drop.

## Reading the fullness indicator

- **CLI**: a `[context] used / budget tokens (P%)` line after each turn.
- **GUI**: a **Context: Nk / Mk (P%)** label in the status bar, next to
  (not to be confused with) the **Session total:** label — that one is the
  cumulative token count/cost for the whole session and only ever grows;
  **Context:** is the current fullness of the window and goes back down
  after a compaction.

When it reads "(trimming disabled)", `max_context_tokens` is set to `0`
(trimming/compaction off entirely) — not recommended for a long session,
since the only recovery left at that point is starting a new conversation.

## What to do when a conversation gets long

1. Check the fullness indicator — if it's climbing toward 100%, that's the
   moment to compact rather than wait for it to trigger mid-turn.
2. Run `/compact` (CLI) or File → Compact Conversation (GUI) at a natural
   pause — between analysis steps, not mid-thought.
3. If you're on a small local model and enabling a large MCP group (see
   [mcp-servers.md](mcp-servers.md#groups)), consider a leaner group for
   that workspace, or set an explicit `context_window` so AIDA budgets
   correctly for the model you're actually running.
4. `aida doctor` flags a profile with no `context_window` set, and a
   global default larger than a profile's known real window — worth a look
   if a session hits the wall unexpectedly.
