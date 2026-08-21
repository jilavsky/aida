"""Tests for aida.workspace.safety.SafetyGuard — containment (incl. ``..``
and symlink escape), relaxed/confirm mode behavior, trash-move delete, and
the always-confirm-outside-allowed-folders rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.workspace.command_allowlist import CommandAllowlist
from aida.workspace.safety import (
    ConfirmationDenied,
    ConfirmationRequest,
    SafetyGuard,
    deny_all,
    relaxed_mode_warning_if_newly_enabled,
    unique_destination,
)


def _approve_all():
    async def _confirm(_request: ConfirmationRequest) -> bool:
        return True

    return _confirm


def _deny_specific(*paths: str):
    denied = {str(Path(p).resolve(strict=False)) for p in paths}

    async def _confirm(request: ConfirmationRequest) -> bool:
        return request.path not in denied

    return _confirm


# --- containment -------------------------------------------------------


def test_is_allowed_true_for_path_inside_root(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path])
    assert guard.is_allowed(tmp_path / "sub" / "file.txt")


def test_is_allowed_true_for_root_itself(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path])
    assert guard.is_allowed(tmp_path)


def test_is_allowed_false_for_sibling_folder(tmp_path: Path):
    allowed = tmp_path / "allowed"
    sibling = tmp_path / "sibling"
    allowed.mkdir()
    sibling.mkdir()
    guard = SafetyGuard(allowed_roots=[allowed])
    assert not guard.is_allowed(sibling / "file.txt")


def test_dotdot_escape_is_normalized_and_blocked(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    guard = SafetyGuard(allowed_roots=[allowed])
    escaping = allowed / ".." / "secret.txt"
    assert not guard.is_allowed(escaping)


def test_symlink_escape_is_blocked(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "real.txt").write_text("secret")
    link = allowed / "link_to_outside"
    link.symlink_to(outside)

    guard = SafetyGuard(allowed_roots=[allowed])
    # link_to_outside/real.txt *looks* like it's under `allowed`, but
    # resolving the symlink lands it under `outside`, which isn't allowed.
    assert not guard.is_allowed(link / "real.txt")


def test_symlink_inside_allowed_root_targeting_allowed_root_is_fine(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    real_target = allowed / "real_dir"
    real_target.mkdir()
    link = allowed / "link"
    link.symlink_to(real_target)
    guard = SafetyGuard(allowed_roots=[allowed])
    assert guard.is_allowed(link / "file.txt")


# --- mode behavior -------------------------------------------------------


@pytest.mark.asyncio
async def test_relaxed_mode_write_inside_allowed_needs_no_confirmation(tmp_path: Path):
    calls = []

    async def _confirm(request: ConfirmationRequest) -> bool:
        calls.append(request)
        return True

    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=_confirm)
    result = await guard.authorize_write(tmp_path / "out.txt")
    assert result == (tmp_path / "out.txt").resolve()
    assert calls == []  # never asked


@pytest.mark.asyncio
async def test_confirm_mode_write_inside_allowed_asks_and_honors_approval(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confirm", confirm_callback=_approve_all())
    result = await guard.authorize_write(tmp_path / "out.txt")
    assert result == (tmp_path / "out.txt").resolve()


@pytest.mark.asyncio
async def test_confirm_mode_write_inside_allowed_raises_when_declined(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confirm", confirm_callback=deny_all)
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_write(tmp_path / "out.txt")


@pytest.mark.asyncio
async def test_relaxed_mode_read_inside_allowed_needs_no_confirmation(tmp_path: Path):
    (tmp_path / "in.txt").write_text("hi")
    calls = []

    async def _confirm(request):
        calls.append(request)
        return True

    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=_confirm)
    await guard.authorize_read(tmp_path / "in.txt")
    assert calls == []


@pytest.mark.asyncio
async def test_confirm_mode_read_inside_allowed_ALSO_needs_no_confirmation(tmp_path: Path):
    """Per-mode confirmation is about mutating actions (write/delete); a
    read of a file the workspace was already configured to read isn't
    gated even in 'confirm' mode."""
    calls = []

    async def _confirm(request):
        calls.append(request)
        return True

    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confirm", confirm_callback=_confirm)
    await guard.authorize_read(tmp_path / "in.txt")
    assert calls == []


# --- always-confirm outside allowed folders, regardless of mode ---------


@pytest.mark.asyncio
async def test_relaxed_mode_still_confirms_outside_allowed_folders(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    calls = []

    async def _confirm(request):
        calls.append(request)
        return True

    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed", confirm_callback=_confirm)
    await guard.authorize_write(outside)
    assert len(calls) == 1
    assert calls[0].action == "write"


@pytest.mark.asyncio
async def test_write_outside_allowed_folders_denied_by_default(tmp_path: Path):
    """No confirm_callback wired in at all -> deny_all -> safe by default."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    guard = SafetyGuard(allowed_roots=[allowed])
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_write(tmp_path / "outside.txt")


