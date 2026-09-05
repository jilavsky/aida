# GUI work guide — for an agent with a working Qt environment

**Written 2026-09-05.** Everything below is the GUI half of work whose
non-GUI half is already **built, tested and merged**. It was left undone
because the session that wrote the rest could not run Qt (`libEGL` missing
from that container), and a filter or a status line written blind is
exactly the kind of change that should not be.

Read this whole file before editing anything. Section 0 is not optional.

---

## 0. Before you start

**Environment check.** These must both pass, or stop and say so:

```bash
python -m pytest tests/ -q --ignore=tests/ui     # expect: 1322 passed
python -m pytest tests/ui -q                     # expect: green
```

If `tests/ui` cannot run, you are in the same situation as the session that
wrote this file — **do not implement anything here**. Say so and stop.

**Ground rules for this codebase**, learned the hard way:

- `ruff check src tests` must pass; `ruff check --fix` handles import
  order. Run it before you declare anything done.
- Core must never import Qt. `tests/ui/test_qt_contract.py` enforces it.
  All Qt imports go through `aida/ui/qt/_qt.py` — import from there, not
  from `PySide6` directly.
- Every claim in a comment must be true. This codebase's comments explain
  *why*, and stale ones are treated as bugs (`PLAN.md` §1.5).
- Do not add a `[x]` to `PLAN.md`. That file holds open work only; move
  finished items out of it.

**Work in four separate commits**, in the order below. Each is
independently useful and independently revertable.

---

## 1. Sidebar filtering — user and workspace

**Why**: conversations accumulate in one flat list with no safe bulk
cleanup, because "delete everything older than 30 days" in a shared list
takes conversations somebody else wanted kept. Background:
`planning/multiuser_plan.md`.

**What already exists** (do not rebuild):

- `ConversationStore.list_conversations(user=None, *, include_unowned=True)`
  filters, and keeps NULL-user rows visible on purpose — every
  conversation predating migration 4 has NULL there, so excluding them
  would make a user's whole history vanish the first time they picked a
  name.
- `ConversationStore.known_users()` → the distinct names in the DB.
- `ConversationSummary.user` and `.workspace_name`.
- `AppConfig.active_user`, already honoured by `start_session`.

### 1a. Make the search box match the workspace too

`src/aida/ui/qt/conversations_sidebar.py`, in `_apply_filter`:

```python
    def _apply_filter(self, query: str) -> None:
        query = query.strip().lower()
        visible = (
            self._all_summaries
            if not query
            else [s for s in self._all_summaries if query in (s.title or "").lower()]
        )
```

The row label already *shows* the workspace (`_row_label` renders
`14:32  [usaxs-staff]  Guinier fits`), but the filter only matches the
title — so typing a workspace name does nothing even though the user can
see it in the row. Change the predicate to match title **or** workspace
**or** user:

```python
            else [s for s in self._all_summaries if _matches(s, query)]
```

with a module-level helper:

```python
def _matches(summary: ConversationSummary, query: str) -> bool:
    """The search box matches anything the row visibly shows — title,
    workspace and user. Matching only the title was a papercut: the
    workspace is right there in the row, so typing it and getting nothing
    reads as a broken filter rather than a narrow one."""
    haystacks = (summary.title, summary.workspace_name, summary.user)
    return any(query in (value or "").lower() for value in haystacks)
```

### 1b. A user filter with its escape hatch — same commit

**Do not ship the filter without the escape hatch.** Hidden work reads as
lost work; that is the single most important sentence in this section.

In `ConversationsSidebar.__init__`, above `self._search_edit`:

```python
        # Filtering by user is organization, never security — anyone can
        # pick any name. "All users" is therefore always one click away,
        # and is the default until a name is actually in use: a filter that
        # can hide work must never be something you can end up inside
        # without having chosen it.
        self._user_filter = QComboBox(self)
        self._user_filter.addItem(ALL_USERS_LABEL)
        self._user_filter.currentTextChanged.connect(lambda _t: self._apply_filter(self._search_edit.text()))
        layout.addWidget(self._user_filter)
```

