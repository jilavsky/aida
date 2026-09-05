# GUI overview

> **Status: beta (0.1.0b4).** Phases 1–10 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [workspaces.md](workspaces.md) · [providers-and-secrets.md](providers-and-secrets.md) · [safety-and-permissions.md](safety-and-permissions.md) · [mcp-servers.md](mcp-servers.md) · [coding-and-scripting.md](coding-and-scripting.md) · [knowledge-bases.md](knowledge-bases.md)

This is a map of the `aida-gui` window — what's where, and which doc covers
it in depth. It's not a manual; each item below links to the file that
explains its fields and behavior.

## Toolbar (left to right)

- **User selector** — an editable box naming who (or what) new
  conversations are labelled with — a person on a shared machine, or a
  project. Type a name and press Return to start using a new one. First on
  purpose: the three selectors read left to right in the order the choices
  narrow each other. See
  [organizing-conversations.md](organizing-conversations.md).
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
- **Providers…** — opens the provider/embedding profiles dialog (add/edit/
  remove profiles). See [providers-and-secrets.md](providers-and-secrets.md).
- **Workspaces…** — opens the workspace management dialog (add/edit/remove
  named workspaces). See [workspaces.md](workspaces.md).
- **Workflows…** — opens the stored-workflow dialog (add/edit/run a saved
  sequence of prompts). See [workflows.md](workflows.md).
- **Schedules…** — opens the in-app scheduler dialog, with each schedule's
  last-run status. See [workflows.md](workflows.md).
