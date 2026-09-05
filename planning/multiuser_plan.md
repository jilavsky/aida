# Conversation organization — the "user" layer (multi-user / multi-task)

**Status: accepted plan, 2026-09-04.** Companion to `PLAN.md` §1.3, which
holds the checkboxes. Supersedes the sketch in
`PLAN_INSTRUMENT_INTEGRATION.md` §1.2.

**Reframed 2026-09-04 after discussion.** This started as "per-user chats
on the beamline machine" and is better understood as **an organization axis
for conversations**. At the beamline the buckets happen to be people; on a
laptop they are just as usefully tasks or projects ("jac-paper",
"usaxs-manual-rewrite"). The mechanism is identical, and the motivating
complaint is the same in both settings: with a flat, ever-growing chat list
there is no safe way to clean up, because a bulk delete in a shared list
will take conversations someone wanted to keep.

**It is not secrecy.** Anyone at the machine can pick any bucket. There is
no password, no permission difference, no attempt to stop one person
reading another's files on a shared OS login. Say this in the docs in those
words, because the word "user" invites the other reading. If a real trust
boundary is ever needed it belongs at the OS login or the deployment model
(`PLAN_INSTRUMENT_INTEGRATION.md` §1.3), not here.

---

## 0. Ordering — do this before the attachment store

The documents work (`documents_implementation.md`) writes files into the
records dir. **A DB column can be added any time; a folder layout cannot be
changed after users have files in it.** If attachments ship first at
`<records_dir>/attachments/<conv8>/` and the user layer later moves
`records_dir` per user, every existing attachment folder is stranded at the
old path — a file migration, and one where getting it wrong means an
undeleted copy of a confidential document.

So:

1. **Phase A of the documents plan (the dropped-image warnings) ships now** —
   it touches no storage and is independent of everything here.
2. **This layer's schema and path resolution land next** (§3 steps 1, 2 and
   4). That settles where files go.
3. **Then the attachment store** (documents Phase B) is built once, on the
   settled layout.
4. The GUI picker, filters and per-user context (§3 steps 3 and 5) can
   follow at leisure — they change no paths.

Steps 1 and 2 here are perhaps a day. It is worth taking that day first.

---

## 1. Why this is cheap

Every place that needs to know about a user is already a single choke
point:

| Concern | Choke points today |
|---|---|
| Creating a conversation | `ConversationRecorder.__init__` → `ConversationStore.create_conversation` — **one** call site (`core/session.py:1145/1151`) |
| Listing conversations | `ConversationStore.list_conversations()` — 3 real callers (GUI sidebar, `aida conversations list`, cleanup) |
| Schema | `persistence/db.py` `_MIGRATIONS`, additive, `PRAGMA user_version`, already at 3 with a ladder exercised twice |
| Where a script is saved | `WorkspaceConfig.resolved_saved_scripts_dir()` — **one** method |
| Where transcripts land | `ensure_records_dir(settings.app.records_dir)` |
| Who the model thinks it is talking to | `AppConfig.user_context` → `build_identity_context_block` — already global, already exists |
| Toolbar widget | `ui/qt/selectors.py` already has `WorkspaceSelector`/`ProfileSelector` to copy |

Artifacts need no column of their own: they are conversation-scoped
(`artifacts.conversation_id`), so filtering conversations filters them.

**Correction to `PLAN.md` §2.1.** It claimed the Phase 4 schema "already
carries a nullable `user` column as cheap insurance." It does not.
Migrations 1–3 give `conversations` exactly `id`, `title`, `workspace_name`,
`profile_name`, `sidecar_dirname`, `created_at`, `updated_at`,
`record_path`, `origin`. The column must be added — one `ALTER TABLE`, so
the correction costs nothing, but it is worth knowing before planning
around it.

## 2. Two axes, and one of them is nearly free

