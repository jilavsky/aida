# Moving and sharing an AIDA setup

**Status: Tiers 1 and 2 shipped 2026-09-11; Tier 3 open.** Written from a real
need: a setup developed on one machine has to reach a work computer, a
colleague, and — at the beamline — whichever workstation is free that day.
Today that is manual file-by-file copying, and it is getting worse as the
feature count grows. This doc inventories every piece of state AIDA owns,
classifies each by whether it *can* move, and proposes two related
deliverables (a share bundle and a full backup) built on one format. The
share bundle is now built, and so is selective import — `aida.portability`,
`aida config export/import`, File → Export/Import Setup… — see §10 for what
shipped and §9 for the decisions that shaped it. User-facing guide:
[`docs/moving-and-sharing.md`](../docs/moving-and-sharing.md).

Related: `planning/multiuser_plan.md` (the `{user}` axis, already shipped —
it interacts with path rewriting), `planning/DESIGN.md` §4 (the two-root
storage layout this all rests on), PLAN.md §2.5 "Sharing" (conversation
export bundles — a neighbour of this work, not the same thing).

## 1. The inventory

Everything AIDA persists, and what class it falls in. Compiled by reading
`aida/config/paths.py`, `aida/config/settings.py`, `aida/persistence/db.py`,
and a real `~/.aida` (10 skills, 10 provider profiles, 7 MCP servers, 5
knowledge indexes, 7 MB DB).

### Class A — portable as-is, pure content

The obvious wins. Kilobytes, no paths, no secrets, no machine assumptions.

| What | Where | Note |
|---|---|---|
| Skills | `~/.aida/skills/*.md` | Fully portable. The single highest-value thing to share. |
| System-prompt files | `~/.aida/prompts/*.md` | Resolved relative to `config_dir()` (`workspace/workspaces.py::_system_prompt_file_path`). Portable content, and the relative form is *already* the machine-independent one. |
| Stored workflows | `~/.aida/workflows/*.yaml` | Reference workspace/profile/mcp_group **by name** — portable if those names come along. |
| Schedules | `~/.aida/schedules.yaml` | Reference workflows by name. Portable. |
| Quick tasks | inside `workspaces.yaml` | Prompt templates. Portable. |
| Provider/embedding profiles | `providers.yaml` | Portable *minus* the secret. `base_url` is fine to carry (`localhost:11434`, the Argo URL) — both are correct on any machine at ANL. |
| Knowledge-base *definitions* | `knowledge.yaml` | Portable except `source_folders`. |

### Class B — portable structure, machine-specific paths inside

This is the real work. Each of these is 90% portable with a handful of
absolute paths embedded in it.

| Field | File | Typical value here |
|---|---|---|
| `records_dir`, `scratch_dir`, `allowed_folders` | `config.yaml` | `/Users/ilavsky/Documents/Aida` |
| `source_folders`, `target_folder`, `templates_dir`, `saved_scripts_dir` | `workspaces.yaml` | `/Users/ilavsky/Experiments/USAXS_data/2026` |
| `python_interpreter` | `workspaces.yaml` | `/opt/miniconda3/envs/pyirena/bin/python3.13` |
| `command` (+ path-bearing `args`) | `mcp.json` | `/opt/miniconda3/envs/pyirena/bin/pyirena-mcp` |
| `source_folders` | `knowledge.yaml` | an iCloud Obsidian vault path |

Note that `{user}` substitution (`aida/config/users.py`) already
demonstrates the pattern this needs: a token in a stored path, expanded at
resolve time. Path *tokenising* for export is the same idea pointed the
other way, and must not collide with it — `{user}` in an exported path
stays a literal `{user}`, untouched.

### Class C — secrets, never in a bundle

- OS keychain entries, service `aida`, one per `secret_ref`
  (`argo`, `argo-usaxs`, `ollama`, `openrRouter`, … plus the Mistral OCR
  ref from `aida.documents.ocr.mistral.SECRET_REF`). The keychain is not
  exportable and must not be.
