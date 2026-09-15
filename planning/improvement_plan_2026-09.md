# AIDA — Review findings & improvement plan (2026-09-15)

Scope: full read of `src/aida/` at commit `39a54c8` (core, providers, mcp,
workspace, coding, documents, persistence, ui/qt), `PLAN.md`, the August
review, and a full non-UI test run in a clean Python 3.11 environment
(`1520 passed, 1 failed` — the one failure is the known
`test_rebuild_reports_a_folder_it_cannot_actually_list` root/chmod artifact,
same as in August). `ruff check` is clean.

**State of the project is very good.** The layering rules hold, the
async/Qt bridge is careful, every past bug report has a test pinned to it,
and the docs are unusually honest about limits. Nothing below is a crash or
a data-loss bug. The consistent theme this time is different from August's
("the configuration surface is the weak spot"): **the engine is solid; what
the model *sees* from tools is sized wrong in both directions.** Several
independent caps were each set sensibly in isolation and together produce
one class of failure: the model gets too little from an MCP tool and too
much from a script, and in either case the turn quietly degrades. These are
the items most likely to bite a beamline user who is not watching the log.

Ordered by value ÷ effort. Everything in §1 is a small, contained change
with an obvious test.

---

## 1. Fix first — small, high value (each ≤ half a day)

- [x] **P1 — MCP tool results are silently cut at 4,000 characters.**
      `McpManager._call_tool` renders every text/JSON block through
      `describe_for_model(a)` with `aida.artifacts.policy.DEFAULT_MAX_CHARS
      = 4000` (~1,000 tokens), appends `... [truncated]`, and *nothing keeps
      the rest*: `ToolCallRecord.content_preview` keeps 2,000 chars for the
      inspector, the recorder persists the truncated string, and there is
      no offset/pagination the model could ask for. Every pyIrena metadata
      dump, every `list_files` over a real run folder, and — most visibly
      for you — every Playwright accessibility snapshot of the ESAF form is
      longer than that. The model then reasons from the first screenful and
      guesses the rest, which looks like a "dumb model" rather than a cap.
      Contrast: `read_file` gets `INTERACTIVE_MAX_CHARS = 100_000` for the
      same kind of content.
      **Fix:** raise the MCP-path cap to ~24–32k chars (6–8k tokens), and
      when a result still exceeds it, write the full text to
      `scratch_dir/tool-results/<call_id>.txt` and append `"[N chars
      omitted — full result saved to <path>; use read_file to see it]"`.
      The scratch folder is already always-allowed for reads, so the model
      can page through it with the existing `read_file` and no new tool is
      needed. One constant, one file write, one test.

- [x] **P1 — Script/command output goes to the model uncapped (up to 512 KB).**
      The opposite problem. `aida.coding.runner.MAX_CAPTURED_BYTES =
      256_000` per stream is a *memory* cap, but `_format_run_result` feeds
      the whole captured stdout *and* stderr straight into the tool result
      — up to ~128k tokens from one `run_python_script`. A reduction script
      that prints per-iteration fit progress, or a `print(df)` on a big
      frame, blows the entire context in one call; the provider 400s
      ("prompt is too long"), the turn dies with an opaque `AgentError`,
      and — because `_trim_context` only runs at the *start* of a turn —
      the user has to send another message before anything recovers.
      **Fix:** same shape as above — keep the 256 KB capture, but hand the
      model head+tail of ~12–16k chars and spill the full output to the
      scratch folder with a pointer. `_BoundedCapture` already knows how to
      keep head and tail; reuse it with a second, smaller limit.