**Axis 1 — workspace. Already recorded, already displayed, not filterable.**
`conversations.workspace_name` is populated on every conversation, and
`conversations_sidebar._row_label` already renders it as
`14:32  [usaxs-staff]  Guinier fits`. But `_apply_filter` matches the search
box against `s.title` only — so typing a workspace name into the filter
does nothing, even though the user can see it in the row.

Making the filter match workspace and title, plus a workspace dropdown
above the list, is ~20 lines and **no schema change at all**. Do it in the
same pass; it may cover a good share of the felt problem on its own.

**Axis 2 — user. One new column.** The bucket a conversation belongs to:
a person at the beamline, a project on a laptop. AIDA does not care which,
and the docs should say so explicitly.

*Naming:* **`user`, decided 2026-09-04** — continuity with
BeamlineAdvisor's username box wins, and the beamline is where this is
actually deployed. The cost is that the word invites a security reading it
does not deserve, so the docs carry that weight instead: every user-facing
mention says *organization, not security* in those words.

*SQL note:* quote the column as `"user"` in every statement. SQLite accepts
it bare, but it is reserved in other engines and reads confusingly next to
`PRAGMA user_version`, which is unrelated.

## 3. Implementation, in order

Each step is independently shippable and backward compatible. With `user`
unset, behaviour is exactly as today: NULL in the column, no filtering, no
user segment in any path.

### Step 1 — schema (migration 4)

```sql
ALTER TABLE conversations ADD COLUMN user TEXT;
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user, updated_at);
```

Bump `CURRENT_SCHEMA_VERSION`; add `user` to `ConversationSummary` and
`_row_to_summary`. Existing rows get NULL, which reads as "before buckets
existed" and stays visible to everyone.

*Test:* a v3 DB migrates and old rows survive with `user is None`. The
concurrent-startup test in `tests/ui/test_main_window.py` already covers
the idempotent-DDL race and should pass untouched.

### Step 2 — path resolution (the one real hazard)

One helper, used everywhere, rather than substitution at each use site:

```python
def resolve_for_user(cfg: WorkspaceConfig, user: str) -> WorkspaceConfig
```

returning a copy with `{user}` expanded in `target_folder`,
`saved_scripts_dir`, `templates_dir` and `source_folders`, plus the same
expansion for `AppConfig.records_dir`. An empty user substitutes
`"default"` so a path never collapses to `//`.

**This must run before `SafetyGuard.for_workspace` builds `allowed_roots`**,
or the agent gets a literal `.../saved_scripts/{user}/` root and every
write into the real folder prompts as out-of-bounds. That ordering is the
only genuine trap in this whole change — make it an explicit test.

Sanitize to a path-safe slug (`persistence/records.slugify` exists) and
reject `.`, `..` and separators, so a typed name cannot escape its folder.

### Step 3 — the picker and the filters

