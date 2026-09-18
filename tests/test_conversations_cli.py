"""Tests for aida.cli.conversations — the ``aida conversations``
list/resume/delete/export subcommand (Phase 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.conversations import (
    AmbiguousConversationIdError,
    UnknownConversationIdError,
    _build_parser,
    _resume_async,
    cmd_delete,
    cmd_export,
    cmd_gc,
    cmd_list,
    cmd_rename,
    resolve_conversation_id,
)
from aida.config.paths import ensure_scratch_dir
from aida.config.settings import ProviderProfile, load_settings
from aida.persistence.store import ConversationStore
from aida.providers.base import Message
from aida.providers.mock import MockProvider, MockTurn

T0 = "2026-08-19T00:00:00"


def _store(tmp_path: Path) -> ConversationStore:
    # Same DB the CLI would open (db_path() honors AIDA_HOME via aida_home fixture).
    return ConversationStore()


# --- resolve_conversation_id --------------------------------------------------


def test_resolve_full_id(aida_home: Path, records_home: Path):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0)
    assert resolve_conversation_id(store, conv_id) == conv_id


def test_resolve_unambiguous_prefix(aida_home: Path, records_home: Path):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0)
    assert resolve_conversation_id(store, conv_id[:8]) == conv_id


def test_resolve_unknown_prefix_raises(aida_home: Path, records_home: Path):
    store = _store(aida_home)
    store.create_conversation(timestamp=T0)
    with pytest.raises(UnknownConversationIdError):
        resolve_conversation_id(store, "no-such-prefix")


def test_resolve_ambiguous_prefix_raises(aida_home: Path, records_home: Path, monkeypatch):
    store = _store(aida_home)
    # Force two conversations to share a prefix by making create_conversation
    # accept explicit ids.
    store.create_conversation(timestamp=T0, conversation_id="aaaa1111")
    store.create_conversation(timestamp=T0, conversation_id="aaaa2222")
    with pytest.raises(AmbiguousConversationIdError):
        resolve_conversation_id(store, "aaaa")


# --- cmd_list ------------------------------------------------------------------


def test_cmd_list_no_conversations(aida_home: Path, records_home: Path, capsys):
    rc = cmd_list(_build_parser().parse_args(["list"]))
    assert rc == 0
    assert "No conversations yet." in capsys.readouterr().out


def test_cmd_list_shows_title_and_workspace(aida_home: Path, records_home: Path, capsys):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0, workspace_name="use-pyirena")
    store.set_title(conv_id, "My analysis", timestamp=T0)
    store.close()

    rc = cmd_list(_build_parser().parse_args(["list"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert conv_id[:8] in out
    assert "use-pyirena" in out
    assert "My analysis" in out


# --- cmd_export ------------------------------------------------------------------


def test_cmd_export_writes_markdown_file(aida_home: Path, records_home: Path, capsys):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0, title="hi")
    store.append_message(conv_id, Message(role="user", content="hello"), timestamp=T0)
    store.close()

    rc = cmd_export(_build_parser().parse_args(["export", conv_id[:8]]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Exported transcript to" in out

    path_str = out.split("Exported transcript to", 1)[1].strip()
    path = Path(path_str)
    assert path.exists()
    assert "hello" in path.read_text(encoding="utf-8")


def test_cmd_export_unknown_id_reports_error(aida_home: Path, records_home: Path, capsys):
    rc = cmd_export(_build_parser().parse_args(["export", "does-not-exist"]))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does-not-exist" in out


def test_cmd_export_dest_writes_to_a_different_folder(
    aida_home: Path, records_home: Path, tmp_path: Path, capsys
):
    """Export Conversation As… equivalent — a snapshot elsewhere must not
    touch the conversation's regular background transcript."""
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0, title="hi")
    store.append_message(conv_id, Message(role="user", content="hello"), timestamp=T0)
    store.close()

    dest = tmp_path / "vault"
    rc = cmd_export(_build_parser().parse_args(["export", conv_id[:8], "--dest", str(dest)]))
    out = capsys.readouterr().out
    assert rc == 0

    path_str = out.split("Exported transcript to", 1)[1].strip()
    path = Path(path_str)
    assert path.parent == dest
    assert "hello" in path.read_text(encoding="utf-8")


