# Credentials, browser automation, and where secrets go

**Recorded 2026-08-28.** Analysis only — nothing here has been implemented,
and no decision has been taken. Written down because the findings are worth
keeping while the question is thought through.

**The question:** AIDA drives a web system (the APS ESAF form) through
Playwright MCP. Logging in by hand works. Could a future *automated* run log
in without the agent — and therefore the LLM provider, and AIDA's own on-disk
records — ever seeing the username and password?

---

## 1. Status quo, and the recommendation until something changes

Log in to ESAF by hand at the start of the day; Playwright MCP's persistent
browser profile keeps that session alive for a few hours; the agent works
inside it. **No credential is stored anywhere, and none passes through the
model.**

This is not a stopgap to be apologised for — it is the best available answer
for attended work, and it is what §3 below recommends anyway. The only thing
it does not cover is a run with nobody present when the session expires.

## 2. The constraint everything else follows from

The model chooses tool-call arguments. Anything passed as an argument is
therefore:

- in the conversation context,
- in the request sent to the LLM provider (and **resent on every subsequent
  turn** of that conversation),
- and persisted by AIDA (§6).

So "hide the password from the agent" cannot mean masking it at the display
layer. It means keeping it out of the tool-call path entirely. Every workable
method below does exactly that; the difference between them is *how*.

## 3. Method 1 — session reuse (works today, no code, no stored credential)

Playwright MCP runs a **persistent browser profile by default**: cookies and
localStorage survive between runs. A human logs in once; later runs inherit an
authenticated session and the agent simply finds itself logged in.

| Flag | Effect |
|---|---|
| `--user-data-dir <path>` | Profile location. Default: `~/Library/Caches/ms-playwright/mcp-{channel}-{workspace-hash}` (macOS), `%USERPROFILE%\AppData\Local\ms-playwright\...` (Windows), `~/.cache/ms-playwright/...` (Linux). |
| `--storage-state <path>` | Loads cookies + localStorage from a JSON file into an isolated context — the explicit form of the same idea. |
| `--isolated` | In-memory profile; state is lost when the browser closes. |
| `--extension` / `--cdp-endpoint <endpoint>` | Attach to an already-running real browser instead of launching one. |

**Consequences to understand:**

- **The profile directory is a bearer credential.** Anyone who can read it can
  act as the user until the session expires. It deserves the protection a
  password gets, and it lives in a cache folder nobody thinks to protect.
- **Sessions expire** — hours to weeks. This is the entire wall between
  attended and unattended operation.
- **One browser instance per profile.** Concurrent runs against the same
  profile conflict.
- **Credential secrecy is not authorization.** An agent inside an
  authenticated session can do anything the user can do in that system. If
  the concern is blast radius rather than password disclosure, this method
  does not address it at all — and `--extension` is considerably worse, since
  it exposes every logged-in site in the user's normal browser.

## 4. Method 2 — a non-interactive credential, for genuinely unattended runs

The right shape for automation is not "store my password secretly" but "issue
a scoped, revocable, auditable credential that is not a human's login": an API
token, a service account, OAuth client credentials. Injected as an environment
variable into the MCP server process, it never enters the model's context.

**AIDA already implements the whole storage-and-injection half of this:**

- `mcp.json` env values of the form `keyring:NAME` or `secret:NAME` are
  resolved from the OS keychain at subprocess-spawn time by
  `resolve_env_secrets` in `src/aida/mcp/server.py`. The resolved value goes
  only into the child process environment — it is never logged, never written
  back to `mcp.json`, and never reaches the model.
- Storage and rotation: `aida config secret set/get/delete`, backed by the OS
  keychain under service name `aida`, with an `AIDA_SECRET_*` environment
  override for headless use (`src/aida/config/secrets.py`).
- The GUI's MCP server form has a "Store Value in Keychain…" button that
  writes the secret and rewrites the env entry as a reference.

**The gap is the consuming side.** Playwright MCP has no tool that accepts a
credential — it drives a browser. So env injection only helps if the target
system exposes an API, or if a small purpose-built MCP server is written.

## 5. Method 3 — a credential broker, if a form login must be automated

The shape: `fill_secret(field_ref, "esaf-password")` — the model names *which
field* and *which named secret*; the tool resolves the value from the keychain
and types it. The model sees `"esaf-password"` and never the value.

What it would cost, and what it does not solve:

- **Needs a wrapper MCP server that owns the browser, or a fork of Playwright
  MCP.** Microsoft's server offers no such primitive.
