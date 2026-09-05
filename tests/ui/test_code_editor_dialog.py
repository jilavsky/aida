"""Tests for aida.ui.qt.code_editor_dialog.CodeEditorDialog — Save/Save As
against real disk, Run/Kill against a real ChatBridge (no session needed —
run_script/cancel_script_run don't touch bridge.session), mirroring
test_knowledge_management_dialog.py's "real bridge, real background loop"
convention for live actions."""

from __future__ import annotations

from pathlib import Path

from aida.ui.qt.bridge import ChatBridge
from aida.ui.qt.code_editor_dialog import CodeEditorDialog
from tests.ui._qt_test_utils import pump_until


class _NullSignal:
    """Stand-in for a Qt Signal on a fake bridge that never actually needs
    to fire one — just enough for CodeEditorDialog's constructor/``done()``
    to connect()/disconnect() against without a real QObject."""

    def connect(self, _slot) -> None:
        pass

    def disconnect(self, _slot) -> None:
        pass


def test_dialog_seeds_initial_text(qapp):
    dialog = CodeEditorDialog(initial_text="print('hi')")
    assert dialog.text() == "print('hi')"


def test_set_text_replaces_content(qapp):
    dialog = CodeEditorDialog(initial_text="old")
    dialog.set_text("new")
    assert dialog.text() == "new"


# --- initial_path / Open… (bug report: "code editor has no way in" for an
# agent-written file, and no way to load an existing file at all) ----------


def test_dialog_seeds_from_initial_path(qapp, tmp_path: Path):
    script = tmp_path / "reduce.py"
    script.write_text("print('from disk')", encoding="utf-8")
    dialog = CodeEditorDialog(initial_path=script)
    assert dialog.text() == "print('from disk')"
    assert dialog.current_path == script
    assert "reduce.py" in dialog.windowTitle()


def test_save_after_initial_path_writes_to_that_same_file_not_save_as(qapp, tmp_path: Path):
    """The whole point of opening a real file (vs. initial_text) — Save
    must act on it directly, no Save As round trip."""
    script = tmp_path / "reduce.py"
    script.write_text("v1", encoding="utf-8")
    dialog = CodeEditorDialog(initial_path=script)
    dialog.set_text("v2")
    dialog._on_save()
    assert script.read_text(encoding="utf-8") == "v2"


def test_on_open_loads_the_chosen_file(qapp, monkeypatch, tmp_path: Path):
    script = tmp_path / "existing.py"
    script.write_text("print('opened')", encoding="utf-8")
    dialog = CodeEditorDialog(initial_text="blank editor")
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getOpenFileName",
        lambda *a, **kw: (str(script), ""),
    )
    dialog._on_open()
    assert dialog.text() == "print('opened')"
    assert dialog.current_path == script
    assert "existing.py" in dialog.windowTitle()


def test_on_open_cancelled_leaves_the_editor_unchanged(qapp, monkeypatch):
    dialog = CodeEditorDialog(initial_text="unchanged")
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getOpenFileName", lambda *a, **kw: ("", "")
    )
    dialog._on_open()
    assert dialog.text() == "unchanged"
    assert dialog.current_path is None


def test_save_with_no_path_falls_back_to_save_as(qapp, monkeypatch, tmp_path: Path):
    dialog = CodeEditorDialog(initial_text="print(1)", saved_scripts_dir=str(tmp_path))
    target = tmp_path / "script.py"
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(target), ""),
    )
    dialog._on_save()
    assert target.read_text(encoding="utf-8") == "print(1)"
    assert dialog.current_path == target


def test_save_as_cancelled_does_not_write(qapp, monkeypatch, tmp_path: Path):
    dialog = CodeEditorDialog(initial_text="print(1)", saved_scripts_dir=str(tmp_path))
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName", lambda *a, **kw: ("", "")
    )
    dialog._on_save_as()
    assert dialog.current_path is None
    assert list(tmp_path.iterdir()) == []