def test_cmd_export_tool_results_flag_overrides_default_mode(
    aida_home: Path, records_home: Path, tmp_path: Path, capsys
):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0, title="hi")
    store.append_message(
        conv_id, Message(role="tool", content="a raw payload", tool_call_id="call_1"), timestamp=T0
    )
    store.close()

    rc = cmd_export(
        _build_parser().parse_args(
            ["export", conv_id[:8], "--dest", str(tmp_path / "vault"), "--tool-results", "off"]
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    path = Path(out.split("Exported transcript to", 1)[1].strip())
    assert "a raw payload" not in path.read_text(encoding="utf-8")


# --- cmd_delete ------------------------------------------------------------------


def test_cmd_delete_with_yes_flag_skips_prompt(aida_home: Path, records_home: Path, capsys):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0)
    store.append_message(conv_id, Message(role="user", content="hello"), timestamp=T0)
    store.close()

    rc = cmd_delete(_build_parser().parse_args(["delete", conv_id[:8], "--yes"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Deleted conversation" in out

    store2 = ConversationStore()
    assert store2.get_conversation(conv_id) is None
    store2.close()


def test_cmd_delete_without_yes_prompts_and_aborts_on_no(
    aida_home: Path, records_home: Path, monkeypatch, capsys
):
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0)
    store.close()

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    rc = cmd_delete(_build_parser().parse_args(["delete", conv_id[:8]]))
    out = capsys.readouterr().out
    assert rc == 1
    assert "Aborted." in out

    store2 = ConversationStore()
    assert store2.get_conversation(conv_id) is not None
    store2.close()


def test_cmd_delete_unknown_id_reports_error(aida_home: Path, records_home: Path, capsys):
    rc = cmd_delete(_build_parser().parse_args(["delete", "does-not-exist", "--yes"]))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does-not-exist" in out


# --- cmd_gc (planning/improvement_plan_2026-09.md §3: scratch cleanup) ------


def _touch_with_age(path: Path, *, days_old: float) -> None:
    import os
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    old_time = time.time() - days_old * 86400
    os.utime(path, (old_time, old_time))


def test_cmd_gc_with_no_orphans_and_scratch_disabled_reports_nothing_to_do(
    aida_home: Path, records_home: Path, capsys
):
    rc = cmd_gc(_build_parser().parse_args(["gc", "--yes"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "No leftover attachment folders." in out
    assert "scratch" not in out.lower()  # --scratch-days defaults to 0: not even mentioned


def test_cmd_gc_scratch_days_defaults_to_disabled(aida_home: Path, records_home: Path, capsys):
    scratch = ensure_scratch_dir()
    _touch_with_age(scratch / "old.txt", days_old=45)

    rc = cmd_gc(_build_parser().parse_args(["gc", "--yes"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "scratch" not in out.lower()
    assert (scratch / "old.txt").exists()


def test_cmd_gc_scratch_days_removes_stale_files_with_yes_flag(
    aida_home: Path, records_home: Path, capsys
):
    scratch = ensure_scratch_dir()
    old_file = scratch / "tool-results" / "stdout-abc.txt"
    new_file = scratch / "new.txt"
    _touch_with_age(old_file, days_old=45)
    _touch_with_age(new_file, days_old=1)

    rc = cmd_gc(_build_parser().parse_args(["gc", "--yes", "--scratch-days", "30"]))
    out = capsys.readouterr().out

    assert rc == 0
    assert "Removed 1 scratch file(s)." in out
    assert not old_file.exists()
    assert new_file.exists()


def test_cmd_gc_scratch_days_without_yes_prompts_and_skips_on_no(
    aida_home: Path, records_home: Path, monkeypatch, capsys
):
    scratch = ensure_scratch_dir()
    old_file = scratch / "old.txt"
    _touch_with_age(old_file, days_old=45)

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    rc = cmd_gc(_build_parser().parse_args(["gc", "--scratch-days", "30"]))
    out = capsys.readouterr().out

    assert rc == 0
    assert "Skipped scratch file cleanup." in out
    assert old_file.exists()


def test_cmd_gc_attachment_orphans_and_scratch_cleanup_are_independent(
    aida_home: Path, records_home: Path, capsys
):
    """Nothing to do on one side must not skip the other."""
    scratch = ensure_scratch_dir()
    old_file = scratch / "old.txt"
    _touch_with_age(old_file, days_old=45)

    rc = cmd_gc(_build_parser().parse_args(["gc", "--yes", "--scratch-days", "30"]))
    out = capsys.readouterr().out

    assert rc == 0
    assert "No leftover attachment folders." in out
    assert "Removed 1 scratch file(s)." in out


# --- cmd_rename ------------------------------------------------------------------


def test_cmd_rename_updates_the_title(aida_home: Path, records_home: Path, capsys):
    """Bug report: "Can we have the chat list in the history column have
    some kind of names? ... these date/times are not very convenient to
    use." set_title already existed for auto-titling; this is the missing
    "rename it again" CLI entry point."""
    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0)
    store.close()

    rc = cmd_rename(_build_parser().parse_args(["rename", conv_id[:8], "USAXS beamtime notes"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "USAXS beamtime notes" in out

    store2 = ConversationStore()
    assert store2.get_conversation(conv_id).title == "USAXS beamtime notes"
    store2.close()


def test_cmd_rename_unknown_id_reports_error(aida_home: Path, records_home: Path, capsys):
    rc = cmd_rename(_build_parser().parse_args(["rename", "does-not-exist", "New Title"]))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does-not-exist" in out


# --- cmd_resume (via _resume_async, same pattern as test_start_session.py) ---


@pytest.mark.asyncio
async def test_resume_async_continues_conversation_with_stored_profile(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = load_settings()
    settings.providers.profiles["p1"] = ProviderProfile(name="p1", kind="openai_compat", model="m1")

    store = _store(aida_home)
    conv_id = store.create_conversation(timestamp=T0, profile_name="p1")
    store.append_message(conv_id, Message(role="user", content="earlier turn"), timestamp=T0)
    store.close()

    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")])
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "/exit")  # end the REPL immediately

    rc = await _resume_async(
        settings,
        conv_id,
        profile_name=None,
        workspace_name=None,
        skill_names=[],
        mcp_group="",
        mcp_names=[],
    )
    assert rc == 0

    store2 = ConversationStore()
    messages = store2.load_messages(conv_id)
    store2.close()
    assert any(m.content == "earlier turn" for m in messages)


@pytest.mark.asyncio
async def test_resume_async_unknown_conversation_reports_error(
    aida_home: Path, records_home: Path, capsys
):
    # In practice cmd_resume's own resolve_conversation_id call filters bad
    # ids before _resume_async is ever reached; this confirms _resume_async
    # still fails closed (prints + returns 1, no traceback) if it somehow is.
    settings = load_settings()
    rc = await _resume_async(
        settings,
        "does-not-exist",
        profile_name=None,
        workspace_name=None,
        skill_names=[],
        mcp_group="",
        mcp_names=[],
    )
    assert rc == 1
    assert "does-not-exist" in capsys.readouterr().out


# --- parser ------------------------------------------------------------------


def test_parser_resume_accepts_workspace_and_mcp_flags():
    args = _build_parser().parse_args(
        ["resume", "abcd1234", "--workspace", "ws1", "--mcp-group", "grp", "--mcp", "a,b"]
    )
    assert args.id == "abcd1234"
    assert args.workspace == "ws1"
    assert args.mcp_group == "grp"
    assert args.mcp == "a,b"


def test_parser_delete_yes_flag_defaults_false():
    args = _build_parser().parse_args(["delete", "abcd1234"])
    assert args.yes is False


def test_parser_rename_accepts_id_and_title():
    args = _build_parser().parse_args(["rename", "abcd1234", "USAXS notes"])
    assert args.id == "abcd1234"
    assert args.title == "USAXS notes"
