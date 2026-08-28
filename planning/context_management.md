# Context-window management and compaction

**Status: planned, not implemented.** Written 2026-08-28 after an audit of
what AIDA does today. Tracked from `PLAN.md` §1.3.

**Why this matters:** the target workload is a long analysis conversation
driving pyIrena MCP — tens of tool calls, each returning dense numeric
results. Running out of context halfway through such a session is the worst
possible failure: the conversation is dead, the reasoning so far is lost, and
the only recovery is starting over. Today AIDA will hit that wall silently,
and on a 128k local model it can be over the real limit before the first
message is sent.

---

## 1. What exists today

- One **global** `AppConfig.max_context_tokens`, default `120_000`
  (`src/aida/config/settings.py`), editable in `config.yaml` and in
  Settings… → "Max context tokens". `0` disables trimming.
- `ChatSession._trim_context()` (`src/aida/core/session.py`) calls
  `trim_history` (`src/aida/core/context.py`) before each turn, which drops
  the **oldest whole turns** until the estimate fits, always keeping every
  system message and at least `min_recent_turns=4` recent turns.
- A `ContextTrimmed` event (`src/aida/core/events.py`) is emitted when it
  actually drops something: an 8-second status-bar message in the GUI, a
  `[context]` line in the CLI.
- The DB and the Markdown transcript are **never** trimmed — trimming only
  affects what is sent to the provider.

## 2. What is wrong with it

**(a) The budget ignores most of what is actually sent.** `trim_history` sums
only the message list. Tool schemas are passed to `provider.complete()`
separately and are never counted. Measured against real pyirena-mcp on
2026-08-28:

```
68 tools -> 40,785 chars of JSON schema ~= 10,200 tokens (AIDA's own estimator)
```

sent on **every request**, invisible to the budget. Vision images
(`Message.images`) are not counted either.

**(b) The estimator undercounts exactly the wrong content.**
`estimate_tokens` is `len(text) // 4`. That is a fair average for English
prose; JSON and dense numeric data tokenize closer to ~3 chars/token, so tool
arguments and tool results — the bulk of a pyIrena session — are
systematically **underestimated**.

**(c) One global number for models with 128k / 256k / 1M windows.** The
default 120k is unsafe on a 128k model (see (a)) and wastes ~88% of a 1M
window. Switching profiles mid-session does not change the budget.

**(d) Trimming discards; it never summarizes.** Anything decided early in a
conversation is simply gone from the model's view, with no trace left behind.

**(e) Running out is unrecoverable in-app.** If trimming cannot save the
request (disabled, the 4-turn floor, or the uncounted schemas), the provider
rejects it and AIDA shows `[provider] API error (400)`. No assistant message
is appended, so history stays *valid* — but it only grows, so every
subsequent turn in that conversation fails identically. There is no
`/compact`, no `/clear`, no "drop oldest N". The only escape is New Chat.

**(f) No visibility.** The status-bar token counter is the **cumulative
session total**, not context fullness; it rises monotonically and says
nothing about how close the wall is. `max_context_tokens` is not mentioned
anywhere in `docs/`.

---

## 3. Design

### 3.1 Per-profile context window

Add to `ProviderProfile` (`src/aida/config/settings.py`):

```python
context_window: int | None = None   # the model's TOTAL context window, in tokens
```

- `None` falls back to `AppConfig.max_context_tokens`, so every existing
  config behaves exactly as it does today. Same opt-in shape as B2's
  `max_tokens`/`temperature`.
- Coerce in `from_dict` with the existing
  `_coerce_optional_number(source, "context_window", ..., kind=int)`; add to
  `to_dict`.
- Note the naming trap for reviewers: `max_tokens` is the **output** cap;
  `context_window` is the **total** window. They are not the same field and
  must never be conflated.

### 3.2 Budget arithmetic

New helper in `src/aida/core/context.py`:

```python
def history_budget(
    *, context_window: int, reserved_output_tokens: int,
    tool_schema_tokens: int, safety_fraction: float = CONTEXT_SAFETY_FRACTION,
) -> int
```

```
usable  = int(context_window * CONTEXT_SAFETY_FRACTION)   # 0.85
budget  = usable - reserved_output_tokens - tool_schema_tokens
```

- `CONTEXT_SAFETY_FRACTION = 0.85` — a named module constant, documented as
  covering estimator error (§2b) plus the provider's own per-request
  overhead. One knob, not five.
- `reserved_output_tokens` = the profile's `max_tokens` when set, else
  `DEFAULT_RESERVED_OUTPUT_TOKENS = 4096` (Anthropic's own default; a
  reasonable stand-in for OpenAI-compat, where output is otherwise
  unbounded).
