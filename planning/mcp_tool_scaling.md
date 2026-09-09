# MCP tool scaling — pyIrena-mcp and beyond

**Status: design discussion, nothing committed to code.** Written after a
real bug report chain on `usaxscontrol`: a chat message failed with `Error
code: 400 - ... 'array_above_max_length'` because the session's combined
tool count hit 137, above the ANL Argo gateway proxy's 128-tool cap on its
`tools` array. Root cause, traced live: pyIrena-mcp alone registers ~110
tools, and it was active in that session's MCP group. See `CHANGELOG.md`
(Unreleased, "A very large combined tool count...") for the stopgap already
shipped — an advisory warning in `aida.core.session` once the combined tool
count crosses 100, naming `McpServerConfig.disabled_tools` as the fix. This
doc is the follow-up: `disabled_tools` unchecking one tool at a time across
~110 entries is not a workable curation UI, and it doesn't reduce the
per-turn token cost of whichever tools stay enabled. Companion doc, other
side of the seam: pyIrena's own
[`planning/ai-agent/01-api-and-mcp-extensions.md`](../../pyirena/planning/ai-agent/01-api-and-mcp-extensions.md)
(the design that produced the current tool set and its category
structure).

## The problem, precisely

Three related but distinct pains:

1. **Hard cap.** Argo's proxy rejects any request whose `tools` array
   exceeds 128 — a proxy-side limit, not a documented Anthropic/OpenAI one,
   so it's invisible talking to a provider directly and only shows up
   through Argo.
2. **Per-turn cost.** Every enabled tool's schema is sent on *every* turn
   and counted against `history_budget` (`aida.core.context`) whether or
   not that turn uses it. A big MCP group is a standing tax, not a one-time
   cost.
3. **Curation UX.** The only lever today, `McpServerConfig.disabled_tools`
   (`aida/config/settings.py`), is a flat per-tool list. Workable for a
   handful of tools; not for ~110.

## What's already true, that makes this easier than it looks

pyIrena's tool names already encode a clean, intentional taxonomy — this
isn't naming-convention archaeology, it matches the categories pyIrena's
own `01-api-and-mcp-extensions.md` designed on purpose:

```
pyirena_read_*                    ~11   read-only result readers
pyirena_ctrl_waxs_*               ~16   WAXS peak-fit control
pyirena_ctrl_sizes_*              ~13   size-distribution control
pyirena_ctrl_simple_*             ~15   simple-fit control
pyirena_ctrl_modeling_*           ~16   unified/population modeling control
pyirena_ctrl_*  (session/level)   ~20   open/close session, unified-fit levels
pyirena_list_files / inspect_file / summarize_* / tabulate_*   ~5   discovery
pyirena_plot_*                     ~2   plotting
```

More importantly: `pyirena.api.control.schemas` already has
`TOOL_SCHEMAS` / `TOOL_SCHEMA_BY_NAME`, a ready-made Anthropic-style schema
registry for every control tool (`pyirena/api/control/__init__.py`'s own
docstring shows `TOOL_SCHEMAS` passed straight to `client.messages.create`).
The `pyirena_ctrl_*` MCP tools are thin wrappers around
`pyirena.api.control.*` functions — "the contract is the `pyirena.api`
function, not the MCP tool or the JSON schema" per that doc's design
principles. A name → callable map is the only missing piece; the hard part
(schemas, function boundary) is already done. This changes the cost
estimate for Tier 2 below from "new subsystem" to "a handful of new MCP
tools wired to infrastructure that already exists."

## Options

### Tier 1 — Category-aware curation UI (shipped)

Implemented as `aida.mcp.tool_grouping.group_tool_names` (pure,
Qt-free, unit-tested in `tests/test_mcp_tool_grouping.py`): a **generic**
longest-common-prefix-then-recursive-bucket algorithm over each tool
name's `_`-separated tokens — not a `pyirena_ctrl_`-specific regex, so any
future MCP server with a similar `<namespace>_<category>_<verb>` naming
habit gets sensible categories for free, and a small/flat server's tool
list renders exactly as before (no header at all). `McpManagementDialog`'s
Tools tab (`src/aida/ui/qt/mcp_management_dialog.py`) renders each
category as a collapsible `_ToolCategoryHeader` (starts collapsed, arrow
to expand) with a tri-state "All enabled" checkbox that bulk-toggles the
category's `_ToolPermissionRow`s in one click — deliberately only the
*Enabled* checkbox, never "Confirm before run", which stays a per-tool
choice. Same `disabled_tools`/`confirm_tools` config underneath — no
pyIrena change needed, no protocol change; `_on_save_tool_permissions` is
untouched, since every tool is still a real `_ToolPermissionRow` under the
hood regardless of grouping.

For pyIrena's real ~110 tools this produces `ctrl/waxs` (18), `ctrl/sizes`
(16), `ctrl/simple` (15), `ctrl/modeling` (18), `read` (11), and several
smaller categories — validated against the actual tool name list in
`tests/test_mcp_tool_grouping.py`'s `PYIRENA_TOOL_NAMES` fixture.

This fixes pain (3) only — curation UX. It does not reduce pain (1) (the
128-tool cap) or pain (2) (per-turn schema-token cost) if a workspace's
selected categories still add up to a large combined count; those are
what Tier 2 is for.