- **Residual leak that defeats naive designs:** Playwright MCP's accessibility
  snapshot has serialized `<input type="password">` values as plaintext into
  the LLM context — [issue #1566](https://github.com/microsoft/playwright-mcp/issues/1566),
  now closed, but the fix version was not confirmed. One snapshot taken
  between filling and submitting puts the password into the context anyway.
  Any broker design must forbid snapshots in that window, and the fix must be
  verified against the version actually installed.
- **The MCP server process sees the plaintext regardless.** Trust is being
  extended to that process, not only to the model.

## 6. Where a secret lands in AIDA today

Traced through the code on 2026-08-28. **There is no redaction anywhere in
AIDA** — no logging filter, no key-name denylist, no secret-wrapper type. If a
secret ever passes through a tool argument, or comes back in a tool result, it
reaches all of these:

| Destination | Persistent? | Cite |
|---|---|---|
| `~/.aida/aida.db`, `messages.tool_calls_json` — plaintext JSON. Mode 0644: no `chmod` anywhere in the codebase | **forever** | `persistence/store.py`, `persistence/db.py` |
| The outbound provider request (`input` / `function.arguments`), **resent every subsequent turn** | network | `providers/anthropic_.py`, `providers/openai_compat.py` |
| `~/.aida/logs/aida.log` + 5 rotated copies, and the console. At DEBUG always; and at **WARNING — live at the default INFO level** — on two paths: a call to an unknown tool, and a tool that raised | **forever** | `core/agent.py`, `config/logging_setup.py` |
| GUI tool-call "Details" pane — full arguments and result, re-rendered from the DB every time the conversation is resumed | on screen | `ui/qt/tool_call_widget.py`, `ui/qt/chat_panel.py` |
| CLI stdout and shell scrollback | terminal | `cli/chat.py` |
| MCP management dialog → Log tab → raw-result view, **with a Copy-to-clipboard button** | session | `mcp/server.py` (`ToolCallRecord`), `ui/qt/mcp_management_dialog.py` |
| The confirmation dialog / prompt text for any tool in `confirm_tools` — the full argument dict is interpolated into it | on screen | `mcp/manager.py`, `ui/qt/main_window.py` |
| For tool **results** additionally: the Markdown transcript under `~/Documents/Aida/` — likely a synced or backed-up folder | **forever** | `persistence/records.py` |

The path from the current situation to that table needs no new feature: **a
page snapshot taken after login fields are filled is a tool result**, and
takes the provider → SQLite → transcript route permanently. This is true
today.

The one existing masking feature (`KEY=***` in the MCP server env editor and
in `aida mcp show`) is display-only and covers server *config* env, not tool
arguments or results.

## 7. The ESAF-specific consideration

The ESAF login page asks for the **Argonne domain** username and password —
an institutional AD credential, not an application password. Two consequences:

- No storage mechanism is likely to satisfy Argonne cyber for a domain
  credential, whatever its technical merits.
- If ESAF sits behind SSO with MFA, unattended login is blocked outright
  regardless of how the password is handled.

The realistic unattended path is therefore to ask APS whether ESAF exposes an
API or can issue a service account — a much easier conversation than "let an
AI agent type my domain password", and it lands squarely in Method 2, which
AIDA already supports.

## 8. Open questions — for thinking about, not answered here

- Is unattended operation actually required, or is "log in each morning,
  agent works for a few hours" sufficient in practice?
- Does ESAF (or the other beamline systems in scope) have an API or a
  service-account path?
- What is Argonne cyber's actual position on an agent operating inside an
  authenticated domain session — separate from the credential question, since
  Method 1 grants full user-equivalent access without storing anything?
- Should AIDA's leak paths (§6) be closed regardless of what is decided about
  credentials? They are a hazard today, independent of this feature — the
  cheap parts are: never log argument *values* (key names only), keep full
  arguments out of the confirm-dialog text, and tighten file modes on
  `~/.aida/aida.db` and the logs.
- If a broker (§5) is ever built, does it belong in AIDA as a native tool, or
  in a separate purpose-built MCP server that owns its own browser?

## Sources

- [Playwright MCP README](https://raw.githubusercontent.com/microsoft/playwright-mcp/main/README.md) — profile and connection flags
- [Playwright MCP — Profile & State](https://playwright.dev/mcp/configuration/user-profile)
- [microsoft/playwright-mcp issue #1566](https://github.com/microsoft/playwright-mcp/issues/1566) — password values in accessibility snapshots
