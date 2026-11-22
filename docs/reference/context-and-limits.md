(context-and-limits)=
# Context window and limits

> **Status: 1.1.1.** In daily use at APS beamlines 12-ID/9-ID. Config
> formats and CLI commands are stable: from here, a breaking change to
> either means a new major version, and every change is dated in
> [`CHANGELOG.md`](https://github.com/jilavsky/aida/blob/main/CHANGELOG.md). See [`TODO.md`](https://github.com/jilavsky/aida/blob/main/TODO.md) for what
> is still open.

**Related:** [providers-and-secrets.md](providers-and-secrets.md) · [mcp-servers.md](mcp-servers.md) · [pyirena.md](../guides/pyirena.md)

Every model has a finite context window — the total number of tokens it can
read in one request, covering the system prompt, every MCP tool's schema,
the whole conversation so far, and the reply it's about to generate. A long
pyIrena analysis session (tens of tool calls, each returning dense numeric
results) is exactly the workload that runs into that wall, and running out
mid-analysis used to be the worst failure in AIDA: the conversation was
simply dead, with New Chat as the only way out. This page covers what fills
the window, how AIDA manages it, and what to do when a conversation gets
long.

(context-and-limits-what-fills)=
## What actually fills the window

It's not just the visible conversation:

- **The conversation history** — every message sent so far, tool calls and
  tool results included.
- **Every enabled MCP server's tool schemas**, sent fresh on *every single
  request*, not just the first. This is easy to underestimate: pyirena-mcp
  alone exposes 28 tools, whose JSON schemas measure out to roughly
  **5,500 tokens on every turn** — see
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

(context-and-limits-window)=
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
and how to fix it. `aida doctor` catches this specific mistake, and adds a
note when `max_tokens` takes more than 15 % of `context_window`: that is
legal, but every token reserved for the reply is a token the history
can't use (128k of a 512k window, for example).

**`context_window` must match what the server actually runs with.** For a
local server the window is set when the model is loaded: llama.cpp's
`--ctx-size`, Ollama's `num_ctx`, LM Studio's context length setting. If the
profile says 512k but the server was started with 128k, AIDA budgets for
512k and the server rejects requests long before compaction ever starts.
Set `context_window` to the server's number, not the model card's maximum.

(context-and-limits-compaction)=
## Compaction

Before every request, including every step inside a long agent turn, AIDA
checks the estimated size of the history against the budget. When it no
longer fits, it works through these steps in order, stopping as soon as the
history fits again:

1. **Shorten old tool results.** The content of older tool results (a
   100,000-character file read from 150 tool calls ago, say) is replaced by
   a one-line note naming the call and its size, and the model is told it
   can call the tool again if it needs that result. Long arguments of old
   tool calls, such as the text of a report written with `write_file`, are
   shortened the same way. The last three tool rounds are never touched.
   This needs no model call, and it works *inside* a single turn, which
   matters for long autonomous runs: one request such as "review every
   proposal in this folder" can run for hundreds of tool calls without
   coming back to you. Once this step has to run, it shortens down to
   80 % of the budget, so it doesn't repeat on every request.
2. **Summarize the oldest turns.** AIDA asks the active model to
   **summarize** the oldest whole turns into a compact set of facts (files
   and folders touched, parameter values and fit results with their exact
   numbers, decisions made and why), then replaces those turns with that
   one summary. Filenames and numeric results are explicitly asked to
   survive verbatim, since those are exactly what a pyIrena session needs
   to keep. The last four turns are kept.
3. **Keep only the current turn.** If the history is still over budget,
   older turns are summarized too, down to the turn you are in now (your
   request that started it is always kept).

There is only ever one summary. When more turns are summarized later, the
previous summary and the new turns are merged into one new summary; a
summary is never summarized again on its own. In the summary request each
tool result is cut to its first and last 1,500 characters, and a very long
transcript is summarized in pieces that each fit in the window.

If the summarization call fails for any reason, AIDA falls back to plainly
dropping those turns rather than failing your turn.

All of this changes only what is sent to the model. The conversation
database and the Markdown record keep every message in full.

**One very large tool result.** A single tool result may take up to about
5 % of the history budget (at least 8,000 and at most 100,000 characters).
Anything longer, from AIDA's own tools or from an MCP server, is saved in
full to `tool-results/` in the scratch folder, and the model gets the start
and the end of it plus the file's path. It can read the rest with
`read_file`, which takes optional `offset` and `length` arguments (in
characters) for reading a long file in parts. You still see the full
result in the chat.

Web results get lower limits whatever the window: 10,000 characters for a
search (any tool whose name contains `web_search` or `search_web`), and
15,000 for a fetched or extracted page (`web_fetch`, `fetch_url`,
`web_extract`, `tavily_extract`). The useful part of a search is its first
hits, and in one long research session web searches alone were more than
half of everything sent to the model. MCP tools are matched too (their
names are `<server>__<tool>`). Change it, or add limits for other tools, in
`config.yaml`, keyed by a pattern on the tool name:

```yaml
tool_result_char_caps:
  "*web_search*": 20000     # replaces the built-in 10,000
  "*fetch_url*": 15000      # AIDA's own web page fetch
  "parallel-websearch__*": 8000
```

When several patterns match, the smallest limit wins. A limit only ever
lowers the general one above, never raises it.

**When nothing more can be done.** If the current turn alone is larger than
the budget, compaction can't help. AIDA then says so (a banner in the GUI
chat, a `[context]` line in the CLI) instead of leaving you with only the
provider's length error. Start a new conversation, or see
[below](context-and-limits-long) for how to split a long job.