- **Settings…** — opens the settings dialog (font size, records folder, log
  level, max agent iterations, assistant name and personal context,
  scheduler timings, and the optional document-OCR key). See
  [documents.md](documents.md#optional-mistral-ocr) for the OCR part.

At the right-hand end, separated from the rest:

- **Documentation** (red) — opens the AIDA documentation in your web
  browser. Deliberately the same shape and corner as pyIrena's, so it is
  recognisable before anything has been read. The same link is under
  **Help → Documentation**.

## Menus

- **File → Open Config / Records / Scratch Folder** — opens each of AIDA's
  own folders in the system file browser, so you never have to hunt for
  them.
- **File → Open Conversation Folder** — the current conversation's
  attachment folder. See [documents.md](documents.md).
- **File → Manage Users…** — rename a label (renaming onto an existing name
  merges them), or clear one. Also **New User…**, which starts using a
  name. See [organizing-conversations.md](organizing-conversations.md).
- **File → Compact Conversation** — summarize older turns to free context.
  See [context-and-limits.md](context-and-limits.md).
- **File → Save Conversation as Workflow…** — turn the prompts you have
  already sent into a stored workflow. See [workflows.md](workflows.md).
- **Help → Documentation** — opens this documentation in your web browser.
- **Help → About AIDA** — version and project link.

## Main area

The window splits into three columns:

- **Conversations sidebar** (left) — see below.
- **Chat column** (center) — the message transcript plus the input box
  (attachments, drag-and-drop, cancel/send).
- **Session column** (right) — the active workspace's live state:
  - **FolderDisplay** — labeled "Workspace permissions" in the UI, since it
    covers everything a session may touch, not just folders — shows the
    workspace's source folders, target folder, sidecar folder name, allowed
    commands, and Python interpreter, each editable in place with a "Save to
    Workspace" button — see [workspaces.md](workspaces.md) and
    [coding-and-scripting.md](coding-and-scripting.md) (for the command
    allowlist and interpreter).
  - **Quick Tasks** — a per-workspace list of short, reusable prompt
    templates for routine jobs ("summarize today's scans in the source
    folder", "fit every dataset with a Unified level and tabulate Rg").
    Double-click one to drop its text into the input box — it is never sent
    automatically, so you can fill in a sample name or scan number first
    (if the input box already has unsent text, AIDA asks before replacing
    it). Right-click for **Add… / Edit… / Delete…**; up to 10 per
    workspace. They are saved into the active workspace immediately
    (`quick_tasks:` in `workspaces.yaml`), so they follow the workspace,
    not the conversation.
  - **McpQuickPanel** — labeled "MCP Servers" in the UI — shows the
    workspace's resolved MCP group and a checkbox per known server. Each
    checkbox is a live control: ticking/unticking it actually starts or
    stops that server right now (not merely a preference for next session),
    and the checked state always reflects which servers are actually
    running, refreshed after every start/stop. A "MCP Servers…" button below
    the checkboxes opens the full management dialog. See
    [mcp-servers.md](mcp-servers.md).

## Conversations sidebar

Lists past conversations (date, workspace, title) with **Resume**,
**Delete…**, **Rename…**, and **Clean Up…** (delete everything older than N
days) — all with confirmation dialogs. Double-clicking an entry resumes it.
Resuming replays chat history and any still-present image/file artifacts.

Above the list, a search box matches the title, workspace and user label,
and — once any conversation carries a user label — a filter narrows the
list to one name, to **(no user)**, or to **All users**. The filter follows
the toolbar's User box when you switch, and otherwise leaves your choice
alone.

Right-clicking gives **Resume**, **Rename…**, **Move to User** and
**Delete…**; a multi-row selection gives **Move to User** and **Delete…**.
**Move to User** is how a conversation started under the wrong name is put
right. See [organizing-conversations.md](organizing-conversations.md).

## Dialogs reachable from the toolbar

- **Settings dialog** (`settings_dialog.py`) — font size and log level take
  effect immediately, no restart needed; also sets the records folder and
  max agent iterations, and shows the configured provider profiles. Two
  fields there shape every conversation in every workspace:
  - **Assistant name** — what the agent calls itself (default "Aida").
  - **Personal context** — a few lines about you and your work ("I run the
    USAXS instrument at APS 9-ID; data are HDF5 written by the beamline
    pipeline"). It is prepended to the system prompt of every session, so
    you don't have to repeat it in each workspace's own system prompt.
    Empty by default — nothing about you is sent until you fill it in.
- **MCP Servers dialog** (`mcp_management_dialog.py`) — add/edit/remove MCP
  servers, per-tool permissions, groups, skills, live start/stop/restart,
  connection tests, and a tool-call log. See [mcp-servers.md](mcp-servers.md).
  Its **Add pyIrena…** button is a one-click setup for pyIrena's MCP server:
  it finds the installation, shows you what it found, and on confirmation
  writes the server, its group, its skills, and its env vars —
  see [pyirena.md](pyirena.md).
- **Knowledge Bases dialog** (`knowledge_management_dialog.py`) — add/edit/
  remove RAG knowledge bases and build/update their indexes. See
  [knowledge-bases.md](knowledge-bases.md).
- **Providers dialog** (`profiles_dialog.py`) — add/edit/remove provider and
  embedding profiles. See [providers-and-secrets.md](providers-and-secrets.md).
- **Workspace Management dialog** (`workspace_management_dialog.py`) —
  add/edit/remove named workspaces (`workspaces.yaml`) from the GUI instead
  of hand-editing YAML. See [workspaces.md](workspaces.md).
- **Code Editor dialog** (`code_editor_dialog.py`) — write, save, and run
  Python scripts against the workspace's saved-scripts folder and
  interpreter. See [coding-and-scripting.md](coding-and-scripting.md).

## First launch

On a fresh install (no provider profiles configured), `aida-gui` opens an
onboarding dialog that walks you through creating a first provider profile
and workspace, so you don't have to hand-edit YAML to get started. It also offers **Add pyIrena MCP Tools…**, but only when pyIrena is
actually installed on the machine. Skipping any of it is fine — the same
settings are reachable from **Providers…**, **Workspaces…**, and
**MCP Servers…** at any time. Afterwards the app reopens the workspace and
profile you last used.

## Status bar

Shows session status messages (e.g. "Starting session…", "Ready — \<profile\>",
"Startup failed", or a transient "Context trimmed: dropped N old turns (~M
tokens now)" whenever old history is cut to fit the context window) and a
permanent token/cost label — input/output token counts and an estimated USD
cost, updated after every turn. Confirmation
prompts (for actions gated by the safety model, workspace switches, deletes,
etc.) appear as modal dialogs, not in the status bar — see
[safety-and-permissions.md](safety-and-permissions.md).