- If `budget` comes out below `MIN_HISTORY_BUDGET` (say 8000), that is a
  misconfiguration — a window too small for the enabled tool set. Log a clear
  warning naming the tool-schema cost and the group, and clamp to the
  minimum rather than trimming to nothing.

### 3.3 Count what is actually sent

In `src/aida/core/context.py`:

- `estimate_tool_schema_tokens(tools: list[ToolSchema]) -> int` —
  `json.dumps` of each schema's `{name, description, parameters}`, summed
  through the dense estimator. Recomputed per turn (the tool set can change
  mid-session when a server is started or stopped from the MCP dialog); it is
  a `json.dumps` plus a `len`, so cost is irrelevant.
- `estimate_tokens_dense(text)` — same shape as `estimate_tokens` but
  `DENSE_CHARS_PER_TOKEN = 3` — used for tool-call arguments and
  `role="tool"` content inside `estimate_message_tokens`.
- Image cost in `estimate_message_tokens`: `IMAGE_TOKEN_ESTIMATE * len(message.images)`,
  with `IMAGE_TOKEN_ESTIMATE = 1600` (roughly a 1024px image at Anthropic's
  ~(w×h)/750 rule — AIDA already downscales to ~1024px in
  `aida/providers/vision.py`). Document the derivation in the constant's
  comment.

Deliberately **no tokenizer dependency**. `tiktoken` is the wrong tokenizer
for Claude and for Qwen anyway, and would add a dependency for false
precision; the safety fraction is the honest way to absorb the error. Revisit
only if real sessions show the estimate off by more than the fraction covers.

### 3.4 Compaction

When the budget is exceeded, summarize the turns that would be dropped
instead of discarding them.

New in `src/aida/core/context.py`:

```python
COMPACTION_PROMPT: str          # the instruction given to the model
def compaction_request_messages(turns: list[list[Message]]) -> list[Message]
def compaction_summary_message(summary_text: str) -> Message
```

and in `ChatSession` (`src/aida/core/session.py`), an
`async def _compact_context(self, turns) -> Message | None`.

Mechanics:

1. `trim_history` already identifies which whole turns would go. Split it so
   the caller can get *both* the kept messages and the dropped turns (return
   the dropped list, or add a `plan_trim()` companion — either is fine, keep
   `trim_history`'s existing signature working for its current tests).
2. Send the dropped turns to the **active provider** via the ordinary
   `provider.complete()` with **no tools** and a low temperature, collecting
   `TextFinished`. No new provider API is needed.
3. Replace those turns with **one** `role="user"` message beginning with a
   clear header, e.g. `# Summary of earlier conversation (compacted)`.
   A user-role message is the safe choice across both API dialects — a
   synthetic assistant message risks confusing tool-call pairing.
4. `COMPACTION_PROMPT` should ask for *facts, not prose*: files and folders
   touched, parameter values and fit results with their numbers, decisions
   taken and why, and anything the user asked to remember. Explicitly ask it
   to preserve exact filenames and numeric values, since those are what a
   pyIrena session needs to keep.

Failure policy: if the summarization call errors, **fall back to today's
behavior** — drop the turns — and say so in the event. Compaction failing
must never fail the user's turn.

Persistence (deliberate v1 choice): **do not** change the schema. The DB
stays the complete, untrimmed record, matching the existing rule that
"recorded history is never trimmed". On resume, `ChatSession` loads the full
history and compacts lazily if it is over budget — costing one summarization
call when reopening a long conversation. The alternative (persisting the
summary and a compacted-range marker) is a schema change and can wait until
the lazy path proves too slow.

Manual trigger: `/compact` in the CLI REPL (`src/aida/cli/chat.py`, alongside
`/profile` and `/max-iterations`) and a **Compact Conversation** action in the
GUI, so the user can compact at a natural task boundary instead of waiting
for the automatic trigger mid-thought.

Events: extend `ContextTrimmed` (`src/aida/core/events.py`) with
`summarized: bool = False` and `summary_tokens: int = 0`. Extending keeps the
existing CLI and GUI wiring; both display sites just need wording that
distinguishes "summarized N turns" from "dropped N turns".

### 3.5 Visibility

- **GUI**: a context-fullness indicator in the status bar —
  `Context: 42k / 88k (48%)` — next to, not replacing, the cumulative usage
  label. Rename the existing one so the two cannot be confused
  (`Session total:` vs `Context:`).
- **CLI**: the same figure on the `[usage]` line after each turn.
- **`aida doctor`**: warn when a configured profile has no `context_window`
  set, and when `AppConfig.max_context_tokens` exceeds the smallest
  configured profile's window. Same "informative, not a hard FAIL" shape as
  the pyIrena check.

---

## 4. Steps

Each step is independently shippable and testable; ship in order.

### Step 1 — measure honestly

Fixes the "silently over the real window" bug on its own, with no new config.

1. Add `estimate_tokens_dense`, `IMAGE_TOKEN_ESTIMATE`,
   `estimate_tool_schema_tokens` to `src/aida/core/context.py`; use the dense
   estimator for tool-call arguments and `role="tool"` content, and count
   images, inside `estimate_message_tokens`.
2. Thread the live tool set into `ChatSession._trim_context()` (it already
   has `self.tools`) and subtract `estimate_tool_schema_tokens` from the
   budget.
3. Tests in `tests/test_context.py`: a schema list of known size estimates as
   expected; a tool-heavy message costs more than its `content` alone; an
   image-bearing message costs `IMAGE_TOKEN_ESTIMATE` more.

### Step 2 — per-profile `context_window`

4. Field + `from_dict`/`to_dict` in `src/aida/config/settings.py` using
   `_coerce_optional_number`.
5. `history_budget()` + constants in `src/aida/core/context.py`;
   `_trim_context` resolves `self.profile.context_window` first and falls back
   to `settings.app.max_context_tokens`.
6. GUI: a `context_window` row in `ProfileFormDialog`
   (`src/aida/ui/qt/profiles_dialog.py`) — reuse the existing
   `_OptionalNumberRow`, as `max_tokens` does.
7. `aida doctor` check (`src/aida/cli/doctor.py`).
8. Tests: `tests/test_settings.py` (round-trip, bad value rejected, absent =
   fallback), `tests/test_context.py` (`history_budget` arithmetic including
   the clamp), `tests/ui/test_profiles_dialog.py`, `tests/test_doctor.py`.

### Step 3 — visibility

9. Fullness indicator in `src/aida/ui/qt/main_window.py` and on the CLI's
   `[usage]` line in `src/aida/cli/chat.py`; disambiguate the existing
   cumulative label.
10. Tests: `tests/ui/test_main_window.py`, `tests/test_chat_cli.py`.

### Step 4 — compaction

11. `COMPACTION_PROMPT`, `compaction_request_messages`,
    `compaction_summary_message` in `src/aida/core/context.py`; expose the
    would-be-dropped turns from the trim path.
12. `ChatSession._compact_context` + wire into `send()`; extend
    `ContextTrimmed`; fall back to plain trimming on failure.
13. `/compact` in the CLI REPL; **Compact Conversation** in the GUI.
14. Tests: `tests/test_context.py` (prompt construction, summary message
    shape) and `tests/test_chat_cli.py` / a new `tests/test_compaction.py`
    driving `MockProvider` — note that the summarization call **consumes a
    scripted `MockTurn`**, so those tests must script one extra turn;
    a failing summarization falls back to dropping; the resulting history is
    still valid for `repair_tool_call_pairing`.

### Step 5 — docs

15. `docs/providers-and-secrets.md`: `context_window` in the profile field
    table, with the `max_tokens` distinction called out explicitly.
16. A short `docs/context-and-limits.md`: what the window is, why MCP tool
    schemas consume it (cite the measured ~10k for pyIrena's 68 tools and
    link to `mcp-servers.md` on lean groups), what compaction does, how to
    read the fullness indicator, and what to do when a conversation gets long.
17. Cross-link from `docs/mcp-servers.md` and `docs/pyirena.md`.

---

## 5. Verification

- Unit tests above, all green on 3.11/3.13 across the three CI OSes.
- **Measured check, not just unit tests:** with pyirena-mcp enabled, start a
  session and confirm the reported budget is `context_window × 0.85` minus
  ~10k of schemas minus the output reservation — i.e. that the ~10,200-token
  schema cost is visibly accounted for. The script used to measure it on
  2026-08-28 built an `McpManager`, called `start_all()`, and ran
  `to_anthropic_tools()` output through `estimate_tokens`.
- **A real long-session run**: a pyIrena analysis conversation driven past
  the budget on a small local model, confirming it compacts and continues
  rather than dying, and that filenames and fit parameters survive the
  summary.
- Confirm an existing `providers.yaml` with no `context_window` anywhere
  behaves exactly as before.

## 6. Deliberately not doing (for now)

- **A real tokenizer.** Wrong tokenizer for the models in use, a new
  dependency, false precision. The safety fraction absorbs the error.
- **Persisting the compacted history.** The DB stays the full record; resume
  re-compacts lazily. Revisit if that call becomes annoying.
- **A separate cheap "utility profile" for summarization.** Uses the active
  profile for now; worth revisiting if summarizing on a 1M-window cloud model
  proves expensive.
- **Per-workspace context settings.** The window is a property of the model,
  so it belongs on the profile.