def test_save_after_save_as_writes_to_the_same_path(qapp, monkeypatch, tmp_path: Path):
    dialog = CodeEditorDialog(initial_text="v1", saved_scripts_dir=str(tmp_path))
    target = tmp_path / "script.py"
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(target), ""),
    )
    dialog._on_save_as()
    dialog.set_text("v2")
    dialog._on_save()
    assert target.read_text(encoding="utf-8") == "v2"


def test_run_with_no_bridge_is_a_safe_noop(qapp, tmp_path: Path):
    dialog = CodeEditorDialog(initial_text="print(1)", saved_scripts_dir=str(tmp_path))
    dialog._on_run()  # must not raise
    assert dialog.output_text() == ""


def test_run_uses_the_configured_workspace_timeout_not_the_hardcoded_default(
    qapp, tmp_path: Path, monkeypatch
):
    """B5: previously always passed DEFAULT_RUN_TIMEOUT_SECONDS (30s) to
    bridge.run_script regardless of what the active workspace configured —
    the "Run" button ignored the same script_timeout_seconds a
    run_python_script tool call now respects."""

    class _FakeBridge:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.script_run_finished = _NullSignal()
            self.script_run_failed = _NullSignal()

        def run_script(self, path, args, *, interpreter, cwd, timeout):
            self.calls.append({"path": path, "timeout": timeout})

    bridge = _FakeBridge()
    dialog = CodeEditorDialog(
        initial_text="print(1)",
        saved_scripts_dir=str(tmp_path),
        bridge=bridge,
        script_timeout_seconds=180.0,
    )
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(tmp_path / "s.py"), ""),
    )
    dialog._on_run()
    assert bridge.calls[0]["timeout"] == 180.0


def test_run_saves_first_then_shows_output(qapp, loop_thread, tmp_path: Path, monkeypatch):
    bridge = ChatBridge(loop_thread)
    target = tmp_path / "script.py"
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(target), ""),
    )
    dialog = CodeEditorDialog(
        initial_text="print('hello from run')", saved_scripts_dir=str(tmp_path), bridge=bridge
    )

    dialog._on_run()
    assert target.exists()  # saved before running
    assert pump_until(qapp, lambda: "hello from run" in dialog.output_text(), timeout=10.0)
    assert dialog._run_button.isEnabled()  # re-enabled after finishing
    assert not dialog._kill_button.isEnabled()


def test_kill_terminates_a_sleeping_script(qapp, loop_thread, tmp_path: Path, monkeypatch):
    # Deliberately a short sleep (not e.g. 30s): if the kill ever silently
    # fails to fire, this test still finishes in a few seconds via the
    # script's own natural completion (and fails on the duration assertion
    # below) instead of blocking the whole suite for however long a longer
    # sleep would have run.
    bridge = ChatBridge(loop_thread)
    target = tmp_path / "sleep.py"
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(target), ""),
    )
    dialog = CodeEditorDialog(
        initial_text="import time; time.sleep(3)", saved_scripts_dir=str(tmp_path), bridge=bridge
    )

    dialog._on_run()
    assert pump_until(qapp, lambda: dialog._kill_button.isEnabled(), timeout=5.0)
    dialog._on_kill()

    assert pump_until(qapp, lambda: not dialog._kill_button.isEnabled(), timeout=8.0)
    assert "exit code:" in dialog.output_text()


def test_done_disconnects_bridge_signals(qapp, loop_thread, tmp_path: Path, monkeypatch):
    bridge = ChatBridge(loop_thread)
    dialog = CodeEditorDialog(bridge=bridge)
    dialog.done(0)
    # A second dialog on the same bridge must be the only one reacting now —
    # if the first dialog's slots were still connected, this would raise
    # (calling a method on a since-deleted C++ QPlainTextEdit) or double-fire.
    target = tmp_path / "script.py"
    monkeypatch.setattr(
        "aida.ui.qt.code_editor_dialog.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(target), ""),
    )
    dialog2 = CodeEditorDialog(
        initial_text="print(1)", saved_scripts_dir=str(tmp_path), bridge=bridge
    )
    dialog2._on_run()
    assert pump_until(qapp, lambda: dialog2.output_text() != "", timeout=10.0)