- `AppConfig.active_user: str = ""` (empty = today's behaviour), persisted
  like `assistant_name`, so the machine reopens as whatever was last used —
  a convenience, not a claim.
- CLI: `--user NAME` on `aida chat` / `run` / `workflow run` /
  `conversations list`, plus an `AIDA_USER` env var for headless and
  scheduled runs, resolved flag → env → config (the precedence the secrets
  layer already uses).
- GUI: a `UserSelector` in `ui/qt/selectors.py` modelled on
  `WorkspaceSelector` — editable combo of names seen before plus free text.
  Known names come from `SELECT DISTINCT user FROM conversations`; no
  user table, no registration step.
- Sidebar: filter by active user, **plus** the workspace filter from §2,
  **plus** a *Show all* escape. Hiding someone's work irrecoverably would
  be a worse bug than showing it — and NULL-user conversations stay
  visible to everyone.
- Switching user ends the current session and starts a new chat, exactly
  as switching workspace already does. Never re-stamp an in-flight
  conversation.

### Step 4 — stamp on create

`create_conversation(..., user=…)`; `ConversationRecorder` passes it
through; `start_session` gains an `user` parameter defaulting to the
resolved active user. One call site.
`list_conversations(user=None, *, include_unowned=True)` — `None` means no
filter, which is what cleanup wants.

### Step 5 — per-user personal context

`AppConfig.user_context` is one per-install string today. Store
`user_contexts: dict[str, str]` alongside it, look up the active user,
fall back to the flat string when absent. `build_identity_context_block`
needs no change — only its one caller in `session.py`.

### Step 6 — docs and examples

- `docs/organizing-conversations.md`, opening with *this is organization,
  not security*.
- Ship `usaxs-user` and `usaxs-staff` in `examples/config/workspaces.yaml`
  with `{user}` already in `saved_scripts_dir`, so the pattern is copied
  rather than reinvented (also wanted by
  `PLAN_INSTRUMENT_INTEGRATION.md` §1.1 for retiring BeamlineAdvisor).
- `aida doctor`: when a user is set, report the resolved folders and
  whether they are writable.

## 4. What stays shared, and why

The surprises here are the things people assume are per-user:

| Shared | Reason |
|---|---|
| `providers.yaml`, keychain secrets (incl. the OCR key) | One OS login means one keychain; per-person keys would need per-person OS accounts. A shared staff profile is what BeamlineAdvisor effectively does today. |
| `mcp.json`, MCP groups | Staff-configured instrument access. |
| `workspaces.yaml` | The `usaxs-user`/`usaxs-staff` split is the intended axis of variation, not per-person workspaces. |
| Knowledge bases | Expensive to build, identical content for everyone. |
| `schedules.yaml`, `workflows/` | Machine-level automation. Per-user scheduling would need the scheduler to know which user to run *as* — a real design question; leave it out. |
| Artifacts, logs, scratch | Conversation- or machine-scoped. |

## 5. Risks

1. **Two AIDA instances on one machine.** `PLAN.md` §2.1 lists unguarded
   concurrent `~/.aida` access as a known gap; this change makes it
   *likely* rather than hypothetical. The DB is the safe part —
   `connect()` sets `busy_timeout` and the migration ladder is idempotent
   under a race. The unsafe part is config writes: two processes saving
   `config.yaml` lose one `active_user`, which is harmless; the same race
   on `workspaces.yaml` is not. Options: (a) accept and document; (b) a
   lock file that makes the second instance say so; (c) reload config
   before every save. **Recommendation: (a) now, (b) before the beamline
   deployment is announced.** Do not skip this one.
2. **Someone picks the wrong bucket**, by typo or otherwise. No defence, by
   design — but keep the name visible in the toolbar at all times so it is
   noticed.
3. **Filtered-away work looks lost.** Mitigated by *Show all* and by
   keeping NULL-user conversations visible.
4. **`{user}` left unsubstituted in an allowed root.** Step 2; it is a
   test, not a design problem.

## 6. Sizing

| Step | Size |
|---|---|
| 1 — migration | ~15 lines + a test. Trivial. |
| 2 — path resolution | ~40 lines and the ordering test. **The subtle one.** |
| 3 — picker and filters | ~80 lines, one new widget; the workspace filter is ~20 of it and needs no schema. |
| 4 — stamp on create | ~30 lines, one call site. |
| 5 — per-user context | ~20 lines. |
| 6 — docs and examples | Half a day of writing. |

Nothing here touches the agent loop, the provider layer, MCP, RAG or the
safety model. It is a persistence and presentation change with one
path-resolution hazard — which is why it is safe to take now, and why now
is better than after users have accumulated history and attachments under
no user at all.

---

## 7. What shipped, 2026-09-05

Steps 1, 2 and 4 — the core layer, and everything the attachment store was
waiting on. 44 new tests; full non-GUI suite green (1258 passed), ruff
clean.

**Schema (migration 4).** `conversations."user" TEXT`, nullable, plus
`idx_conversations_user (user, updated_at)`. Quoted everywhere. Verified by
a test that builds a real v3 database with rows in it, opens it through
`connect()`, and asserts the rows survive with `user IS NULL`.

**`aida/config/users.py`** — a new leaf module so the GUI, CLI and session
layer can all reach it without importing each other:

- `user_slug()` — the security-relevant piece, though not in the way the
  word "user" suggests. It is not protecting one person from another; it is
  stopping a *typed name* from escaping the folder it is interpolated into.
  Collapsing every non-alphanumeric run to a hyphen destroys `..`, `/` and
  `\` rather than escaping them, so `"../../etc"` becomes `"etc"` and a
  name of pure punctuation becomes `"default"`. Ten hostile inputs are
  tested, each asserted to resolve *inside* its parent.
- `resolve_active_user()` — flag → `$AIDA_USER` → `config.yaml`, skipping
  blank values at every level so a stray whitespace env var cannot win.
- `resolve_workspace_for_user()` — returns **the same object**, not a copy,
  when the workspace has no `{user}` in it. Every configuration predating
  this feature is therefore untouched in a way nothing downstream can
  observe.

**The ordering hazard, closed.** `{user}` is expanded in exactly one place
in `_start_session`: immediately after `get_workspace()`, before
`validate_workspace`, `_ensure_workspace_folders`,
`SafetyGuard.for_workspace` and `build_workspace_context_block`. Two tests
hold it:

- A unit test asserting both halves — that a guard built from an
  *unexpanded* workspace does **not** cover the real folder, and that one
  built from a resolved workspace does. The negative half is the point: if
  it ever starts passing, the placeholder is being expanded somewhere else
  and the positive half no longer proves anything.
- An end-to-end test through `start_session` asserting the real directory
  is created, that no folder literally named `{user}` is left behind, and
  that the model is told the resolved path.

`resolved_saved_scripts_dir()` needed no change: it derives from
`target_folder`, so expanding the workspace first makes the derived path
per-user for free.

**Stamping.** One call site, as predicted. `create_conversation(user=…)`
stores `None` for an empty name, so "no user" has exactly one
representation and the `IS NULL` filter cannot miss half of them.
**Resume never re-labels** — a conversation belongs to whoever created it,
even if a different name is active now; re-stamping would quietly move
someone else's work into the current bucket. Tested.

**Filtering.** `list_conversations(user=None, *, include_unowned=True)`.
The default matters more than it looks: every conversation predating
migration 4 has a NULL user, so excluding them would make a user's entire
history vanish from the sidebar the first time they picked a name — a
data-loss-shaped surprise from a labelling feature. `known_users()` reads
`SELECT DISTINCT "user"`, so a name exists because a conversation used it;
no user table, no registration step.

**CLI.** `--user` on `aida chat` and `aida run`; `--user` and `--all-users`
on `aida conversations list`, where `--all-users` beats `--user` beats the
configured default (an explicit "show me everything" must never be narrowed
by an `active_user` left in `config.yaml`). The listing's user column
appears only once something in the DB uses it, so a single-user install's
output is byte-for-byte unchanged.

### GUI and documentation follow-up, 2026-09-05

The GUI was completed in an environment where all 521 baseline Qt tests
passed. The toolbar now has an editable `UserSelector`; changing it saves
`AppConfig.active_user` and starts a new chat without re-labelling the open
one. The sidebar has an **All users** escape hatch, keeps NULL-user legacy
history visible under named filters, and searches the visible workspace and
user fields as well as the title. `docs/organizing-conversations.md` and the
`usaxs-user` / `usaxs-staff` examples document the finished surface.

One code-driven departure from the GUI guide: when no workspace is active,
the user switch explicitly preserves the current profile. `start_session`
does not fall back to `AppConfig.last_profile_name`; passing
`profile_name=None` in that state opens the no-profile startup failure path.
A workspace-backed switch still lets the workspace choose its configured
profile as planned.

Step 5, per-user personal context, remains separate work; it was described
in this plan but was not included in `gui_implementation_guide.md`.

**The attachment store (`documents_implementation.md` Phase B) is now
unblocked** — the records-dir layout is settled and `{user}` expansion
happens before any consumer sees a path.


---

## 8. Managing labels (2026-09-05, after the GUI landed)

Two gaps surfaced the moment the toolbar box was used by hand rather than
by a test.

**The box acted on every keystroke.** `UserSelector` was wired to
`QComboBox.currentTextChanged`, which on an *editable* combo fires per
character: typing "jan" emitted "j", "ja", "jan", and since `user_changed`
restarts the session, the app tore down and rebuilt a session three times
while the user was still on the first letter. Every test passed, because
they all drove it with `setCurrentText`, which sets the whole string at
once — the tests exercised the API and never the interaction. Now
`activated` (a real pick from the list) and `lineEdit().editingFinished`
(Return, or focus leaving) are what emit, an unchanged value is swallowed,
and `set_users` no longer looks like a user action on the next commit.
A regression test drives `textEdited` character by character.

**A mistyped name could not be repaired.** Nothing validates a free-text
name, so "Jan", "jan" and "Jam" are three people to the database, and until
now nothing in the app could put a split history back together.
`ConversationStore.rename_user` plus a small **File ▸ Manage Users…**
dialog does it; renaming onto an existing name is a *merge*, confirmed
first because it is irreversible from there.

**The dialog was a dead end with no names in use** — an empty list and two
greyed-out buttons, so a dialog called "Manage Users" could not get you a
user. **New User…** fixes that. It writes nothing, because there is nowhere
to write a user to; it makes the name *active*, and the next conversation
is what brings it into existence. The list shows it as "no conversations
yet — active" so it does not look as though the button did nothing. This is
the honest shape: a name exists because a conversation carries it
(`known_users`), and a second source of truth could only disagree with the
conversations.

**No "delete user".** Clearing a label sets it to NULL: the conversations
stay and become visible to everyone, which is what removing a *label*
means. Deleting somebody's work is louder and different, and it already
lives in the sidebar — duplicating a destructive action in two places is
how the wrong one gets clicked.

**A rename is not a switch.** Choosing a new name starts a new chat (the
same handler the toolbar box uses); renaming the name you are working
under does not, because the open conversation was relabelled in place by
the UPDATE and restarting would cost the user their chat for a spelling
fix.

**Toolbar order: User, Workspace, Provider**, left to right in the order
the choices narrow each other — and the order a shared machine is picked
up in: who you are, then what you are doing, then what with.

Still open: per-user `user_context`, which the GUI guide did not cover.


---

## 9. Three bugs from first real use (2026-09-05)

All three surfaced within minutes of the GUI being used by hand, and none
of them was visible from the code alone.

**The User box fitted two characters.** A `QComboBox` sizes itself to its
widest *item*, and on a fresh install the widest item is the empty
"no user" entry. The other selectors never hit this because their items are
always real names. Fixed with `setMinimumContentsLength(18)` plus
`AdjustToContents`.

**Declaring a second name deleted the first.** `known_users()` derives from
`SELECT DISTINCT "user" FROM conversations`, and a session's conversation
row is created empty and deleted again if nothing is ever sent
(`_delete_conversation_if_empty`). So a name whose only conversation was
that unused one disappeared the moment the user switched away. The fix is
`AppConfig.known_users`, **unioned** with the database-derived names rather
than replacing them — which is the distinction that makes it not the
"second source of truth" §8 argued against: it can only add a name that has
no conversations yet, never contradict what the conversations say.

**Selecting a user changed nothing in the list.** Two causes. The sidebar
filter did not follow the toolbar at all, and — more importantly — a
selected user matched `summary.user in (selected, None)`, deliberately
including unlabelled conversations so that a pre-existing history would not
vanish on upgrade. But *all* of a pre-existing history is unlabelled, so in
practice the filter appeared to do nothing for exactly the people it was
protecting. Now a selected name shows only that name's conversations, a new
**(no user)** entry reaches the unlabelled ones on their own, and "All
users" remains one click away as the real safety net. The filter follows a
switch in the toolbar but does not stamp on a choice made in the sidebar
during ordinary refreshes.

The lesson matching §8's: each of these passed every test, because the
tests drove the widgets programmatically and never asked what the thing
looks like or does the second time you touch it.


---

## 10. Moving a conversation between labels (2026-09-05)

`rename_user` moves *everything* a name owns, which is right for a typo but
useless for the commonest mistake a free-text label produces: the wrong
name was selected when one chat was started. `set_conversation_user` moves
named conversations, and the sidebar's right-click menu offers it — for a
multi-selection too, since a run of chats is usually mis-filed together.

It leaves `updated_at` alone on purpose. Relabelling is not activity;
bumping the timestamp would reorder the sidebar and silently change what a
cleanup-older-than sweep catches.

One bug found while wiring it: `_refresh_conversations_sidebar` handed the
toolbar the raw `store.known_users()` while `_known_users()` returned the
union with the remembered list — so a freshly declared name appeared and
then vanished on the next refresh. Both now share one merge.


---

## 11. A hung GUI suite, and the guard for it (2026-09-05)

Adding one entry to the sidebar's context menu broke three tests that pin
the menu's exact contents — expected, and my omission for not updating
them. What was not expected: the run then **hung for ten minutes** with no
indication of which test or why.

`timeout = 30` is configured and did not help, and the reason is worth
recording. pytest-timeout raises in the Python main thread — which is
exactly the thread parked inside a native Qt modal, so the exception is not
delivered until the modal returns, and it never does because nobody is
going to click it. A modal in a GUI test is therefore not a slow test; it
is an unkillable one.

`tests/ui/conftest.py` now has an autouse fixture that replaces every
blocking call (`QDialog.exec`, `QMenu.exec`, the `QMessageBox` statics,
`QInputDialog.getText/getInt`, the `QFileDialog` statics) with one that
raises, naming the call. A test's own `monkeypatch.setattr` still wins,
since it is applied afterwards — this only catches the paths nobody thought
to stub. An immediate named failure is worth far more than any of those
dialogs ever were.

It earned its keep on the first run. The hang was not a stray dialog at
all: `_augment_with_attachments` was left returning a 3-tuple while its one
caller unpacked 4, so **every chat turn with no attachments** raised
`ValueError` — which the send path's own broad `except` swallowed and
answered with a `QMessageBox.warning`, i.e. a modal, i.e. a hung suite.
Ruff cannot see a tuple-arity mismatch and no non-GUI test touches that
method, so nothing else would have caught it.

Two process notes, both mine:

- A patch script that raises **before** its `write_text` applies *nothing*.
  One did, on its last edit; the error looked like "one replacement
  failed", and only the single line ruff then complained about got fixed —
  leaving the file half-edited. Write first, or verify by reading the file
  back. Never infer from ruff that an edit landed.
- The broken branch was the *early return* for "nothing attached", not the
  attachment path. A change about attachments broke the case with none.
  `tests/ui/test_main_window.py` now asserts the arity of both branches
  against the caller directly.


---

## 12. Per-user personal context (2026-09-05) — the layer is complete

`AppConfig.user_contexts: dict[str, str]`, resolved by
`AppConfig.context_for_user(user)`, which **falls back** to the flat
`user_context` rather than replacing it. That is the whole design decision:
on a shared machine most of the useful framing ("this is the USAXS
instrument, these are the detectors") is true for everyone, and only a line
or two differs per person — requiring an entry per user before anyone got
any context would make the common case worse to get the rare one right.

`build_identity_context_block` needed no change, exactly as step 5
predicted; only its one caller in `session.py` did.

In Settings the **Personal context** box edits the *active user's* text
when there is one, and the label says whose — a box that silently means two
different things depending on a dropdown elsewhere in the window is worse
than no box. Clearing it removes that user's entry rather than saving an
empty string, so "no personal context" and "an empty personal context" stay
the same state.

Also added a `dict[str,str]` kind to the config coercion table, so a
hand-edited `user_contexts:` that is not a mapping warns and falls back to
the default like every other malformed field.

**With this, §1.3 is done.** Nothing from the user layer remains open.