You can also compact yourself at a natural task boundary rather than
mid-thought:

- **CLI**: type `/compact` in the chat REPL.
- **GUI**: File → **Compact Conversation**.

Manual compaction shortens every old tool result outside the last three
tool rounds and summarizes everything but the last four turns, so it helps
even in a conversation with only one or two long turns.

% CHECK(Jan): not run, needs a model (/compact and Compact Conversation both call the active model).

Either way, AIDA reports what happened, for example "shortened 12 old tool
results; summarized 3 old turns into ~900 tokens", or "dropped 3 old
turns" if it had to fall back to a plain drop. In the GUI this appears as
a quiet grey line in the chat, at the point where it happened, with the
size before and after:

```text
──── Context reduced: shortened 34 old tool results (~780,412 → ~606,003 tokens) ────
```

It is also shown briefly in the status bar. The line is for you only: it
is not saved with the conversation and the model never sees it.

(context-and-limits-stops)=
## When the agent stops in the middle of a task

AIDA ends a turn when the model replies without asking for a tool, and
waits for you. In a long job two kinds of reply do that by mistake, and
AIDA now keeps the turn going for each, once or twice, with a message of
its own. That message is shown in the chat as a user message marked
"sent automatically by AIDA", and it stays in the conversation.

- **A reply cut off at the output limit.** Every reply is limited to the
  profile's `max_tokens`, and for a reasoning model the hidden thinking
  counts against that limit too. A long planning phase followed by a large
  `write_file` can run out before the tool call is complete, and some
  servers then send the reply without the half-finished call. AIDA asks
  the model to continue and to write large files in several smaller steps.
  This happens once per turn; a second cut-off in the same turn stops it
  with an error that says to raise `max_tokens`. The same applies when a
  tool call arrives with its arguments cut off.
- **A short reply that stops the work.** Right after tool work, a model
  sometimes plans the next step at length in its hidden reasoning and then
  sends only a short sentence with no tool call. AIDA answers such a reply
  with "Continue the task now" (at most twice per turn). It counts as a
  stall when the reply is short (300 characters or less) and either its
  reasoning is long compared with it (at least 3,000 characters and ten
  times the reply; measured stalls had ~15,000 characters of reasoning
  behind ~150 of reply), or, for models that send no reasoning, it is
  phrased as an upcoming action ("Let me check…", "Writing the dossier
  now.", or ending in a colon or ellipsis). A reply ending in a question
  mark, or a closing offer like "Let me know if you need anything else.",
  never counts. If the end of the reasoning contains a tool call written
  out in text that was never sent, the message says so and asks the model
  to send it. Turn it off with the Settings checkbox **Nudge the agent when
  it stalls mid-task**, or in `config.yaml`:

  ```yaml
  auto_continue_on_stall: false
  ```

To see why a turn ended, look in `aida.log`. Every round trip that ends a
turn logs one line with the stop reason (`stop`, or `length` for the
output limit), the reply and output-token sizes, and how many characters
of reasoning the model produced:

```text
round trip 37 ended: stop_reason=length, text=52 chars, tool_calls=0, output_tokens=64000, reasoning=171233 chars
```

A short reply right after tool work also logs the last 1,500 characters of
the reasoning (never added to the conversation), and whether it contains
tool-call markup, so a stall can be explained afterwards.

**A garbled tool name.** When a call names a tool that does not exist but
is close to one (`edit_file=path`, a misspelling), the reply names the
likely tool and its required arguments. The call itself is not run, since
its arguments may be incomplete.

(context-and-limits-indicator)=
## Reading the fullness indicator

```{figure} img/context-and-limits/context-and-limits-01-status-bar.png
:alt: The right-hand end of the AIDA status bar reading Session total: 0 in / 0 out (~$0.000 est.) and Context: 3k / 15k (20%), with circle 1 at the Session total label and circle 2 at the Context label.

The status bar after resuming a conversation on a local model with `context_window: 32768`:
(1) **Session total**, (2) **Context**. The budget (15k) is smaller than the 32k window: a 15 %
safety margin, the reply reserve and the tool schemas come off it first.
```

- **CLI**: a `[context] used / budget tokens (P%)` line after each turn.
- **GUI**: a **Context: Nk / Mk (P%)** label in the status bar, next to
  (not to be confused with) the **Session total:** label — that one is the
  cumulative token count/cost for the whole session and only ever grows;
  **Context:** is the current fullness of the window and goes back down
  after a compaction.

When it reads "Context: trimming disabled", `max_context_tokens` is set to `0`
(trimming/compaction off entirely) — not recommended for a long session,
since the only recovery left at that point is starting a new conversation.

(context-and-limits-long)=
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
5. For a long job over many items (proposals, samples, data files), give
   AIDA one item per message ("continue with the next proposal from
   PROGRESS.md") and have it keep its progress in a file. Each message is a
   turn that compaction can summarize as a whole. A repeating
   [workflow](../guides/workflows.md) step does the same without you
   typing: it starts each pass from a clean context and carries state in a
   progress file. A larger window only postpones the problem.
