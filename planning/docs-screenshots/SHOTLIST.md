# Documentation screenshots: shot list

One row per screenshot in `docs/`. The ID is `<page>-<nn>-<what>`, where `<page>` is the page's
file name; the image lives next to the page at `<page dir>/img/<page>/<id>.png`.

- **automated: yes**: `python scripts/make_doc_screenshots.py --only <id>` makes it.
- **automated: no**: needs judgment (real data, a tricky state, an OS dialog). The page holds an
  invisible `% SCREENSHOT <id>: …` comment until the image exists; then replace the comment with
  a `figure` that has alt text and a caption, and set the status to `done`.
- **status**: `done` (image committed and shown), `todo` (placeholder only).

`tests/test_doc_screenshots.py` checks that this list, the pages, the images and the
`@shot` functions agree.

Column order: ID · page · dataset · state to set up · must show · automated? · status.

Inputs are under `scripts/doc_shots/data/` (seeded conversations as JSON, the returned plot PNG).
Every shot runs on a scratch `AIDA_HOME` with AIDA's `MockProvider`: no model, no network, no key.

## gui-overview

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| gui-overview-01-window | gui-overview | conversation-unified-overlay.json + 3 short ones | resume the seeded pyIrena conversation, user usaxs-team, 1600×940 | whole window, 12 numbered callouts matching the legend | yes | done |

## workspaces

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| workspaces-01-management | workspaces | standard settings | Workspaces dialog, saxs-analysis selected | list, Add/Edit/Remove, Details of saxs-analysis | yes | done |
| workspaces-02-edit-form | workspaces | standard settings + usaxs-notes KB | Edit Workspace form for saxs-analysis | the form from Name down to the Scripting group, OK/Cancel | yes | done |

## knowledge-bases

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| knowledge-bases-01-dialog | knowledge-bases | ollama-embed profile, usaxs-notes KB (not built) | Knowledge Bases dialog, usaxs-notes selected | list, Add/Edit/Remove/Rebuild/Update, Details | yes | done |
| knowledge-bases-02-add-form | knowledge-bases | same | the Add/Edit form on usaxs-notes | name, source folders, embedding profile, chunk size and overlap | yes | done |
| knowledge-bases-03-retrieval | knowledge-bases | two example passages (invented) | RetrievalRow, expanded | summary line, Details button, passages with file, heading, score, text | yes | done |

## workflows

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| workflows-01-dialog | workflows | daily-report and citation-watch from the page's YAML | Workflows dialog, daily-report selected | list, Add/Edit/Remove, details with vars and steps | yes | done |
| workflows-02-repeating-step | workflows | citation-watch | step editor on its repeating step | prompt, Expect files, Repeat until file, Max passes | yes | done |
| workflows-03-schedules | workflows | nightly-report (ok) and hourly-check (failed) runs written to the scratch DB | Schedules dialog, hourly-check selected | list, all buttons incl. Run Now, details with the failed status line | yes | done |

## skills

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| skills-01-panel | skills | all bundled sample skills installed | main window, standard settings; grab the Skills section | one checkbox per skill, pyirena-usage and saxs-basics ticked, Reload, Save to Workspace | yes | done |

## organizing-conversations

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| organizing-conversations-01-sidebar | organizing-conversations | 5 seeded conversations, users usaxs-team, saxs-student, none | main window, sidebar filter All users; grab the sidebar | filter (1), search (2), date headings, two-line entries incl. one without a label | yes | done |
| organizing-conversations-02-manage-users | organizing-conversations | same | Manage Users dialog, usaxs-team selected | labels with counts, active marker, New User / Rename or Merge / Clear Label | yes | done |
| organizing-conversations-03-move-to-user | organizing-conversations | same | sidebar right-click menu on the first entry, Move to User submenu open | both menus side by side | yes | done |

## installation

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| installation-01-onboarding | installation | none (empty scratch AIDA_HOME) | fresh install, no profiles | the first-run dialog; the MCP button depends on what is installed on the machine | yes | done |

## providers-and-secrets

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| providers-and-secrets-01-providers-dialog | providers-and-secrets | standard_settings() profiles | Configure → Providers…, claude-direct selected | tabs, list, buttons, details; 4 callouts | yes | done |
| providers-and-secrets-02-profile-form | providers-and-secrets | standard_settings() profiles | Edit… on claude-direct | the form, Secret ref set, Secret value empty | yes | done |

## mcp-servers

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| mcp-servers-01-dialog | mcp-servers | page-local mcp.json: pyirena (neutral command, not started) + http example | Configure → MCP Servers…, pyirena selected | server list, button column, tabs, Edit…; 7 callouts | yes | done |
| mcp-servers-02-server-form | mcp-servers | same | Edit… on pyirena | the form with env hidden, groups and skills ticked | yes | done |
| mcp-servers-03-tools-tab | mcp-servers | a real pyirena-mcp install | Start pyirena, open the Tools tab, tick a Confirm box | per-tool Enabled / Confirm before run checkboxes and Save Tool Permissions | no | todo |

## safety-and-permissions

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| safety-and-permissions-01-confirm-dialog | safety-and-permissions | none | SafetyGuard.authorize_write in a confirm-mode workspace's target folder; MainWindow._ask_confirmation with exec replaced by show-and-grab | the question, Allow / Allow for This Chat / Deny | yes | done |

## context-and-limits

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| context-and-limits-01-status-bar | context-and-limits | conversation-unified-overlay.json (as gui-overview-01) | resume on local-ollama (context_window 32768); crop the right end of the status bar | Session total and Context labels, 2 callouts | yes | done |

## documents

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| documents-01-attached | documents | placeholder /tmp/aida-docs/papers/silica-pdms-usaxs.pdf | fresh session; file attached, question typed, not sent | chat column: attachment chip in the input box, Attach… button, callouts 1–2 | yes | done |
| documents-02-settings-ocr | documents | none | Settings dialog on scratch config, no key | the Document OCR group: help text, key field, Verify key, Clear key | yes | done |

## coding-and-scripting

| ID | page | dataset | state to set up | must show | automated? | status |
|---|---|---|---|---|---|---|
| coding-and-scripting-01-open-in-editor | coding-and-scripting | conversation-script.json | resume the seeded conversation | the assistant bubble with a code block and its "</> Open in Editor" button (callout 1) | yes | done |
| coding-and-scripting-02-code-editor | coding-and-scripting | placeholder S1–S3_silica.h5 under /tmp/aida-docs/data/usaxs-2026-11 | Code Editor opened on saved_scripts/list_h5.py, Run clicked, finished | editor, button row, output pane with exit code and stdout; callouts 1–3 | yes | done |
| coding-and-scripting-03-scripting-group | coding-and-scripting | none | workspace form for saxs-analysis | the Scripting group: Enabled, Python interpreter, Command allowlist, timeout, Templates and Saved scripts folders | yes | done |
