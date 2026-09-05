"""The ``user`` organization axis (planning/multiuser_plan.md).

Not authentication and not a security boundary — a label stamped on new
conversations, used to filter the listing, and expandable into configured
paths. These tests cover the three things that can actually go wrong: the
slug letting a typed name escape its folder, ``{user}`` reaching a path
consumer unexpanded, and a filtered listing hiding history that predates
the feature.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aida.config.settings import AppConfig, WorkspaceConfig
from aida.config.users import (
    DEFAULT_USER_SLUG,
    resolve_active_user,
    resolve_records_dir_for_user,
    resolve_workspace_for_user,
    substitute_user,
    user_slug,
    uses_user_placeholder,
)
from aida.persistence.db import CURRENT_SCHEMA_VERSION, connect
from aida.persistence.store import ConversationStore
from aida.providers.base import Message
from aida.workspace.safety import SafetyGuard

# --- slugging: the only thing standing between a typed name and a path ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("jan", "jan"),
        ("Jan Ilavsky", "jan-ilavsky"),
        ("  spaced  ", "spaced"),
        ("jac-paper", "jac-paper"),
        ("USAXS_2026", "usaxs-2026"),
    ],
)
def test_user_slug_normalizes_ordinary_names(raw: str, expected: str):
    assert user_slug(raw) == expected


@pytest.mark.parametrize(
    "hostile",
    ["../../etc", "..", ".", "/", "\\", "../secrets", "a/../../b", "~", "...", "  ../  "],
)
def test_user_slug_cannot_escape_its_folder(hostile: str):
    """Every traversal character is destroyed rather than escaped, so no
    typed name — hostile or merely careless — can reach outside the folder
    it is substituted into."""
    slug = user_slug(hostile)
    assert "/" not in slug and "\\" not in slug
    assert ".." not in slug
    assert not slug.startswith(".")
    # And it still resolves to something inside the parent it is joined to.
    base = Path("/records/attachments").resolve()
    assert base in (base / slug).resolve().parents


@pytest.mark.parametrize("empty", ["", None, "   ", "///", "..."])
def test_user_slug_falls_back_to_a_named_default(empty):
    """Never an empty segment: '.../scripts/{user}/x' with no user must not
    collapse into '.../scripts//x', and must not leave a literal
    '{user}' folder behind either."""
    assert user_slug(empty) == DEFAULT_USER_SLUG


# --- precedence ----------------------------------------------------------


def test_resolve_active_user_precedence_is_flag_then_env_then_config():
    config = AppConfig(active_user="from-config")
    env = {"AIDA_USER": "from-env"}
    assert resolve_active_user("from-flag", app_config=config, env=env) == "from-flag"
    assert resolve_active_user(None, app_config=config, env=env) == "from-env"
    assert resolve_active_user(None, app_config=config, env={}) == "from-config"
    assert resolve_active_user(None, app_config=AppConfig(), env={}) == ""


def test_resolve_active_user_ignores_blank_values_at_every_level():
    """A whitespace-only env var or config entry must fall through rather
    than winning and yielding a user named '   '."""
    config = AppConfig(active_user="from-config")
    assert resolve_active_user("  ", app_config=config, env={"AIDA_USER": "  "}) == "from-config"


def test_resolve_active_user_returns_the_name_as_typed():
    """Stored in the DB and shown in the UI as written; slugged only when
    it becomes part of a path."""
    assert resolve_active_user("Jan Ilavsky", app_config=AppConfig(), env={}) == "Jan Ilavsky"


# --- substitution --------------------------------------------------------


def test_substitute_user_leaves_paths_without_the_placeholder_alone():
    assert substitute_user("/data/usaxs", "jan") == "/data/usaxs"
    assert substitute_user(None, "jan") is None
    assert substitute_user("", "jan") == ""


def test_substitute_user_expands_every_occurrence():
    assert substitute_user("/w/{user}/scripts/{user}", "Jan") == "/w/jan/scripts/jan"


def test_resolve_workspace_returns_the_same_object_when_nothing_to_expand():
    """Every configuration predating this feature must be untouched — not
    merely equal, but the same object, so nothing downstream can observe a
    difference."""
    ws = WorkspaceConfig(name="w", target_folder="/out", source_folders=["/in"])
    assert resolve_workspace_for_user(ws, "jan") is ws
    assert resolve_workspace_for_user(None, "jan") is None
    assert not uses_user_placeholder(ws)


def test_resolve_workspace_expands_every_path_field():
    ws = WorkspaceConfig(
        name="w",
        source_folders=["/data/{user}/raw", "/shared"],
        target_folder="/out/{user}",
        templates_dir="/t/{user}",
        saved_scripts_dir="/s/{user}",
    )
    resolved = resolve_workspace_for_user(ws, "Jan Ilavsky")
    assert resolved.source_folders == ["/data/jan-ilavsky/raw", "/shared"]
    assert resolved.target_folder == "/out/jan-ilavsky"
    assert resolved.templates_dir == "/t/jan-ilavsky"
    assert resolved.saved_scripts_dir == "/s/jan-ilavsky"
    # The original is not mutated — a workspace object is shared config.
    assert ws.target_folder == "/out/{user}"


def test_resolve_workspace_with_no_active_user_uses_the_default_segment():
    ws = WorkspaceConfig(name="w", target_folder="/out/{user}")
    assert resolve_workspace_for_user(ws, "").target_folder == f"/out/{DEFAULT_USER_SLUG}"


def test_resolve_records_dir_for_user():
    assert resolve_records_dir_for_user("~/Documents/Aida/{user}", "jan") == "~/Documents/Aida/jan"
    assert resolve_records_dir_for_user(None, "jan") is None


def test_saved_scripts_dir_default_follows_the_expanded_target_folder():
    """`resolved_saved_scripts_dir()` derives from target_folder when unset,
    so expanding the workspace first is what makes the derived path
    per-user too — without a second substitution anywhere."""
    ws = WorkspaceConfig(name="w", target_folder="/out/{user}")
    resolved = resolve_workspace_for_user(ws, "jan")
    assert resolved.resolved_saved_scripts_dir() == str(Path("/out/jan/saved_scripts"))


# --- THE hazard: substitution must precede the safety guard --------------


def test_guard_built_from_a_resolved_workspace_allows_the_real_folder(tmp_path: Path):
    """The one genuine trap in this feature. A SafetyGuard built from an
    *unexpanded* workspace holds a literal '.../{user}/' allowed root, so
    every write into the real folder reads as out-of-bounds and prompts —
    a failure that looks like a broken safety model rather than a broken
    path. Both halves are asserted so the ordering cannot silently regress.
    """
    ws = WorkspaceConfig(name="w", target_folder=str(tmp_path / "out" / "{user}"))
    real_file = tmp_path / "out" / "jan" / "report.md"

    unresolved_guard = SafetyGuard.for_workspace(target_folder=ws.target_folder, mode="confirm")
    assert not unresolved_guard.is_allowed(real_file), (
        "an unexpanded {user} root must NOT cover the real folder — if this "
        "starts passing, the placeholder is being expanded somewhere else "
        "and this test no longer proves anything"
    )

    resolved = resolve_workspace_for_user(ws, "jan")
    guard = SafetyGuard.for_workspace(target_folder=resolved.target_folder, mode="confirm")
    assert guard.is_allowed(real_file)


# --- schema --------------------------------------------------------------


def test_migration_4_adds_the_user_column_to_an_existing_v3_database(tmp_path: Path):
    """An upgrade in place: a v3 DB with real rows in it gains the column,
    keeps every row, and reports NULL for conversations that predate it."""
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, title TEXT, workspace_name TEXT, profile_name TEXT,
            sidecar_dirname TEXT NOT NULL DEFAULT 'figures', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, record_path TEXT, origin TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            seq INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
            tool_calls_json TEXT, tool_call_id TEXT, name TEXT, created_at TEXT NOT NULL,
            UNIQUE(conversation_id, seq)
        );
        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, call_id TEXT,
            kind TEXT NOT NULL, path TEXT, mime_type TEXT, created_at TEXT NOT NULL, seq INTEGER
        );
        INSERT INTO conversations (id, title, created_at, updated_at)
        VALUES ('old1', 'From before users existed', '2026-01-01', '2026-01-01');
        PRAGMA user_version = 3;
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        row = conn.execute('SELECT title, "user" FROM conversations WHERE id = ?', ("old1",)).fetchone()
        assert row["title"] == "From before users existed"
        assert row["user"] is None
    finally:
        conn.close()


# --- stamping and filtering ---------------------------------------------


def _store_with_conversations(tmp_path: Path) -> tuple[ConversationStore, dict[str, str]]:
    store = ConversationStore(tmp_path / "aida.db")
    ids = {
        "jan": store.create_conversation(timestamp="2026-01-03", title="Jan's fits", user="jan"),
        "eva": store.create_conversation(timestamp="2026-01-02", title="Eva's scans", user="eva"),
        "legacy": store.create_conversation(timestamp="2026-01-01", title="Before users"),
    }
    for conv_id in ids.values():
        store.append_message(conv_id, Message(role="user", content="hi"), timestamp="2026-01-04")
    return store, ids


def test_list_conversations_unfiltered_by_default(tmp_path: Path):
    store, _ = _store_with_conversations(tmp_path)
    try:
        assert len(store.list_conversations()) == 3
    finally:
        store.close()


def test_list_conversations_filtered_keeps_unlabelled_history_visible(tmp_path: Path):
    """The upgrade trap: every conversation predating migration 4 has a NULL
    user, so filtering them out would make a user's whole history vanish
    from the sidebar the first time they picked a name."""
    store, _ = _store_with_conversations(tmp_path)
    try:
        titles = [c.title for c in store.list_conversations("jan")]
        assert titles == ["Jan's fits", "Before users"]
        assert "Eva's scans" not in titles
    finally:
        store.close()


def test_list_conversations_can_exclude_unlabelled_when_asked(tmp_path: Path):
    store, _ = _store_with_conversations(tmp_path)
    try:
        titles = [c.title for c in store.list_conversations("jan", include_unowned=False)]
        assert titles == ["Jan's fits"]
    finally:
        store.close()


def test_known_users_needs_no_registration_step(tmp_path: Path):
    store, _ = _store_with_conversations(tmp_path)
    try:
        assert store.known_users() == ["eva", "jan"]
    finally:
        store.close()


def test_conversation_summary_carries_the_user(tmp_path: Path):
    store, ids = _store_with_conversations(tmp_path)
    try:
        assert store.get_conversation(ids["jan"]).user == "jan"
        assert store.get_conversation(ids["legacy"]).user is None
    finally:
        store.close()


def test_empty_user_is_stored_as_null_not_empty_string(tmp_path: Path):
    """So 'no user' has exactly one representation in the DB, and the
    include_unowned logic (an IS NULL test) cannot miss half of them."""
    store = ConversationStore(tmp_path / "aida.db")
    try:
        conv_id = store.create_conversation(timestamp="2026-01-01", user="")
        assert store.get_conversation(conv_id).user is None
    finally:
        store.close()
