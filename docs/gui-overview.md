# GUI overview

> **Status: pre-alpha.** Config formats and CLI commands may change without
> notice until Phase 5. See [`PLAN.md`](../PLAN.md) for the full roadmap.

**Related:** [workspaces.md](workspaces.md) · [providers-and-secrets.md](providers-and-secrets.md) · [safety-and-permissions.md](safety-and-permissions.md) · [mcp-servers.md](mcp-servers.md) · [coding-and-scripting.md](coding-and-scripting.md) · [knowledge-bases.md](knowledge-bases.md)

This is a map of the `aida-gui` window — what's where, and which doc covers
it in depth. It's not a manual; each item below links to the file that
explains its fields and behavior.

## Toolbar (left to right)

- **Workspace selector** — a dropdown of configured workspaces, plus
  "(no workspace)". Switching asks for confirmation and starts a new
  conversation in the chosen workspace. See [workspaces.md](workspaces.md).
- **Profile selector** — a dropdown of configured provider profiles.
  Switching mid-conversation is allowed and keeps history. See
  [providers-and-secrets.md](providers-and-secrets.md).
- **New Chat** — starts a fresh conversation in the current workspace/profile
  without switching either; the old conversation stays saved in the sidebar.
- **Code Editor…** — opens a syntax-highlighted Python editor (Save/Save
  As/Run/Kill) pre-filled from a chat code block, or blank. See
  [coding-and-scripting.md](coding-and-scripting.md).
- **MCP Servers…** — opens the MCP management dialog. See
  [mcp-servers.md](mcp-servers.md).
- **Knowledge Bases…** — opens the knowledge base management dialog. See
  [knowledge-bases.md](knowledge-bases.md).
- **Settings…** — opens the settings dialog (font size, records folder, log
  level, max agent iterations, profile list view).

## Main area

The window splits into three columns:

- **Conversations sidebar** (left) — see below.
- **Chat column** (center) — the message transcript plus the input box
  (attachments, drag-and-drop, cancel/send).
- **Session column** (right) — the active workspace's live state:
  - **FolderDisplay** shows the workspace's source folders, target folder,
    sidecar folder name, allowed commands, and Python interpreter, each
    editable in place with a "Save to Workspace" button — see
    [workspaces.md](workspaces.md) and
    [coding-and-scripting.md](coding-and-scripting.md) (for the command
    allowlist and interpreter).
  - **McpQuickPanel** shows the workspace's resolved MCP group and a
    checkbox per known server — which servers this workspace would use next
    session. See [mcp-servers.md](mcp-servers.md).

## Conversations sidebar

Lists past conversations (date, workspace, title) with **Resume**,
**Delete…**, **Rename…**, and **Clean Up…** (delete everything older than N
days) — all with confirmation dialogs. Double-clicking an entry resumes it.
Resuming replays chat history and any still-present image/file artifacts.

## Dialogs reachable from the toolbar

- **Settings dialog** (`settings_dialog.py`) — font size and log level take
  effect immediately, no restart needed; also sets the records folder and
  max agent iterations, and shows the configured provider profiles.
- **MCP Servers dialog** (`mcp_management_dialog.py`) — add/edit/remove MCP
  servers, per-tool permissions, groups, skills, live start/stop/restart,
  connection tests, and a tool-call log. See [mcp-servers.md](mcp-servers.md).
- **Knowledge Bases dialog** (`knowledge_management_dialog.py`) — add/edit/
  remove RAG knowledge bases and build/update their indexes. See
  [knowledge-bases.md](knowledge-bases.md).
- **Code Editor dialog** (`code_editor_dialog.py`) — write, save, and run
  Python scripts against the workspace's saved-scripts folder and
  interpreter. See [coding-and-scripting.md](coding-and-scripting.md).

## Status bar

Shows session status messages (e.g. "Starting session…", "Ready — \<profile\>",
"Startup failed") and a permanent token/cost label — input/output token
counts and an estimated USD cost, updated after every turn. Confirmation
prompts (for actions gated by the safety model, workspace switches, deletes,
etc.) appear as modal dialogs, not in the status bar — see
[safety-and-permissions.md](safety-and-permissions.md).
