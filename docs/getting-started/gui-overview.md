(gui-overview)=
# GUI overview

> **Status: 1.1.1.** In daily use at APS beamlines 12-ID/9-ID. Config
> formats and CLI commands are stable: from here, a breaking change to
> either means a new major version, and every change is dated in
> [`CHANGELOG.md`](https://github.com/jilavsky/aida/blob/main/CHANGELOG.md). See [`TODO.md`](https://github.com/jilavsky/aida/blob/main/TODO.md) for what
> is still open.

**Related:** [workspaces.md](../guides/workspaces.md) · [providers-and-secrets.md](../reference/providers-and-secrets.md) · [safety-and-permissions.md](../reference/safety-and-permissions.md) · [mcp-servers.md](../reference/mcp-servers.md) · [coding-and-scripting.md](../guides/coding-and-scripting.md) · [knowledge-bases.md](../guides/knowledge-bases.md)

This is a map of the `aida-gui` window — what's where, and which doc covers
it in depth. It's not a manual; each item below links to the file that
explains its fields and behavior.

(gui-overview-window)=
## The window

```{figure} img/gui-overview/gui-overview-01-window.png
:alt: The AIDA main window. Menu bar and toolbar across the top with the User, Workspace and Profile selectors, New Chat, Code Editor and the red Documentation button. Below, three columns: the conversations sidebar on the left; in the middle the conversation title, a user question about Unified fits, a collapsed line reading 2 tool calls, an I(Q) plot returned by pyIrena and the start of the answer, with the input box under it; on the right the collapsible Folders, MCP Servers, Skills and Quick Tasks sections. The status bar reads Ready, local-ollama. Numbered circles mark the parts described in the legend.

The AIDA window resuming a conversation with pyIrena: a question, two tool calls collapsed into
one line, the I(Q) plot the tool returned, and the answer. (The conversation was written into a
scratch database for the screenshot; no model was running.)
```

1. **Menu bar**: File, View, Configure and Help. See [Menus](#gui-overview-menus).
2. **User selector**: who new conversations are labelled with.
3. **Workspace selector**: the workspace this conversation runs in.
4. **Profile selector**: the provider profile (model) answering.
5. **New Chat** and **Code Editor…**.
6. **Documentation**: opens these pages in your browser. Items 2–6 are described under
   [Toolbar](#gui-overview-toolbar).
7. **Conversations sidebar**: user filter, search, past conversations by date, and the
   Resume / Delete… / Rename… / Clean Up… buttons. See
   [Conversations sidebar](#gui-overview-sidebar).
8. **Conversation title strip**: the name, with workspace, profile and user underneath. See
   [Conversation title strip](#gui-overview-title-strip).
9. **Transcript**: messages, tool calls collapsed into one line ("2 tool calls"), and images
   returned by tools. See [Tool calls in the transcript](#gui-overview-tool-calls).
10. **Input box** with **Send** and **Attach…**; **Stop** appears here while a turn runs. See
    [Stopping a turn](#gui-overview-stopping).
11. **Session column**: the active workspace's live state, in collapsible sections. See
    [Main area](#gui-overview-main-area).
12. **Status bar**: session status, token use and cost, context use. See
    [Status bar](#gui-overview-status-bar).

(gui-overview-toolbar)=
## Toolbar (left to right)

- **User selector** — an editable box naming who (or what) new
  conversations are labelled with — a person on a shared machine, or a
  project. Type a name and press Return to start using a new one. First on
  purpose: the three selectors read left to right in the order the choices
  narrow each other. See
  [organizing-conversations.md](../guides/organizing-conversations.md).
- **Workspace selector** — a dropdown of configured workspaces, plus
  "(no workspace)". Switching asks for confirmation and starts a new
  conversation in the chosen workspace. See [workspaces.md](../guides/workspaces.md).
- **Profile selector** — a dropdown of configured provider profiles.
  Switching mid-conversation is allowed and keeps history. See
  [providers-and-secrets.md](../reference/providers-and-secrets.md).
- **New Chat** — starts a fresh conversation in the current workspace/profile
  without switching either; the old conversation stays saved in the sidebar.
- **Code Editor…** — opens a syntax-highlighted Python editor (Save/Save
  As/Run/Kill) pre-filled from a chat code block, or blank. See
  [coding-and-scripting.md](../guides/coding-and-scripting.md).

That is the whole toolbar, and it is deliberately short: everything on it
is part of running a session. The seven configuration dialogs that used to
sit here — MCP Servers, Knowledge Bases, Providers, Workspaces, Workflows,
Schedules, Settings — are in the **Configure** menu instead (see below), so
a day at the beamline never needs anything but the left-hand end of this
bar.

At the right-hand end, separated from the rest:

- **Documentation** (red) — opens the AIDA documentation in your web
  browser. Deliberately the same shape and corner as pyIrena's, so it is
  recognisable before anything has been read. The same link is under
  **Help → Documentation**.

(gui-overview-menus)=
## Menus

- **File → Open Config / Records / Scratch / Skills Folder** — opens each
  of AIDA's own folders in the system file browser, so you never have to
  hunt for them. The config, scratch and skills folders live under
  `~/.aida/`, which most file browsers hide by default; this is the way in.
  See [mcp-servers.md](../reference/mcp-servers.md#adding-editing-via-the-gui) for what
  goes in the skills folder.
- **File → Open Conversation Folder** — the current conversation's
  attachment folder. See [documents.md](../guides/documents.md).
- **File → Export Conversation As…** — write a standalone snapshot of the
  current conversation's Markdown transcript to any folder (an Obsidian
  vault, say), with its own choice of how much tool-result text to include.
  Independent of the transcript this conversation already keeps up to date
  in the configured Records folder — see
  [`moving-and-sharing.md`](../guides/moving-and-sharing.md). The conversations
  sidebar's right-click menu has the same **Export…** for any past
  conversation, not just the open one.
- **File → Manage Users…** — rename a label (renaming onto an existing name
  merges them), or clear one. Also **New User…**, which starts using a
  name. See [organizing-conversations.md](../guides/organizing-conversations.md).
% CHECK(Jan): the File menu also has "Export Setup…" and "Import Setup…" (between "Export
% Conversation As…" and "Compact Conversation"); they are not listed here. Covered in
% guides/moving-and-sharing.md?
- **File → Compact Conversation** — summarize older turns to free context.
  See [context-and-limits.md](../reference/context-and-limits.md).
- **File → Save Conversation as Workflow…** — turn the prompts you have
  already sent into a stored workflow. See [workflows.md](../guides/workflows.md).
- **View → Conversations Column / Session Column / Reset Column Widths** —
  show or hide either side column, or put all three back to their default
  widths. The way back from a column you dragged shut.
- **View → Tool Calls → Hidden / Grouped / Expanded** — how much of the
  agent's tool use the transcript shows. See "Tool calls in the transcript"
  below.
- **Configure → Workspaces… / Providers… / MCP Servers… / Knowledge
  Bases…** — the four objects a session is built out of: a workspace picks
  a provider profile, an MCP group and its knowledge bases. See
  [workspaces.md](../guides/workspaces.md),
  [providers-and-secrets.md](../reference/providers-and-secrets.md),
  [mcp-servers.md](../reference/mcp-servers.md) and
  [knowledge-bases.md](../guides/knowledge-bases.md).
- **Configure → Workflows… / Schedules…** — a stored sequence of prompts,
  and the in-app scheduler that runs one unattended (with each schedule's
  last-run status). See [workflows.md](../guides/workflows.md).
- **Configure → Settings…** (`Ctrl+,`) — font size, records folder, log
  level, max agent iterations, assistant name and personal context,
  scheduler timings, and which model reads scanned PDF pages. See
  [documents.md](../guides/documents.md#scanned-pdfs) for that part. On
  macOS this one is in the application menu, **AIDA → Settings…** (`⌘,`),
  where Qt puts it to match the platform convention.
- **Help → Documentation** — opens this documentation in your web browser.
- **Help → About AIDA** — version and project link.

(gui-overview-main-area)=
## Main area

The window splits into three columns:

- **Conversations sidebar** (left) — see below.
- **Chat column** (center) — a title strip, the message transcript, and
  the input box (attachments, drag-and-drop, cancel/send).
% CHECK(Jan): the session column has five collapsible sections, titled "Folders", "MCP Servers",
% "Skills", "Quick Tasks" and "Workspace Notes". Skills and Workspace Notes are not described
% below, and the order below (Folders, Quick Tasks, MCP) differs from the window.
- **Session column** (right) — the active workspace's live state:
  % CHECK(Jan): the section header in the window says "Folders"; the "Workspace permissions"
  % group-box title is cleared by CollapsibleSection, so that label is not visible.
  - **FolderDisplay** — labeled "Workspace permissions" in the UI, since it
    covers everything a session may touch, not just folders — shows the
    workspace's source folders, target folder, sidecar folder name, allowed
    commands, and Python interpreter, each editable in place with a "Save to
    Workspace" button — see [workspaces.md](../guides/workspaces.md) and
    [coding-and-scripting.md](../guides/coding-and-scripting.md) (for the command
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
    workspace's resolved MCP group and a checkbox per known server, laid
    out in two columns (names read down the left column and continue down
    the right, so an alphabetical list stays alphabetical). Each checkbox
    is a live control: ticking/unticking it actually starts or stops that
    server right now (not merely a preference for next session), and the
    checked state always reflects which servers are actually running,
    refreshed after every start/stop. A "MCP Servers…" button below the
    checkboxes opens the full management dialog. See
    [mcp-servers.md](../reference/mcp-servers.md).

(gui-overview-stopping)=
### Stopping a turn

While a turn runs, a red **Stop** button appears beside Send (`Esc` does the
same thing) and a line next to it counts the seconds. Stop is cooperative,
not a kill switch — it takes effect at the next safe point, which is one of:

- **between the model's tokens** — a model part-way through a long answer is
  cut off immediately and the text it had already produced is kept;
- **between tool calls** — the remaining calls in the batch are abandoned and
  recorded as cancelled;
- **before the next round trip** — no further step is started.

The one thing it cannot interrupt is a **tool call already in flight**. That
call runs to completion (bounded by the server's own `timeout_seconds` — see
[mcp-servers.md](../reference/mcp-servers.md)) and the stop lands the moment it returns;
killing it mid-call would leave an MCP server with an orphaned request and a
half-written file with no way to tell which. While that is what you are
waiting on, the label says so by name — *"Stopping… 12s — waiting for
pyirena_fit_unified"* — and the Stop button greys out, because pressing it
again genuinely does nothing.

A cancelled turn leaves a valid conversation: whatever the model had already
said is kept, every tool call it announced is answered, and you can send the
next message straight away. One side effect worth knowing: the token usage of
an aborted round trip is not counted, so the cost estimate in the status bar
slightly under-reports a session you stop often — the request was still
billed.

(gui-overview-tool-calls)=
### Tool calls in the transcript

When the agent goes off and does things — reading files, running a fit,
driving a browser — a whole run of those calls collapses into one grey
line in the transcript:

```
▸ 12 tool calls · 11 ✓ 1 ✗ · 8.4 s
```

That is twelve calls, eleven of which succeeded, taking 8.4 seconds
between them. While the turn is still running the line names the tool it
is waiting on instead (`⏳ 12 tool calls · running pyirena_plot_iq…`), so a
slow call still looks different from a hung one. Click the line to open
the run and see every call; click a call's **Details** for its full
arguments and result.

**View → Tool Calls** sets how much is shown by default, and the choice is
remembered between launches:

- **Hidden** — one dim `12 tool calls` line per run, no tally or timing.
  Reading a session rather than debugging it.
- **Grouped** (the default) — the summary line above.
- **Expanded** — every call visible, as its own row.

Switching mode is retroactive: the calls are always recorded, whatever the
mode, so **if a turn goes wrong, switch to Expanded and the whole failed
turn is right there to inspect** — nothing has to be re-run. That is the
one to reach for before sending a bug report.

(gui-overview-title-strip)=
### Conversation title strip

Above the transcript, a strip shows what this conversation is called, with
the workspace, provider profile and user label underneath it. The name is
written by the model from the conversation's own content and re-checked
every few turns — see
[organizing-conversations.md](../guides/organizing-conversations.md#how-conversations-get-their-names).

Click the ✎ (or double-click the strip) to name it yourself. A name you
type is never changed again.

(gui-overview-sidebar)=
## Conversations sidebar

Lists past conversations under date headings — **Today**, **Yesterday**,
**Previous 7 days**, then by month. Each entry is two lines: the title,
then when it was last used, its workspace and its user label. Recent
entries are timed rather than dated ("09:03", "Yesterday 17:44", "Sat
11:02"), so you can tell this morning's work from last March's at a
glance.

The buttons are **Resume**, **Delete…**, **Rename…**, and **Clean Up…**
(delete everything older than N days) — all with confirmation dialogs.
Double-clicking an entry resumes it. Resuming replays chat history and any
still-present image/file artifacts.

Above the list, a search box matches the title, workspace and user label,
plus the text of your messages and the agent's replies (not tool output;
this part runs once you pause typing, from three characters on), and — once any conversation carries a user label — a filter narrows the
list to one name, to **(no user)**, or to **All users**. The filter follows
the toolbar's User box when you switch, and otherwise leaves your choice
alone.

Right-clicking gives **Resume**, **Rename…**, **Export…**, **Move to User**
and **Delete…**; a multi-row selection gives **Move to User** and
**Delete…**. **Move to User** is how a conversation started under the wrong
name is put right. See [organizing-conversations.md](../guides/organizing-conversations.md).
**Export…** writes that conversation's transcript to a folder you pick,
without opening it — the same "Export Conversation As…" dialog as the File
menu's, see above.

(gui-overview-dialogs)=
## Dialogs reachable from the Configure menu

% CHECK(Jan): the Code Editor dialog at the end of this list opens from the toolbar, not the
% Configure menu; the Workflows… and Schedules… dialogs (Configure menu) are not listed.

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
  - **Transcript tool results** (Off/Summary/Full, default Full) — how much
    of a tool result's own text is written into every conversation's
    exported Markdown transcript. Off and Summary exist so a long
    instrument-run record reads like a lab notebook instead of a dump of
    every raw tool payload the agent needed; images and files a tool
    produced are always linked regardless of this setting.
- **MCP Servers dialog** (`mcp_management_dialog.py`) — add/edit/remove MCP
  servers, per-tool permissions, groups, skills, live start/stop/restart,
  connection tests, and a tool-call log. See [mcp-servers.md](../reference/mcp-servers.md).
  Its **Add pyIrena…** button is a one-click setup for pyIrena's MCP server:
  it finds the installation, shows you what it found, and on confirmation
  writes the server, its group, its skills, and its env vars —
  see [pyirena.md](../guides/pyirena.md).
- **Knowledge Bases dialog** (`knowledge_management_dialog.py`) — add/edit/
  remove RAG knowledge bases and build/update their indexes. See
  [knowledge-bases.md](../guides/knowledge-bases.md).
- **Providers dialog** (`profiles_dialog.py`) — add/edit/remove provider and
  embedding profiles. See [providers-and-secrets.md](../reference/providers-and-secrets.md).
- **Workspace Management dialog** (`workspace_management_dialog.py`) —
  add/edit/remove named workspaces (`workspaces.yaml`) from the GUI instead
  of hand-editing YAML. See [workspaces.md](../guides/workspaces.md).
- **Code Editor dialog** (`code_editor_dialog.py`) — write, save, and run
  Python scripts against the workspace's saved-scripts folder and
  interpreter. See [coding-and-scripting.md](../guides/coding-and-scripting.md).

(gui-overview-first-launch)=
## First launch

On a fresh install (no provider profiles configured), `aida-gui` opens an
onboarding dialog that walks you through creating a first provider profile
and workspace, so you don't have to hand-edit YAML to get started. It also offers **Add pyIrena MCP Tools…**, but only when pyIrena is
actually installed on the machine. Skipping any of it is fine — the same
settings are reachable from **Providers…**, **Workspaces…**, and
**MCP Servers…** at any time. Afterwards the app reopens the workspace and
profile you last used.

(gui-overview-status-bar)=
## Status bar

% CHECK(Jan): the status bar also shows a permanent "Context: Nk / Mk (P%)" label next to the
% cost (described in reference/context-and-limits.md), not mentioned here.

Shows session status messages (e.g. "Starting session…", "Ready — \<profile\>",
"Startup failed", or a transient "Context trimmed: dropped N old turns (~M
tokens now)" whenever old history is cut to fit the context window) and a
permanent token/cost label — input/output token counts and an estimated USD
cost, updated after every turn. Three buttons appear only when needed:
**⚠ N schedule failures** and **⏳ N jobs waiting** (see
[workflows.md](../guides/workflows.md)), and a red **⚠ Scheduler problem** when the
scheduler itself cannot poll, for example because a config file has a
syntax error; it clears itself once the next poll works.

If something went wrong while a chat was starting (a knowledge base
skipped, an MCP server that failed to start, more tools than the provider
accepts), an amber **"This chat started with N warnings"** notice appears
in the chat itself, and the "Ready" message says so.

The conversation's title is generated after the answer is complete, so the
input box is free straight away; a message you send while the title is
still being generated waits a moment for it. Confirmation
prompts (for actions gated by the safety model, workspace switches, deletes,
etc.) appear as modal dialogs, not in the status bar — see
[safety-and-permissions.md](../reference/safety-and-permissions.md).