with `ALL_USERS_LABEL = "All users"` at module level. Then:

- `set_conversations` repopulates the combo from the summaries it was
  given (`sorted({s.user for s in summaries if s.user})`), preserving the
  current selection if it still exists and falling back to `ALL_USERS_LABEL`
  if it does not. **Block signals while repopulating** or you will
  re-enter `_apply_filter` mid-update.
- Hide the combo entirely when no summary has a user
  (`self._user_filter.setVisible(bool(names))`) — a single-user install
  must not grow a control that always says "All users".
- `_apply_filter` narrows by the selected user before the text query,
  keeping NULL-user rows visible (mirror `include_unowned=True`):

```python
        selected = self._user_filter.currentText()
        if selected != ALL_USERS_LABEL:
            visible = [s for s in visible if s.user in (selected, None)]
```

### 1c. Tests

`tests/ui/test_conversations_sidebar.py` — follow the existing style there.

- Text query matches a workspace name, and a user name, not just a title.
- With users present, the combo is visible and lists them; with none, it
  is hidden.
- Selecting a user hides another user's conversation **and keeps the
  NULL-user one visible**. This is the regression test that matters.
- Selecting a user then switching back to "All users" restores everything.
- `set_conversations` with a user selected keeps that selection.

---

## 2. The user picker in the toolbar

**Why**: `active_user` is currently only settable by hand-editing
`config.yaml`. Everything underneath it works already.

### 2a. `UserSelector`

`src/aida/ui/qt/selectors.py`. Copy `WorkspaceSelector` (same file, ~line
33) — same label + `QComboBox` shape, same `Signal(str)` on change. Make
the combo **editable** so a new name can be typed:

```python
class UserSelector(QWidget):
    """Who (or what) new conversations are labelled with.

    Editable on purpose: names are not registered anywhere — one exists
    because a conversation used it (`ConversationStore.known_users`) — so
    the first conversation for a new person or project is created by
    typing the name here.
    """

    user_changed = Signal(str)
```

Give it `set_users(names, *, current=None)` and `current_user()`.
Include a blank first entry meaning "no user".

### 2b. Wire it into `MainWindow`

`src/aida/ui/qt/main_window.py`, `_build_ui`, immediately after
`self.profile_selector` (~line 171):

```python
        self.user_selector = UserSelector(self)
        toolbar.addWidget(self.user_selector)
        self.user_selector.user_changed.connect(self._on_user_changed)
```

Populate it wherever the sidebar is refreshed
(`_refresh_conversations_sidebar`, ~line 1209) — you already have a store
open there:

```python
    def _refresh_conversations_sidebar(self) -> None:
        store = ConversationStore()
        try:
            self.sidebar.set_conversations(store.list_conversations())
            self.user_selector.set_users(
                store.known_users(), current=self.settings.app.active_user
            )
        finally:
            store.close()
```

Handler:

```python
    def _on_user_changed(self, name: str) -> None:
        """Switching user starts a new chat, exactly as switching workspace
        does — and never re-labels the conversation already open. A
        conversation belongs to whoever created it (see
        ConversationRecorder's resume path); re-stamping it here would
        quietly move someone else's work into the current bucket."""
        if name == (self.settings.app.active_user or ""):
            return
        self.settings.app.active_user = name
        save_app_config(self.settings.app)
        self._restart_session(
            workspace_name=self.workspace_selector.current_workspace() or None,
            profile_name=None,
            resume_conversation_id=None,
        )
```

`_restart_session` needs no new parameter: it rebuilds the session from
`self.settings`, and `start_session` resolves the active user from
`settings.app` via `resolve_active_user`.

Import `save_app_config` from `aida.config.settings` if it is not already
imported there.

### 2c. Tests

