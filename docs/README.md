# AIDA documentation

> **Status: beta (0.1.0b1).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in the release notes.
> See [`PLAN.md`](../PLAN.md) for what is still planned.

Task-oriented setup and configuration guides, one file per subsystem. Each
covers both the CLI and GUI way to configure that subsystem — most things
in AIDA can be done either way. For the design rationale behind a
subsystem (why it works this way), see [`PLAN.md`](../PLAN.md); for fully
commented example config files, see [`examples/config/`](../examples/config/).

| File | What you'll set up there |
|---|---|
| [`installation.md`](installation.md) | Install AIDA, run `aida doctor`, understand the `~/.aida/` layout |
| [`providers-and-secrets.md`](providers-and-secrets.md) | LLM/embedding provider profiles (Ollama, OpenAI, Claude, ANL Argo), API keys in the OS keychain |
| [`workspaces.md`](workspaces.md) | Named workspaces: source/target folders, which profile/MCP servers/skills a workspace uses |
| [`safety-and-permissions.md`](safety-and-permissions.md) | What AIDA is allowed to read/write/run without asking, and what always asks |
| [`mcp-servers.md`](mcp-servers.md) | Adding MCP servers (pyIrena, bait_mcp, web search, ...), groups, per-tool permissions |
| [`coding-and-scripting.md`](coding-and-scripting.md) | Letting the agent run Python/shell commands, code templates, the Code Editor |
| [`knowledge-bases.md`](knowledge-bases.md) | RAG: indexing your own documents so the agent can search them |
| [`gui-overview.md`](gui-overview.md) | A spatial tour of the desktop app — what's where |
| [`pyirena.md`](pyirena.md) | Using AIDA with pyIrena: one-click MCP setup, and sharing an environment between the two packages |

New to AIDA? Start with `installation.md`, then `providers-and-secrets.md`,
then `workspaces.md` — that's enough to have a working chat session. The
rest are opt-in features you can add as you need them.

## Not implemented yet

So you don't go looking for them: `aida run` (headless one-shot execution),
stored named workflows, and the scheduler are Phase 10 — `aida run` prints a
"not yet implemented" message today. Remote (HTTP) MCP servers, voice
input/output, and an alternative web frontend are parked ideas, not planned
work. See [`PLAN.md`](../PLAN.md).