- **`mcp.json`'s `env` values.** These are *not* keychain-backed: they are
  plaintext in the config file. The live config on this machine contains
  `"BRAVE_API_KEY": "BSAIF2yb…"` in cleartext. Any naive
  "zip up `~/.aida`" would mail that key to a colleague. This is the single
  most important safety requirement on the exporter, and it is a finding
  independent of whether the export feature is ever built.

`mcp.json`, `config.yaml`, `providers.yaml`, `schedules.yaml`,
`workspaces.yaml` happen to be mode 0600 (a side effect of
`_atomic_write_text` using `mkstemp`); `knowledge.yaml` is 0644. Worth
making that deliberate rather than incidental while touching this area.

### Class D — machine-specific, actively harmful to copy

Not merely useless — copying them makes the target install worse.

- `window_width/height/x/y` (`window_x: 2563` is a second monitor that the
  laptop does not have), `splitter_sizes`, `font_size`, `collapsed_panels`.
- `last_workspace_name` / `last_profile_name`.
- `scheduler.lock`, `logs/`, `tmp/`, `.DS_Store`, `mcp.json.bak-*`.

### Class E — personal, not shareable even though it is portable

Distinct from D: technically machine-independent, but it is *about a
person*, and shipping it to a colleague is wrong.

- `user_context`, `user_contexts`, `active_user`, `known_users`.
- `WorkspaceConfig.notes` — the Notes panel is explicitly private working
  scratch (its docstring says so). The live config has genuine working
  notes in it.

These belong in a *backup* (moving to your own new machine) and not in a
*share bundle*. That asymmetry is the main reason the two scopes must be
separate settings rather than one "export everything" button.

### Class F — bulk data, backup-only

- `aida.db` (7 MB): conversations, messages, artifact metadata,
  `schedule_runs`. Copies cleanly — one SQLite file, `PRAGMA user_version`
  migrations run on open (`persistence/db.py`), so an older backup restored
  into a newer AIDA simply migrates forward. **But** it stores absolute
  paths in `conversations.record_path`, `conversations.attachments_path`,
  `conversations.sidecar_path` and `artifacts.path`.
- `~/.aida/artifacts/` (2.3 MB): the binaries those rows point at.
- `~/.aida/knowledge/*.db`: derived embeddings. Rebuildable *if* the source
  folders exist on the target; still worth carrying in a backup, because an
  index answers queries even when its sources are absent.
- The records dir `~/Documents/Aida/`: transcripts, `figures/` sidecars,
  `attachments/` (copies of documents fed into conversations). User
  documents. Large, and arguably the user's to move with Finder — but the
  DB points into it, so a backup that skips it leaves dangling links.

### Class G — not AIDA's to move, and no format fixes this

The honest answer to "many features are not copyable". A bundle can *name*
these; it cannot carry them.

- conda environments and the MCP server packages inside them
  (`pyirena`, `aievaluator`, `epics-mcp`, `bait`), Node/`npx` and
  npm-cached servers like `playwright-mcp` / `server-brave-search`.
- Ollama and its pulled models (`gemma4:26b-mlx` is not coming along).
- Experiment data folders and any external `templates_dir` repo
  (e.g. `bits-usaxs`).
- Network reachability: `EPICS_CA_ADDR_LIST: 10.54.122.63` only means
  something on the beamline LAN.

**The design consequence:** the import step's real product is not the
copied files, it is the *report* — "these 7 things resolved, these 4 did
not, here is what to install and here are the 5 secrets to re-enter."
AIDA already has the machinery to produce that: `validate_workspace`,
`validate_profile`, `known_group_names`, and `aida doctor`'s check list.
Import should end by running them, not by inventing new checks.

## 2. Two deliverables, one format

Keeping these separate is the core decision.

**Share bundle** — "send my setup to a colleague / stand up a second
machine's *configuration*." Class A + B (paths tokenised or mapped),
secret *refs* listed but no values, Class C/D/E/F excluded. Kilobytes,
inspectable, emailable.