`tests/ui/test_selectors.py` — construction, `set_users`, signal on
change, blank entry means no user.

`tests/ui/test_main_window.py` — changing the selector writes
`settings.app.active_user`, and a conversation created afterwards carries
it (`session.recorder.user`). Use the existing MockProvider harness in
that file; `pump_until` from `tests/ui/_qt_test_utils.py` is how the
existing tests wait for a restart.

---

## 3. Telling the user where an attachment went

**Why**: attachments are now copied into
`<records_dir>/attachments/<conv8>/` and deleted with the conversation
(`planning/documents_implementation.md` Phase B). Nothing says so.

### 3a. Status-bar line on ingest

In `main_window.py`, `_on_send_requested` already computes the kept set:

```python
        self.bridge.send(
            outgoing,
            images=images,
            attachment_paths=[p for p in attachments if p not in failures],
        )
```

Add, right after, a status-bar line naming the files — one sentence, no
dialog:

```python
        kept = [p for p in attachments if p not in failures]
        if kept:
            names = ", ".join(Path(p).name for p in kept)
            self.statusBar().showMessage(
                f"Attached {names} — copied into this conversation's folder", 6000
            )
```

### 3b. "Open Conversation Folder"

`_build_menu_bar` (~line 322), beside the existing folder actions:

```python
        open_attachments_action = QAction("Open Conversation Folder", self)
        open_attachments_action.triggered.connect(self._on_open_conversation_folder)
        file_menu.addAction(open_attachments_action)
```

```python
    def _on_open_conversation_folder(self) -> None:
        """The attachments folder for the conversation currently open.

        Deliberately does not *create* it: `attachments_dir()` is a pure
        lookup (`_claim_attachments_dir` is the writing half), and opening
        a folder for a chat that never had an attachment should say so
        rather than conjure an empty directory and a database row.
        """
        session = self.bridge.session
        recorder = session.recorder if session is not None else None
        if recorder is None:
            self.statusBar().showMessage("No conversation open yet.", 5000)
            return
        directory = recorder.attachments_dir()
        if not directory.is_dir():
            self.statusBar().showMessage("Nothing has been attached to this conversation.", 5000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
```

### 3c. Tests

`tests/ui/test_main_window.py`. The file already monkeypatches
`QDesktopServices.openUrl` for the other folder actions — copy that
pattern. Cover: no session → message not a crash; session with no
attachments → message, **and assert the folder was not created**; with an
attachment → `openUrl` called with the right path.

---

## 4. Settings: the OCR key, and the workspace switch

**Why**: Phase D shipped the backend, the consent gate and the fallback
(`planning/documents_implementation.md`). The key can currently only be
set via `$AIDA_SECRET_MISTRAL_OCR`.

**Read first**: `src/aida/documents/ocr/mistral.py` module docstring, and
`OcrBackend.approved_for` in `figure_tools.py`. The consent design is
already decided; you are adding the two places it is configured from.

### 4a. Settings dialog — a "Document OCR" group

`src/aida/ui/qt/settings_dialog.py`. The dialog is a flat
`form.addRow(...)` layout; add a group after the scheduler rows.

Copy the **write-only** secret pattern from
`src/aida/ui/qt/profiles_dialog.py` (~line 515): the field is never
populated from storage and is written with
`aida.config.secrets.set_secret` on OK.

Rows to add:

- A `QLabel` with `setOpenExternalLinks(True)`:
  `Get a free API key from <a href="https://console.mistral.ai/api-keys">console.mistral.ai/api-keys</a>. The free tier covers roughly 10 documents / 50 MB at a time — enough for occasional use.`
- **A plain statement, not buried**: `Documents you ask about are uploaded
  to Mistral. AIDA asks before each one. Enable it per workspace in
  Workspaces…`
- `QLineEdit` with `setEchoMode(QLineEdit.EchoMode.Password)`,
  placeholder `(unchanged)`.
- A **Clear key** button calling `aida.config.secrets.delete_secret(SECRET_REF)`.