- [x] **P1 — Anthropic `max_tokens` default of 4096 truncates tool-call
      arguments, not just replies.** `anthropic_.DEFAULT_MAX_TOKENS = 4096`
      applies when the profile leaves `max_tokens` unset (the documented
      default). A reply that hits it shows the `TruncationNotice` — fine.
      But a `write_file`/`write_markdown_report` whose *content argument*
      is a 3,500-word report also hits it: the stream ends
      `stop_reason=max_tokens` mid-`input_json_delta`, `json.loads` fails,
      the call is issued as `{"_unparsed_arguments": "..."}`, the tool does
      `arguments["path"]`, and the model receives the error text `'path'`
      (a bare `KeyError` string — `wrap_tool_errors` doesn't cover
      `KeyError`, so `AgentLoop`'s blanket handler does `str(exc)`). The
      model retries the same call and hits the same wall. This is the
      likeliest reason a "write the report" turn loops or gives up.
      **Fix (three small pieces):** (1) raise `DEFAULT_MAX_TOKENS` to 8192
      (every current Claude model accepts it; 16384 is also safe for the
      3.5+/4 family); (2) in `AgentLoop._run_turns`, when a tool call's
      arguments carry `_unparsed_arguments`, don't execute it — answer it
      with an error result that says *why* ("arguments were cut off by the
      output token limit; shorten the content or split the write"), so the
      model can act on it; (3) in `AgentLoop`, format tool exceptions as
      `f"{type(exc).__name__}: {exc}"` — `'path'` alone is unreadable, and
      some exceptions stringify to an empty string.

- [x] **P1 — MCP tool-call timeout is a hard-coded 60 s.**
      `McpServerHandle.DEFAULT_CALL_TIMEOUT_SECONDS = 60.0`, and
      `start_session` constructs `McpManager` without passing one.
      `_KNOWN_SERVER_KEYS` has no timeout key, so it cannot be set from
      `mcp.json` either. A pyIrena size-distribution or modeling run over a
      folder, or a Playwright wait on a slow ANL page, can exceed 60 s and
      the tool is reported as failed while the server is still working
      (and, for a stdio server, may then answer a request nobody is waiting
      for). `script_timeout_seconds` is already per-workspace; this is the
      same knob missing for MCP.
      **Fix:** add `timeout_seconds` (per server) to `McpServerConfig` /
      `_KNOWN_SERVER_KEYS`, thread it through `McpManager._handle_kwargs`,
      expose it in the Add/Edit Server form. Keep 60 as the default.

- [x] **P2 — Conversation history is never prompt-cached on Anthropic.**
      `to_cached_system_param` and `to_cached_tools_param` set two
      breakpoints (system, last tool). The message list gets none. Within
      a single turn every tool round-trip re-sends the *entire* history
      uncached — a 20-call analysis turn pays for the history 20 times
      over, on the exact path where history is largest. Anthropic allows
      four breakpoints; the standard pattern is one on the last message
      block so the whole prefix is cached and each round trip only pays
      for the new tool result. `UsageInfo.cache_read_input_tokens` is
      already plumbed to the meta line, so the win is immediately visible.
      **Fix:** in `AnthropicProvider.complete`, after `to_anthropic_params`,
      add `cache_control` to the last content block of the last message
      (turn it into a list-of-blocks if it is a bare string). ~10 lines,
      one translation test. Worth checking once that Argo passes it
      through — the fact that `cache_read_input_tokens` shows up today
      suggests it does.

- [x] **P2 — `fetch_url` reads only the first 80 KB of raw HTML and sends
      no User-Agent.** `_fetch_sync` does `response.read(DEFAULT_FETCH_MAX_CHARS
      * 4)` (80,000 bytes) *before* stripping `<script>`/`<style>`. A modern
      article page is 200 KB–1 MB with the body after a large inlined
      head, so the 20k-char text the model gets is mostly nav and cookie
      banners with the article missing; and urllib's default UA gets a 403
      from many publishers and from anything behind Cloudflare.
      **Fix:** read up to ~4 MB, extract text, *then* cap at 20k chars;
      send a browser-like `User-Agent`. Two lines.

- [x] **P2 — Attaching an image to a profile with `supports_vision: false`
      silently drops the pixels.** `MainWindow._augment_with_attachments`
      adds an `ImageRef` regardless; `select_images_within_cap` is only
      consulted when `supports_vision` is true, so the model receives
      `[image artifact ...: image/png, N bytes]` and answers "I can't see
      images". Nothing in the GUI says why. A beamline user attaching a
      plot to ask "does this look right?" hits this on the first day with
      an Ollama profile. **Fix:** when an image is attached and the active
      profile has `supports_vision` false, show a status-bar/inline notice
      ("this profile has vision disabled — the image will be described,
      not seen; enable *Supports vision* in Providers…"). One check in
      `_on_send_requested`.

## 2. Usability — small GUI additions a beamline user will feel

- [ ] **Keyboard shortcuts.** The only shortcut in the whole main window is
      Ctrl+Return on Send (`grep setShortcut` finds nothing else). Cheap
      and expected by anyone coming from Claude/ChatGPT desktop: **Esc →
      Stop** while a turn runs; **Ctrl/Cmd+N → New Chat**; **Ctrl/Cmd+, →
      Settings**; **Ctrl/Cmd+Shift+C → Compact Conversation**;
      **Ctrl/Cmd+L → focus the input box**. All `QAction.setShortcut`
      calls on actions that already exist.
      Nit found on the way: `_InputTextEdit`'s docstring says "Shift+Enter
      (or Ctrl+Enter) inserts a newline", but Ctrl+Return is also the Send
      button's shortcut and `QTextEdit` does not consume Ctrl+Return, so
      Ctrl+Enter *sends*. Behaviour is fine (two ways to send); fix the
      docstring or drop the button shortcut so the two agree.

- [ ] **Prompt history recall (Up/Down in an empty input box).** Beamline
      work is repetitive — "plot the last 5 files", "summarize this run"
      typed many times a day. Keep the last ~50 prompts of the session in
      `InputBox` (in memory, or in `AppConfig` if it should survive a
      restart) and let Up/Down cycle them when the cursor is on the first/
      last line, exactly like a shell. ~40 lines in `_InputTextEdit.
      keyPressEvent`. Pairs well with the existing Quick Tasks panel
      without overlapping it (Quick Tasks are curated; this is recent).

- [ ] **"Continue" button on the two cut-off notices.** Both the
      `TruncationNotice` (`stop_reason == "length"`) and the
      `ErrorBanner` for `iteration cap reached (N)` end a turn the user
      almost always wants to resume. A button that sends "Please continue
      where you left off." is one `QPushButton` connected to
      `MainWindow._on_send_requested`. (After the §1 `max_tokens` fix this
      triggers far less often, but the iteration cap still will.)

- [ ] **Search inside conversations, not just titles.** The sidebar filter
      matches `title`, `workspace_name`, `user` only (`_matches`). "Which
      chat did I analyze sample X in?" needs a `LIKE` over
      `messages.content` — one extra query in `ConversationStore`
      (`search_conversations(term)`) and the sidebar merging its ids into
      the visible set when the filter text is ≥ 3 chars. SQLite FTS5 is
      the upgrade if it ever feels slow; a `LIKE` over a few thousand rows
      is instant.

- [ ] **Mid-turn context check.** `_trim_context` runs once, before the
      turn. With `max_agent_iterations` now 50, a single turn can grow by
      hundreds of thousands of characters of tool results and only the
      *next* user message triggers a trim. Give `AgentLoop` an optional
      `before_round_trip` async hook; `ChatSession` passes `_trim_context`
      (it only ever drops whole *older* turns, so the running turn is
      safe). Yields the same `ContextTrimmed` event the status bar already
      renders. Medium-small; becomes important once §1's two result caps
      are in place and no single call can blow the window anymore.

## 3. Hardening — cheap, do when touching the file anyway

- [ ] **`ChatBridge.is_busy` has a theoretical race.** `send()` assigns
      `self._turn_future` on the Qt thread *after*
      `run_coroutine_threadsafe` returns, while `_drain`'s `finally` sets
      it to `None` on the loop thread. If `_drain` finishes before the
      assignment lands — only realistic when `session.send` raises
      `SessionBusyError` synchronously on its first `__anext__`, e.g. Send
      pressed during a manual compaction — `is_busy` stays `True` forever
      and every later Enter is routed to `queue_user_message` on a turn
      that isn't running. Unlikely (the GIL switch interval usually saves
      it) but the fix is one line: `return self._turn_future is not None
      and not self._turn_future.done()`.

- [ ] **SQLite WAL mode.** `db.connect` sets `busy_timeout` but leaves the
      default rollback journal. The GUI already has two connections from
      two threads (session writer + sidebar reader), and the per-user
      beamline layout makes two AIDA processes on one `~/.aida` a real
      possibility (PLAN §2.1 notes it is unguarded). `PRAGMA
      journal_mode=WAL` lets readers never block the writer and vice
      versa, and makes the "database is locked" path far rarer. One
      `execute` after `connect`; WAL survives in the file so it is a
      one-time switch. (Only caveat: WAL needs the DB on a local disk, not
      a network share — worth a line in `docs/installation.md`.)

- [ ] **Scratch folder is never cleaned.** `ensure_scratch_dir` creates it;
      `persistence/cleanup.py` knows about records, attachments and
      orphans but not scratch. After §1 starts spilling tool results
      there it will grow faster. Add "scratch files older than N days" to
      the existing cleanup (`aida conversations cleanup` already has the
      age plumbing), default off or 30 days.

- [x] **`_unparsed_arguments` on the OpenAI path too.** Same handling as
      the P1 item above — `process_openai_chunk` and `finalize_stream`
      produce the same sentinel dict, so the `AgentLoop` fix covers both
      providers; just add an OpenAI-shaped test next to the Anthropic one.

- [ ] **Misnamed exception in `_start_session`.** A workspace that fails
      `validate_workspace` raises `UnknownProfileError(f"workspace ...")`.
      Functionally harmless (both land in `_STARTUP_ERRORS`), but a log
      grep for profile errors finds workspace problems. Add
      `InvalidWorkspaceError` or reuse `UnknownWorkspaceError`.

## 4. Considered, not recommended now (recorded so it is not rediscovered)

- **Parallel execution of a round-trip's tool calls.** `AgentLoop` runs
  `pending_tool_calls` sequentially even though `to_anthropic_params` goes
  out of its way to keep the model issuing parallel calls. `asyncio.
  gather` would be ~15 lines, but: confirmation prompts would stack up
  as modal dialogs, pyIrena's tools are mostly CPU-bound sync so a stdio
  server serializes them anyway, and event ordering in the transcript
  (`ToolCallGroup`) assumes finish-in-issue-order. Revisit only with a
  measured case where the server actually runs calls concurrently.
- **Tool-result pagination as a first-class tool (`read_tool_result`).**
  The scratch-spill in §1 gives 90% of the value with zero new schema,
  which matters given the 128-tool Argo cap.
- **FTS5 for conversation search.** `LIKE` first; upgrade only on
  measurement.

---

Everything in §1 and §2 is independent of everything else and of the
open Phase-10 distribution work, so it can go into `0.1.0b8` piecemeal.
If only one thing gets done, make it the two result-size items — they are
the same fix twice and they are the ones users cannot diagnose themselves.
