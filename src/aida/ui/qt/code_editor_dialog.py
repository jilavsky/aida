"""``CodeEditorDialog`` (Phase 9): a syntax-highlighted Python editor with
Save/Save As/Run/Kill — mirrors ``KnowledgeManagementDialog``'s split
between plain disk I/O (Save/Save As write immediately, no separate
confirmation step) and a live action (Run) that goes through ``ChatBridge``
so a real subprocess never blocks the Qt thread.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from aida.coding.runner import DEFAULT_RUN_TIMEOUT_SECONDS, RunResult
from aida.ui.qt._qt import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from aida.ui.qt.python_highlighter import PythonHighlighter


class CodeEditorDialog(QDialog):
    def __init__(
        self,
        *,
        initial_text: str = "",
        saved_scripts_dir: str | None = None,
        python_interpreter: str | None = None,
        bridge=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Code Editor")
        self.resize(760, 560)
        self._saved_scripts_dir = saved_scripts_dir
        self._python_interpreter = python_interpreter
        self._bridge = bridge
        self._current_path: Path | None = None

        layout = QVBoxLayout(self)

        self._editor = QPlainTextEdit(self)
        self._editor.setPlainText(initial_text)
        # Kept alive as an attribute — QSyntaxHighlighter's own C++ object
        # is only kept alive by whatever holds a Python reference to it.
        self._highlighter = PythonHighlighter(self._editor.document())
        layout.addWidget(self._editor, stretch=2)

        buttons = QHBoxLayout()
        self._save_button = QPushButton("Save", self)
        self._save_button.clicked.connect(self._on_save)
        buttons.addWidget(self._save_button)

        self._save_as_button = QPushButton("Save As…", self)
        self._save_as_button.clicked.connect(self._on_save_as)
        buttons.addWidget(self._save_as_button)

        self._run_button = QPushButton("Run", self)
        self._run_button.clicked.connect(self._on_run)
        buttons.addWidget(self._run_button)

        self._kill_button = QPushButton("Kill", self)
        self._kill_button.clicked.connect(self._on_kill)
        self._kill_button.setEnabled(False)
        buttons.addWidget(self._kill_button)
        layout.addLayout(buttons)

        self._output_view = QPlainTextEdit(self)
        self._output_view.setReadOnly(True)
        layout.addWidget(self._output_view, stretch=1)

        if self._bridge is not None:
            self._bridge.script_run_finished.connect(self._on_run_finished)
            self._bridge.script_run_failed.connect(self._on_run_failed)

    def done(self, result: int) -> None:
        """Disconnect from ``self._bridge`` before closing — same leaked-
        connection fix as ``KnowledgeManagementDialog.done``: without this,
        reopening this dialog N times and running once would pop N output
        updates from N still-connected, already-closed instances."""
        if self._bridge is not None:
            for signal, slot in (
                (self._bridge.script_run_finished, self._on_run_finished),
                (self._bridge.script_run_failed, self._on_run_failed),
            ):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(slot)
        super().done(result)

    # --- accessors (mainly for tests) -----------------------------------------

    def text(self) -> str:
        return self._editor.toPlainText()

    def set_text(self, text: str) -> None:
        self._editor.setPlainText(text)

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    def output_text(self) -> str:
        return self._output_view.toPlainText()

    # --- save ------------------------------------------------------------------

    def _on_save(self) -> None:
        if self._current_path is None:
            self._on_save_as()
            return
        self._write_to(self._current_path)

    def _on_save_as(self) -> None:
        default_dir = self._saved_scripts_dir or str(Path.home())
        path_str, _selected_filter = QFileDialog.getSaveFileName(self, "Save Script", default_dir, "Python Files (*.py)")
        if not path_str:
            return
        self._write_to(Path(path_str))

    def _write_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._editor.toPlainText(), encoding="utf-8")
        self._current_path = path
        self.setWindowTitle(f"Code Editor — {path.name}")

    # --- run / kill --------------------------------------------------------------

    def _on_run(self) -> None:
        if self._bridge is None:
            return
        if self._current_path is None:
            self._on_save_as()
            if self._current_path is None:
                return
        else:
            self._write_to(self._current_path)  # always run the latest edits, not a stale save

        self._output_view.setPlainText("Running…")
        self._run_button.setEnabled(False)
        self._kill_button.setEnabled(True)
        self._bridge.run_script(
            str(self._current_path),
            [],
            interpreter=self._python_interpreter,
            cwd=str(self._current_path.parent),
            timeout=DEFAULT_RUN_TIMEOUT_SECONDS,
        )

    def _on_kill(self) -> None:
        if self._bridge is not None:
            self._bridge.cancel_script_run()

    def _on_run_finished(self, result: RunResult) -> None:
        self._run_button.setEnabled(True)
        self._kill_button.setEnabled(False)
        lines = [f"exit code: {result.returncode}", f"duration: {result.duration_seconds:.2f}s"]
        if result.timed_out:
            lines.append("TIMED OUT — process was killed")
        if result.stdout:
            lines.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            lines.append(f"stderr:\n{result.stderr}")
        self._output_view.setPlainText("\n".join(lines))

    def _on_run_failed(self, message: str) -> None:
        self._run_button.setEnabled(True)
        self._kill_button.setEnabled(False)
        self._output_view.setPlainText(f"Error: {message}")


__all__ = ["CodeEditorDialog"]
