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
