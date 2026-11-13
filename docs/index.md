# AIDA

AIDA (AI Data Assistant) is a local scientific agent workbench: a chat agent with file, script,
MCP, knowledge-base and workflow tools, used at APS beamlines 12-ID/9-ID. Install it with
`pip install aida-workbench[gui]`; run it as `aida` (command line) or `aida-gui` (desktop app).

> **Status: 1.1.0.** In daily use at APS beamlines 12-ID/9-ID. Config
> formats and CLI commands are stable: from here, a breaking change to
> either means a new major version, and every change is dated in the
> {doc}`changelog`. See [`TODO.md`](https://github.com/jilavsky/aida/blob/main/TODO.md) for what
> is still open.

:::{admonition} Start here
New to AIDA? Start with {doc}`getting-started/installation`, then
{doc}`reference/providers-and-secrets`, then {doc}`guides/workspaces`. That's enough to have a
working chat session. The rest are opt-in features you can add as you need them.
:::

Task-oriented setup and configuration guides, one page per subsystem. Each
covers both the CLI and GUI way to configure that subsystem — most things
in AIDA can be done either way. For the design rationale behind a
subsystem (why it works this way), see [`PLAN.md`](https://github.com/jilavsky/aida/blob/main/PLAN.md); for fully
commented example config files, see [`examples/config/`](https://github.com/jilavsky/aida/tree/main/examples/config).

| Page | What you'll set up there |
|---|---|
| {doc}`getting-started/installation` | Install AIDA, run `aida doctor`, understand the `~/.aida/` layout |
| {doc}`getting-started/concepts` | The ideas behind a workspace, and three setups that actually do something |
| {doc}`getting-started/gui-overview` | A spatial tour of the desktop app — what's where |
| {doc}`reference/providers-and-secrets` | LLM/embedding provider profiles (Ollama, OpenAI, Claude, ANL Argo), API keys in the OS keychain |
| {doc}`guides/workspaces` | Named workspaces: source/target folders, which profile/MCP servers/skills a workspace uses |
| {doc}`guides/skills` | Skills: single-file and folder skills, how they get switched on, shipping scripts with a skill |
| {doc}`reference/safety-and-permissions` | What AIDA is allowed to read/write/run without asking, and what always asks |
| {doc}`reference/mcp-servers` | Adding MCP servers (pyIrena, bait_mcp, web search, ...), groups, per-tool permissions |
| {doc}`reference/context-and-limits` | The model's context window, what fills it, compaction, and the fullness indicator |
| {doc}`guides/organizing-conversations` | User/project labels, sidebar filtering, and `{user}` paths |
| {doc}`guides/documents` | Attachment storage and cleanup, figure tools, and optional Mistral OCR |
| {doc}`guides/coding-and-scripting` | Letting the agent run Python/shell commands, code templates, the Code Editor |
| {doc}`guides/knowledge-bases` | RAG: indexing your own documents so the agent can search them |
| {doc}`guides/pyirena` | Using AIDA with pyIrena: one-click MCP setup, and sharing an environment between the two packages |
| {doc}`guides/bernardyn` | Using AIDA with Bernardyn: one-click MCP setup for its five publication-figure tools |
| {doc}`guides/axis-camera` | Axis network camera stills via `axis-cam-mcp`: build it, test on macOS, deploy to the beamline machine |
| {doc}`guides/workflows` | Headless `aida run`, stored named workflows, and the in-app scheduler (Phase 10) |
| {doc}`guides/moving-and-sharing` | Exporting your setup to another machine or a colleague, and importing one |

## Not implemented yet

So you don't go looking for them: OS-level scheduler installers
(launchd/Task Scheduler/systemd) were deliberately deferred in favor of the
in-app scheduler — see [Workflows](guides/workflows.md#why-no-os-level-scheduler).
Remote (HTTP) MCP servers, voice input/output, and an alternative web
frontend are parked ideas, not planned work. See [`PLAN.md`](https://github.com/jilavsky/aida/blob/main/PLAN.md).

```{toctree}
:maxdepth: 2
:hidden:

getting-started/index
guides/index
reference/index
changelog
```