### Tier 2 — Meta-tool / dispatcher pattern (parked: design separately)

Collapse most of pyIrena's surface into a handful of tools:

- `pyirena_list_categories()` → category names + tool counts
- `pyirena_list_tools(category)` → names + one-line descriptions
- `pyirena_describe_tool(name)` → full JSON schema (straight from
  `TOOL_SCHEMA_BY_NAME`)
- `pyirena_call(name, arguments)` → validates against that schema,
  dispatches to the real `pyirena.api.control` function, returns its result

A session then sees a handful of pyIrena tools regardless of how large
pyIrena's real tool count grows — this is the only option that removes
pains (1) and (2), not just (3). Keep session-lifecycle tools
(`open_dataset`, `list_open_sessions`, `close_session`,
`get_session_summary`) always visible at the top level rather than behind
the dispatcher, since nearly every control workflow starts there.

**Placement:** build this in pyIrena-mcp, not as a generic AIDA-side proxy.
pyIrena already has the taxonomy and the schema registry; a pyIrena-side
fix also benefits every other MCP client talking to it (Claude Desktop,
anything else), not just AIDA. A generic "AIDA flattens any oversized MCP
server" proxy is a plausible future generalization if a *different* server
hits this same wall, but building it speculatively now, before a second
case exists, is exactly the premature-abstraction trap — revisit only if
that second case shows up.

**Design choice: fixed dispatcher, not dynamic tool lists.** An
alternative is having "activate category" change what `tools/list`
reports server-side, pushing a `notifications/tools/list_changed` and
having AIDA's `McpManager` re-fetch mid-session. Nothing in `McpManager`
handles that notification today (checked: `start_all()`/`start_server()`
call `list_tools()` exactly once, at startup) — wiring it up is real new
work in AIDA's MCP client, plus a second question of whether whatever
builds each provider request re-reads `session.tools` fresh per turn
(needs verifying) rather than freezing it at session start. The fixed
4-tool dispatcher needs none of that: `pyirena_call` works for any tool
name without the advertised tool list ever changing. Start there; revisit
dynamic lists only if the fixed dispatcher's discovery round-trips prove
too costly in practice.

**Two things AIDA needs regardless of where the dispatcher logic lives:**

- **Tool-call transparency.** `ToolCallRow` (`aida.ui.qt.tool_call_widget`)
  shows the real tool name today. A generic `pyirena_call(name="pyirena_
  ctrl_waxs_run_fit", ...)` would render as "pyirena_call" unless AIDA
  unpacks the inner `name` argument for display. Small, but needed to keep
  the chat transcript legible/debuggable — the exact opposite of the
  "diagnostics are a feature" principle this codebase already follows
  elsewhere (see `ErrorBanner`'s docstring).
- **`confirm_tools` gating.** `McpServerConfig.confirm_tools` matches on
  the real MCP tool name today. Behind a dispatcher, every call arrives as
  `pyirena_call` — AIDA would need to match against the `name` *argument*
  instead to decide whether to prompt before running it. This is
  safety-relevant, not just cosmetic: some `pyirena_ctrl_*` calls
  plausibly mutate live session/instrument-adjacent state, and losing
  per-tool confirm granularity silently would be a regression, not a
  simplification. Needs a decision before implementation, not after.

**Open questions to resolve before implementation:**
- Exact category boundaries for the dispatcher — same as pyIrena's own
  taxonomy above, or coarser/finer?
- Does `pyirena_call`'s error shape stay pyIrena's existing "errors are
  data, `{"error": ..., "suggestion": ...}`" convention (per
  `01-api-and-mcp-extensions.md` design principle 6)? Almost certainly
  yes — no reason to diverge.
- Where does the name → callable map live — generated from the existing
  `pyirena.api.control` imports in `pyirena/api/control/__init__.py`, or
  maintained by hand alongside `TOOL_SCHEMA_BY_NAME`?
- Do the non-`ctrl_` tools (`pyirena_read_*`, discovery, plotting) go
  behind the same dispatcher, a second one, or stay top-level (they're
  fewer and don't share `TOOL_SCHEMA_BY_NAME`'s registry today)?

### Tier 3 — Embedding-based automatic tool retrieval (not needed yet)

Reuse AIDA's existing RAG/embedding infrastructure to select the top-K
relevant tool schemas per turn instead of a human or the agent picking a
category explicitly. Scales to many large MCP servers at once, but adds a
new silent-failure mode (the wrong tools get retrieved) and real
engineering weight. pyIrena's tools already have a clean human-legible
taxonomy, which is exactly the case Tier 2 handles well without retrieval.
Parked; revisit only if Tier 2 turns out insufficient at a larger scale
(many big MCP servers active at once, not just pyIrena).

## Decision log

| Decision | Status |
|---|---|
| Tier 1 (category-aware curation UI) | **Shipped** — `aida.mcp.tool_grouping`, `_ToolCategoryHeader` in `mcp_management_dialog.py` |
| Tier 2 (fixed dispatcher, built in pyIrena-mcp) | Agreed direction, design not finalized — see open questions above |
| Tier 3 (embedding retrieval) | Parked, no near-term need |
| Generic AIDA-side proxy for *any* oversized MCP server | Rejected for now — no second concrete case yet; revisit if one appears |