Import `SECRET_REF` from `aida.documents.ocr.mistral` — do not retype the
string.

In `open_settings_dialog` (`main_window.py` ~line 1651), after
`dialog.updated_app_config()`, write the key **only if the field is
non-empty** (empty means "leave it alone", which is why the field is never
pre-filled):

```python
        ocr_key = dialog.ocr_api_key()
        if ocr_key:
            set_secret(OCR_SECRET_REF, ocr_key)
```

`updated_app_config()` must stay unchanged — the key is not an
`AppConfig` field and must never be written to `config.yaml`.

### 4b. Workspace dialog — `use_ocr`

`src/aida/ui/qt/workspace_management_dialog.py`. Copy the
`_scripting_checkbox` pattern (~line 179 for construction, ~line 289 for
collection):

```python
        self._use_ocr_checkbox = QCheckBox(
            "Use Mistral OCR for figures in attached documents", self
        )
        self._use_ocr_checkbox.setChecked(workspace.use_ocr if workspace else False)
        self._use_ocr_checkbox.setToolTip(
            "Uploads attached documents to Mistral to read their figures. Off by default. "
            "Per workspace because the answer differs: a manuals workspace can have it on "
            "while one used to review unpublished manuscripts keeps it off. You are still "
            "asked before each document is sent."
        )
```

and `use_ocr=self._use_ocr_checkbox.isChecked()` in the `WorkspaceConfig(...)`
construction at ~line 274.

**Check the detail panel too** (~line 310): it lists
`scripting_enabled: ...`; add `use_ocr: ...` beside it, or the field will
be invisible when reviewing a workspace.

### 4c. Tests

`tests/ui/test_settings_dialog.py`: the OCR field starts empty; leaving it
empty does not call `set_secret`; a value calls it with `SECRET_REF`;
`updated_app_config()` gains no OCR field.

`tests/ui/test_workspace_management_dialog.py`: the checkbox round-trips
through edit → OK (the existing "quick tasks survive an edit" test in that
file is the pattern — a field the form does not carry through gets reset
on OK, which is a bug that has already happened once here).

---

## 5. Documentation — last, once the above works

Two files, both held until now so they would not describe a half-built UI:

- **`docs/organizing-conversations.md`** — opens with *this is
  organization, not security*, in those words. The user picker, the
  sidebar filter, `{user}` in `records_dir`/`target_folder`/
  `saved_scripts_dir`/`templates_dir`/`source_folders`, `--user`,
  `$AIDA_USER`. State plainly: anyone can pick any name, there is no
  password, and it does not stop anyone reading anyone's files.
- **`docs/documents.md`** — what is copied where, that it is deleted with
  the conversation, `aida conversations gc`, the figure tools, and the OCR
  section: opt-in per workspace, the key, that documents leave the
  machine, and that it falls back silently-but-audibly to the built-in
  extractor.

Link both from `docs/README.md`, and add the `usaxs-user` / `usaxs-staff`
examples with `{user}` in `saved_scripts_dir` to
`examples/config/workspaces.yaml` (wanted by
`PLAN_INSTRUMENT_INTEGRATION.md` §1.1 for retiring BeamlineAdvisor).

---

## 6. When you are done

```bash
ruff check src tests
python -m pytest tests/ -q                 # all of it, ui included
```

Then update, in this order:

1. `CHANGELOG.md` — under `[Unreleased]`, extend the existing user-label
   and attachment entries rather than adding new ones; they already
   describe the GUI as "still to come".
2. `PLAN.md` §1.3 and §1.5 — remove the items you finished. Do not tick
   them; that file holds open work only.
3. `planning/multiuser_plan.md` §7 and
   `planning/documents_implementation.md` — append what you built and any
   place you departed from this guide, with the reason.

**If something here is wrong, trust the code over this file** and say what
you changed. This guide was written from a reading of the code by someone
who could not run it.