**Full backup** — "I am moving machines / this workstation may be
reimaged." Share bundle *plus* Class E, `aida.db`, `artifacts/`,
`knowledge/`, and optionally the records dir. Tens to hundreds of MB.
Still excludes Class C (keychain) and D (window geometry — a restore should
not drag a dead monitor's coordinates onto the new machine either).

One implementation, one format, a `scope` field in the manifest. The
backup is a superset, not a second codebase.

### Format

A zip. Boring on purpose, stdlib `zipfile` (already used in
`documents/figures.py`, no new dependency), inspectable without AIDA,
diffable after unpacking, and it scales from 30 kB to 300 MB unchanged.

```
manifest.json          bundle_version, aida_version, created_at, scope,
                       source_platform, source_aida_home
config/app.yaml        portable subset of config.yaml (see §1 D/E)
config/providers.yaml
config/workspaces.yaml
config/mcp.json        env secrets redacted
config/knowledge.yaml
config/schedules.yaml
workflows/*.yaml
skills/*.md
prompts/*.md
paths.json             path inventory: token, original, role, referents
secrets.json           refs required — names only, never values
README.txt             what this is, what it deliberately omits
# backup scope adds:
data/aida.db
data/artifacts/**
data/knowledge/*.db
records/**             optional
```

`manifest.json` carries `bundle_version: 1` and the reader must accept
every older version it has ever written — the same "old configs must always
load" rule `settings.py` opens with. This is the one piece of the design
that is expensive to get wrong later.

## 3. Path handling — where the effort actually is

Three strategies, in increasing order of cost and value.

**(a) Verbatim.** Import paths unchanged; let `validate_workspace` warn
about unreachable folders (it already does, precisely). Nearly free.
Acceptable for exactly one import; intolerable by the third.

**(b) Tokenise the known roots.** On export, rewrite path prefixes to
tokens; on import, expand against the target's own values:

| Token | Source |
|---|---|
| `${HOME}` | `Path.home()` |
| `${AIDA_HOME}` | `config.paths.app_dir()` |
| `${RECORDS}` | effective records dir |
| `${CONDA_ENV:pyirena}` | a `.../envs/<name>/bin\|Scripts/...` segment |

`${HOME}` alone resolves the majority of a real config — every
`source_folders`, `target_folder`, `allowed_folders`, `scratch_dir` entry
in the live file is under `/Users/ilavsky/`. `${CONDA_ENV:…}` resolves the
MCP `command` paths, which are the most annoying to fix by hand, and it
crosses the macOS→Windows boundary (`bin/x` → `Scripts/x.exe`) that
otherwise breaks every server entry. Resolve on import by probing
`conda env list`, then known prefixes, then `shutil.which(basename)`; if
none hit, import the server **disabled with a stated reason** rather than
silently broken.

**(c) An explicit mapping table.** `paths.json` lists every distinct
machine-specific path that did not tokenise, with the fields referencing
it. Import shows original → new, pre-filled with a best guess, plus
"leave as-is" / "clear". CLI prompts, GUI table.

Recommendation: **(b) + (c)**. (b) makes the common case one keystroke;
(c) makes the uncommon case possible at all. (a) is what you get for free
if the tiering below stops early, and it is a defensible Tier 1.

## 4. Secrets — the rule

Never in a bundle, no flag, no exceptions. What the bundle carries instead:

- `secrets.json`: the *list* of refs the imported config needs, derived
  from every `secret_ref` in `providers.yaml` + `embedding_profiles`, the
  OCR ref if a workspace sets `use_ocr`, and every redacted `mcp.json` env
  key.
- Export **scans `mcp.json` `env` values** and redacts anything that looks
  like a credential (key/token/secret/password in the name, or a
  high-entropy value), listing each in `secrets.json`. Non-secret env
  (`EPICS_CA_ADDR_LIST`, `EPICS_CA_AUTO_ADDR_LIST`) passes through.
- Import emits the exact commands to fix it:
  `aida config secret set argo`, one line per ref — and in the GUI, a
  "set these now" walk-through.

**Decided against (2026-09-11), not merely deferred:** a
passphrase-encrypted secrets sidecar for the self-move case. It is genuinely
tempting — re-typing five keys on the new machine is the annoying part of a
self-move — but it means a new `cryptography` dependency, a passphrase UX,
and a file that is a footgun if mishandled. Re-entering the keys is the
correct trade, and the import report makes it a copy-paste job rather than a
memory test.

## 5. Merge semantics on import

Import into a non-empty `~/.aida` needs a stated policy, and the safe one:

- **Default: add new, skip existing, report both.** A name collision on a
  workspace / profile / server / skill / workflow is skipped and listed.
- `--overwrite` replaces on collision; `--rename` imports as
  `name (imported)`. Per-item selection in the GUI.
- **`config.yaml` scalars are opt-in** (`--include-app-settings`, off by
  default). A colleague should not silently inherit
  `max_agent_iterations: 500`, `default_safety_mode: relaxed`, or
  `user_context: "The user is Jan…"`. Class D never imports at all.
- Dependency closure: importing a workspace pulls the profile it names, the
  skills it lists, the KB definitions it references, and the MCP servers
  whose `groups` contain its `mcp_group` (`mcp/groups.py::resolve_group` is
  the resolver to reuse). Selective import must offer the closure, not just
  the one item, or it produces configs that validate as broken.
- Every write goes through the existing `save_*_config` helpers so it
  inherits `_atomic_write_text`. Take a timestamped copy of each touched
  file first — the `mcp.json.bak-<stamp>` convention already sitting in
  `~/.aida` is the precedent.

## 6. The DB rewrite (backup scope only)

Restoring `aida.db` needs one pass over four columns —
`conversations.record_path`, `.attachments_path`, `.sidecar_path`, and
`artifacts.path` — applying the same token map §3 built. Bounded and
testable: a few UPDATEs plus a check that the rewritten path exists, with
unresolved rows left alone and counted in the report (a broken image link
in a year-old transcript is a cosmetic loss, not a reason to fail a
restore).

Restore must refuse to run against a live AIDA — the two-instances-sharing-
`~/.aida` hazard already noted in PLAN.md §2.1 is much worse when one of
them is swapping the DB out. `aida.core.proc_lock` already exists for the
scheduler and is the natural mechanism.

## 7. Effort

Assumes this repo's normal standard: tests for round-trip, old-bundle
compatibility, collision handling, redaction, and Windows path separators.

| Tier | Contents | Effort |
|---|---|---|
| **1 — share bundle, CLI** | Format + manifest + versioning; export of Class A/B; `${HOME}`/`${AIDA_HOME}` tokenising; secret redaction + `secrets.json`; import with skip-on-collision; import report reusing `validate_*`; `aida config export` / `aida config import`; docs. | **3–4 d** |
| **2 — usable import** | Mapping table (CLI prompts + GUI dialog); `${CONDA_ENV:…}` resolution incl. macOS↔Windows; selective import with dependency closure; File-menu Export/Import + report dialog. | **3–4 d** |
| **3 — full backup** | Backup scope (db/artifacts/knowledge/records); DB path rewrite; restore guard via `proc_lock`; pre-restore backup of the existing `~/.aida`; `aida backup create` / `aida backup restore`. | **3–4 d** |

**≈ 10 days total**, and the tiers are genuinely independent — Tier 1 alone
delivers the "email my skills, prompts, workspaces and provider definitions
to a colleague" case that prompted this, and it is the tier with the best
value-per-day by a wide margin.

New module: `aida/portability/` (`bundle.py`, `paths_map.py`, `report.py`)
— a leaf that may import `aida.config` and `aida.persistence` but nothing
above them, per the layering rule `tests/test_contract_layering.py`
enforces. GUI stays thin: file dialogs, a mapping table, a report view.

## 8. Testing

- Round-trip: export a populated `aida_home` → import into an empty one →
  every Class A/B item identical modulo remapped paths.
- A `bundle_version: 1` bundle must still import after the format grows.
- Redaction: a bundle built from an `mcp.json` containing an API key must
  not contain that string anywhere — assert on the raw zip bytes.
- Collision matrix: skip / overwrite / rename for each config kind.
- Closure: importing one workspace brings its profile, skills, KBs, and
  group servers, and the result passes `validate_workspace` with no
  unknown-profile failure.
- Path mapping across platforms: a macOS-exported bundle imported with
  Windows separators and a `Scripts/*.exe` conda layout.
- Backup: restore into a fresh home, then assert the DB migrates, the
  rewritten artifact paths exist, and a resumed conversation renders its
  images.
- `aida_home` / `records_home` fixtures in `tests/conftest.py` already give
  the isolation all of this needs.

## 9. Decisions taken (2026-09-11)

Recorded here rather than only in a commit message, because the reasoning is
what a future reader needs.

1. **Tier 1 first, Tiers 2-3 left open.** Tier 1 solves the stated problem
   (a colleague, and the author's own second machine) and has by far the
   best value per day. Tiers 2 and 3 stay in PLAN.md §1.6 as independent
   follow-ons.
2. **MCP command resolution pulled forward into Tier 1.** It was scoped as
   Tier 2, but a real `mcp.json` has five conda-env absolute paths in it and
   every one would have been wrong on the target — the single most annoying
   part of the move, for about half a day of work.
3. **No secrets, no exceptions.** No flag, no encrypted sidecar. See §4.
4. **Personal fields excluded by default**, `--include-personal` to include
   them. Off by default is what makes a bundle safe to email without
   thinking about it; the flag is for a self-move. `WorkspaceConfig.notes`
   counts as personal — its own docstring calls it private working scratch.
5. **Skip-on-collision by default**, with `--on-conflict overwrite|rename`.
   An import must never be the thing that loses someone's configuration.
6. **General settings travel but do not apply** without `--app-settings`.
   Importing a colleague's workspaces must not switch your install to their
   `default_safety_mode: relaxed`.
7. **Plain `.zip`.** Openable and readable by anyone, on any machine, with
   no AIDA involved — which is exactly what you want before emailing your
   configuration to someone.
8. **GUI: two File-menu items and a report dialog**, no per-item pick list.
   The pick list is Tier 2's expense and it is closure logic, not pixels.

## 10. What Tier 1 actually shipped

`aida/portability/` — `paths_map.py` (tokenize/expand), `bundle.py`
(export/import/merge), `report.py` (shared CLI+GUI text). A leaf package:
it reads `aida.config` and, for the report only, `aida.workspace`'s
validators; nothing above it imports it, so `aida config export` costs
nothing at startup for anyone who never runs it.

- `aida config export DEST [--include-personal]`
- `aida config import SRC [--on-conflict skip|overwrite|rename] [--app-settings]`
  — exits 2 when an executable could not be located, so a scripted machine
  setup notices what a human would read in the report.
- GUI: File → Export Setup… / Import Setup…, plus a copyable report dialog.
  An import reloads `Settings` **in place** (other objects hold the same
  instance) and refreshes the selectors, so imported workspaces and profiles
  appear without a restart.
- 45 tests at the time Tier 1 landed (35 + 6 GUI + 4 CLI; see §11 for the
  count after Tier 2). The three that matter most: the raw zip bytes must not contain a known API key; a
  `bundle_version` newer than this AIDA is refused by name; a `../` member
  name cannot write outside the target folder.

Two findings fell out of building it, both pre-existing and unrelated to
export:

- The author's live `mcp.json` had a Brave Search API key in cleartext in a
  server's `env` block. Nothing was wrong with AIDA — `env` is by design the
  one place a key is not keychain-backed — but it is exactly what a naive
  "zip up `~/.aida`" would have leaked, and it is why redaction is a hard
  requirement rather than a nicety.
- Validation on import surfaced two silently-broken references in that same
  config: a workspace naming a skill `Worksday_skills` that does not exist
  (a typo for `Workday_skills`), and one naming `prompts/pyirena.md` with no
  `~/.aida/prompts/` folder to resolve it against — so its system prompt was
  the literal string. Both were already true before any of this; the import
  report is simply the first thing that ever said so out loud.

## 11. What Tier 2 added

Three separable things, all of which Tier 1 did crudely on purpose.

**Reading a bundle without importing it.** `aida/portability/contents.py`
parses a bundle into a `BundleContents` — every selectable item with a
one-line summary — touching nothing under `~/.aida`. Everything below is
built on it, and so is `aida config import --list`.

**Dependency closure** (`closure.py`). A selection is a *seed*; the closure
is its transitive expansion over the references the config format already
has: a workspace pulls its profile, skills, knowledge bases and prompt file,
plus every server whose `groups` contains its `mcp_group`; a knowledge base
pulls its embedding profile; a workflow pulls its workspace; a schedule
pulls its workflow. Iterated to a fixpoint, because those chain. This is the
part that was correctly identified as the expensive half of Tier 2, and it
is expensive in logic rather than pixels — the reason selective import
without it is worse than useless is that a workspace missing its profile
imports *cleanly* and fails later. A reference the bundle cannot satisfy is
reported, never fatal: one stale name in a colleague's config must not block
everything else in it.

**Path remapping** (`path_issues.py`, plus `PathMapper.overrides`).
`inspect_paths` answers "what will not resolve here" before anything is
written; an override maps a bundle-side value to a real one and matches by
**longest prefix**, so one entry redirects a whole tree and a specific rule
can sit inside a broad one.

Surfaces: `--list`, `--only KIND:NAME` (lenient kind aliases, repeatable or
comma-separated), `--no-deps`, `--map FROM=TO`, `--check`; and in the GUI a
Contents tab (checkable tree, ticking a workspace ticks its dependencies and
says which) and a Paths tab that appears only when something is wrong and
shrinks as it is filled in.

Two bugs surfaced while building it, both pre-existing in Tier 1:

- A workspace's `system_prompt` may name **any** path relative to `~/.aida`,
  and the exporter carried the file at that path — but the importer only
  ever extracted members under `prompts/`. A workspace naming
  `team-prompts/reviewer.md` therefore shipped its prompt and never unpacked
  it, leaving the imported workspace using the literal path string as its
  prompt. The importer now extracts prompt-class members at their own
  relative path.
- `--check` claimed "nothing was written" while `load_settings` created
  default config files and `validate_workspace` created `~/.aida/skills/`.
  Both are correct behaviour in their own right and wrong under a promise of
  no writes. Fixed by composing `Settings` from the per-file loaders during
  a preview, and by giving `validate_workspace` a `skills_root` argument —
  which also fixes a latent problem where it consulted `~/.aida/skills` even
  when called against a different config directory.

Test count is now 100 for portability across three files: 70 in
`tests/test_portability.py`, 13 in `tests/ui/test_portability_dialog.py`,
and 17 of the 24 in `tests/test_config_cmds.py`. The ones worth naming: a selected workspace's import must
produce **no** validation warnings (that is the closure's actual contract),
a dry run must leave the target byte-identical, and an override must turn a
reported unresolvable command into a working one.

## 12. Still open

Tier 3 only, tracked in PLAN.md §1.6. Two smaller things noticed while
building Tier 1 and deliberately left alone:

- `knowledge.yaml` is written mode 0644 while every other config file is
  0600 (a side effect of which files predate `_atomic_write_text`). Worth
  making deliberate rather than incidental, but it is a `settings.py`
  change, not a portability one.
- A bundle records `source_platform` in its manifest but nothing reads it
  yet. It is there for a future importer that wants to warn about, say, a
  Windows-authored `command_allowlist` landing on macOS.