@pytest.mark.asyncio
async def test_read_outside_allowed_folders_requires_confirmation(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    guard = SafetyGuard(allowed_roots=[allowed], confirm_callback=deny_all)
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_read(outside)


# --- for_workspace convenience constructor -------------------------------


def test_for_workspace_unions_source_target_and_global_folders(tmp_path: Path):
    src = tmp_path / "src"
    tgt = tmp_path / "tgt"
    glob = tmp_path / "shared"
    guard = SafetyGuard.for_workspace(
        source_folders=[str(src)], target_folder=str(tgt), global_allowed_folders=[str(glob)]
    )
    assert guard.is_allowed(src / "a.txt")
    assert guard.is_allowed(tgt / "b.txt")
    assert guard.is_allowed(glob / "c.txt")


def test_for_workspace_with_no_target_folder(tmp_path: Path):
    src = tmp_path / "src"
    guard = SafetyGuard.for_workspace(source_folders=[str(src)], target_folder=None)
    assert guard.is_allowed(src / "a.txt")
    assert not guard.is_allowed(tmp_path / "elsewhere.txt")


# --- delete = move to _trash ----------------------------------------------


@pytest.mark.asyncio
async def test_delete_moves_file_into_trash_under_allowed_root(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    victim = allowed / "sub" / "doomed.txt"
    victim.parent.mkdir()
    victim.write_text("bye")

    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed")
    destination = await guard.delete(victim)

    assert not victim.exists()
    assert destination.exists()
    assert destination.parent == allowed / "_trash"
    assert destination.read_text() == "bye"


@pytest.mark.asyncio
async def test_delete_collision_safe_naming_in_trash(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    trash = allowed / "_trash"
    trash.mkdir()
    (trash / "doomed.txt").write_text("already here")

    victim = allowed / "doomed.txt"
    victim.write_text("new one")

    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed")
    destination = await guard.delete(victim)

    assert destination.name == "doomed (1).txt"
    assert destination.read_text() == "new one"
    assert (trash / "doomed.txt").read_text() == "already here"  # untouched


@pytest.mark.asyncio
async def test_delete_with_trash_disabled_hard_deletes(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    victim = allowed / "doomed.txt"
    victim.write_text("bye")

    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed", trash_enabled=False)
    await guard.delete(victim)

    assert not victim.exists()
    assert not (allowed / "_trash").exists()


@pytest.mark.asyncio
async def test_delete_of_missing_file_raises_file_not_found(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed")
    with pytest.raises(FileNotFoundError):
        await guard.delete(allowed / "does-not-exist.txt")


@pytest.mark.asyncio
async def test_delete_outside_allowed_folders_confirms_first(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("bye")
    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed", confirm_callback=_approve_all())
    destination = await guard.delete(outside)
    assert destination.parent == tmp_path / "_trash"


@pytest.mark.asyncio
async def test_delete_declined_leaves_file_in_place(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    victim = allowed / "doomed.txt"
    victim.write_text("bye")
    guard = SafetyGuard(allowed_roots=[allowed], mode="confirm", confirm_callback=deny_all)
    with pytest.raises(ConfirmationDenied):
        await guard.delete(victim)
    assert victim.exists()


# --- unique_destination helper --------------------------------------------


def test_unique_destination_returns_same_path_if_free(tmp_path: Path):
    target = tmp_path / "file.txt"
    assert unique_destination(target) == target


def test_unique_destination_appends_counter_on_collision(tmp_path: Path):
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "file (1).txt").write_text("y")
    result = unique_destination(tmp_path / "file.txt")
    assert result == tmp_path / "file (2).txt"


# --- relaxed-mode one-time warning ----------------------------------------


def test_relaxed_mode_warning_on_transition_from_confirm():
    assert relaxed_mode_warning_if_newly_enabled("confirm", "relaxed") is not None


def test_relaxed_mode_warning_on_first_creation_as_relaxed():
    assert relaxed_mode_warning_if_newly_enabled(None, "relaxed") is not None


def test_no_warning_when_already_relaxed():
    assert relaxed_mode_warning_if_newly_enabled("relaxed", "relaxed") is None


def test_no_warning_when_switching_to_confirm():
    assert relaxed_mode_warning_if_newly_enabled("relaxed", "confirm") is None


# --- debug logging (bug report: "may be add more console debug errors
# which we can disable later? ... change the debug level so I can help
# with console report?") ----------------------------------------------


@pytest.mark.asyncio
async def test_authorize_outside_allowed_folders_logs_at_info(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="aida.safety")
    guard = SafetyGuard(allowed_roots=[tmp_path / "allowed"], confirm_callback=_approve_all())
    outside = tmp_path / "elsewhere" / "note.txt"

    await guard.authorize_read(outside)

    messages = [r.message for r in caplog.records if r.name == "aida.safety"]
    assert any("outside allowed folders" in m for m in messages)
    assert any("approved" in m for m in messages)


@pytest.mark.asyncio
async def test_authorize_inside_allowed_folders_logs_at_debug(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="aida.safety")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed", confirm_callback=_approve_all())

    # authorize_read bypasses _authorize entirely for in-bounds paths (reads
    # inside allowed folders are never gated — see its docstring), so this
    # exercises authorize_write, which always goes through _authorize and
    # therefore always logs.
    await guard.authorize_write(allowed / "note.txt")

    messages = [r.message for r in caplog.records if r.name == "aida.safety"]
    assert any("inside_allowed_roots=True" in m for m in messages)


# --- authorize_run_script (Phase 9: mode-governed, no allowlist) -----------


@pytest.mark.asyncio
async def test_run_script_relaxed_mode_inside_allowed_needs_no_confirmation(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=deny_all)
    cwd = await guard.authorize_run_script(tmp_path / "script.py")
    assert cwd == (tmp_path / "script.py").resolve()


@pytest.mark.asyncio
async def test_run_script_confirm_mode_inside_allowed_asks_and_honors_approval(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confirm", confirm_callback=_approve_all())
    await guard.authorize_run_script(tmp_path / "script.py")


@pytest.mark.asyncio
async def test_run_script_confirm_mode_inside_allowed_raises_when_declined(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confirm", confirm_callback=deny_all)
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_run_script(tmp_path / "script.py")


@pytest.mark.asyncio
async def test_run_script_outside_allowed_folders_always_confirms(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "elsewhere" / "script.py"
    guard = SafetyGuard(allowed_roots=[allowed], mode="relaxed", confirm_callback=deny_all)
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_run_script(outside)


# --- authorize_execute (Phase 9: command allowlist) -------------------------


@pytest.mark.asyncio
async def test_execute_relaxed_mode_inside_allowed_and_allowlisted_needs_no_confirmation(tmp_path: Path):
    guard = SafetyGuard(
        allowed_roots=[tmp_path], mode="relaxed", confirm_callback=deny_all, command_allowlist=CommandAllowlist(["git status"])
    )
    cwd = await guard.authorize_execute("git status", tmp_path)
    assert cwd == tmp_path.resolve()


@pytest.mark.asyncio
async def test_execute_confirm_mode_inside_allowed_and_allowlisted_asks_and_honors_approval(tmp_path: Path):
    guard = SafetyGuard(
        allowed_roots=[tmp_path],
        mode="confirm",
        confirm_callback=_approve_all(),
        command_allowlist=CommandAllowlist(["git status"]),
    )
    cwd = await guard.authorize_execute("git status", tmp_path)
    assert cwd == tmp_path.resolve()


@pytest.mark.asyncio
async def test_execute_confirm_mode_inside_allowed_and_allowlisted_raises_when_declined(tmp_path: Path):
    guard = SafetyGuard(
        allowed_roots=[tmp_path], mode="confirm", confirm_callback=deny_all, command_allowlist=CommandAllowlist(["git status"])
    )
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_execute("git status", tmp_path)


@pytest.mark.asyncio
async def test_execute_outside_allowed_folders_always_confirms_even_in_relaxed_mode(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    guard = SafetyGuard(
        allowed_roots=[allowed],
        mode="relaxed",
        confirm_callback=deny_all,
        command_allowlist=CommandAllowlist(["git status"]),
    )
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_execute("git status", outside)


@pytest.mark.asyncio
async def test_execute_not_allowlisted_always_confirms_even_in_relaxed_mode(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=deny_all)  # empty allowlist
    with pytest.raises(ConfirmationDenied):
        await guard.authorize_execute("rm -rf /", tmp_path)


@pytest.mark.asyncio
async def test_execute_not_allowlisted_approved_still_runs(tmp_path: Path):
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=_approve_all())
    cwd = await guard.authorize_execute("some-unlisted-command", tmp_path)
    assert cwd == tmp_path.resolve()


@pytest.mark.asyncio
async def test_for_workspace_wires_command_allowlist():
    guard = SafetyGuard.for_workspace(command_allowlist=["git status"])
    assert guard.command_allowlist.is_allowed("git status")
    assert not guard.command_allowlist.is_allowed("ls")
