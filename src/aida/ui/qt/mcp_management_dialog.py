"""``McpManagementDialog`` (Phase 7, planning/phase07_mcp_management.md):
add/edit/remove MCP servers, per-tool permissions, groups, skills, and
diagnostics — entirely from the GUI, no manual ``mcp.json`` editing.

Structural precedent this file follows throughout: ``ConversationsSidebar``'s
``_ids_by_row: list[str]`` pattern for the server list; ``SettingsDialog``'s
self-contained ``QFormLayout`` + ``QDialogButtonBox`` form for
``ServerFormDialog``; ``CleanupDialog`` living inside its main widget's
module rather than its own file. Unlike ``ConversationsSidebar`` (a "dumb"
widget whose actions are all just signals ``MainWindow`` acts on),
``McpManagementDialog`` persists ``mcp.json`` edits itself
(``save_mcp_config``) the moment they happen — there is no "Save to
Workspace"-style deferred-commit step here, since every action here (add a
server, flip a permission) is already a single, complete unit of work with
nothing else it needs to be batched against.

Live start/stop/restart/test-connection go through the ``ChatBridge``
passed in at construction (its ``mcp_server_status_changed``/
``mcp_server_action_failed``/``mcp_connection_tested`` signals drive this
dialog's refreshes) so a server added here can be started in the *current*
session without restarting the whole chat.

**Raw result inspector scope (documented, not silently dropped):** reachable
from the Log tab only in this pass. Making it reachable by clicking a
tool-call row inside the live chat transcript would need a ``call_id``
threaded through every ``NativeTool``'s function signature
(``aida.core.tools.ToolFunc``, and every module that builds one) — a much
larger, higher-risk change than one nice-to-have justifies; see
planning/phase07_mcp_management.md's review notes.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from aida.config.paths import install_bundled_skills
from aida.config.secrets import set_secret
from aida.config.settings import McpConfig, McpServerConfig, Settings, save_mcp_config
from aida.core.context import list_skills
from aida.mcp.config_io import merge_mcp_config
from aida.mcp.groups import add_group, delete_group, known_group_names, rename_group, resolve_group
from aida.mcp.manager import ConnectionTestResult
from aida.mcp.pyirena_setup import DEFAULT_SERVER_NAME as PYIRENA_SERVER_NAME
from aida.mcp.pyirena_setup import find_pyirena_mcp, pyirena_server_config, pyirena_version
from aida.mcp.server import ToolCallRecord
from aida.ui.qt._qt import (
    QAbstractItemView,
    QCheckBox,
    QDesktopServices,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    Qt,
    QTabWidget,
    QTextBrowser,
    QUrl,
    QVBoxLayout,
    QWidget,
)


def _parse_kv_lines(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if sep:
            env[key.strip()] = value
    return env


def _replace_env_value(text: str, key: str, new_value: str) -> str:
    """Rewrite just ``key``'s line in a ``KEY=VALUE``-per-line env block,
    leaving every other line (order, spacing, unrelated values) untouched —
    used by "Store in Keychain" so swapping one value for a ``keyring:``
    reference doesn't disturb the rest of what the user typed."""
    lines = text.splitlines()
    out = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        k, sep, _v = stripped.partition("=")
        if not replaced and sep and k.strip() == key:
            out.append(f"{key}={new_value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={new_value}")
    return "\n".join(out)


def _status_for(name: str, bridge) -> str:
    manager = bridge.mcp_manager if bridge is not None else None
    if manager is None:
        return "stopped"
    if name in manager.running_server_names:
        return "running"
    if name in manager.start_errors:
        return "error"
    return "stopped"


# --- Add/Edit server sub-dialog ---------------------------------------------


class ServerFormDialog(QDialog):
    """Add (``server=None``) or edit (``server`` given) one MCP server.

    Env values default masked when editing an existing server (there's
    something to hide the moment the dialog opens) and unmasked when adding
    a new one (nothing to hide yet, and the user needs to type them in).
    Masking is display-only — swapping the visible text between the real
    ``KEY=value`` lines and ``KEY=***`` placeholders — because
    ``QPlainTextEdit`` (needed for multiple env vars) has no per-widget
    echo mode the way ``QLineEdit`` does; this is the "simplification of
    the plan's 'values maskable' item" called out in the phase file rather
    than a per-row masked grid.
    """

    def __init__(
        self,
        *,
        mcp_config: McpConfig,
        server: McpServerConfig | None = None,
        skills_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = server is not None
        self._existing = server
        self.setWindowTitle("Edit MCP Server" if self._is_edit else "Add MCP Server")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(server.name if server else "", self)
        self._name_edit.setReadOnly(self._is_edit)  # name is the identity; not renameable in-place
        form.addRow("Name:", self._name_edit)

        self._command_edit = QLineEdit(server.command if server else "", self)
        form.addRow("Command:", self._command_edit)

        self._args_edit = QPlainTextEdit("\n".join(server.args) if server else "", self)
        self._args_edit.setPlaceholderText("One argument per line, e.g.\n--stdio")
        form.addRow("Args:", self._args_edit)

        self._raw_env_text = "\n".join(f"{k}={v}" for k, v in (server.env if server else {}).items())
        self._env_edit = QPlainTextEdit(self._raw_env_text, self)
        self._env_edit.setPlaceholderText("KEY=VALUE, one per line")
        form.addRow("Env:", self._env_edit)

        env_options_row = QHBoxLayout()
        self._hide_values_checkbox = QCheckBox("Hide values", self)
        self._hide_values_checkbox.toggled.connect(self._on_hide_toggled)
        env_options_row.addWidget(self._hide_values_checkbox)
        # B6: env values previously had no home but plaintext mcp.json — the
        # one remaining hole in "secrets never touch YAML/JSON" (every other
        # secret already goes through aida.config.secrets via secret_ref).
        # This stores the real value in the OS keychain and swaps the env
        # line for a "keyring:NAME" reference, which McpServerHandle.start()
        # resolves back to the real value at launch time.
        store_secret_button = QPushButton("Store Value in Keychain…", self)
        store_secret_button.clicked.connect(self._on_store_secret_in_keychain)
        env_options_row.addWidget(store_secret_button)
        env_options_row.addStretch(1)
        form.addRow("", env_options_row)
        if self._is_edit and server and server.env:
            self._hide_values_checkbox.setChecked(True)

        self._groups_list = QListWidget(self)
        for name in known_group_names(mcp_config):
            item = QListWidgetItem(name, self._groups_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if server and name in server.groups else Qt.CheckState.Unchecked
            )
        form.addRow("Groups:", self._groups_list)
        add_group_row = QHBoxLayout()
        self._new_group_edit = QLineEdit(self)
        self._new_group_edit.setPlaceholderText("New group name…")
        add_group_button = QPushButton("Add", self)
        add_group_button.clicked.connect(self._on_add_group)
        add_group_row.addWidget(self._new_group_edit)
        add_group_row.addWidget(add_group_button)
        form.addRow("", add_group_row)

        self._skills_list = QListWidget(self)
        for info in list_skills(skills_dir):
            item = QListWidgetItem(info.name, self._skills_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if server and info.name in server.skills else Qt.CheckState.Unchecked
            )
        form.addRow("Skills:", self._skills_list)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_hide_toggled(self, checked: bool) -> None:
        if checked:
            self._raw_env_text = self._env_edit.toPlainText()
            masked = "\n".join(
                f"{line.split('=', 1)[0]}=***" for line in self._raw_env_text.splitlines() if line.strip()
            )
            self._env_edit.setPlainText(masked)
            self._env_edit.setReadOnly(True)
        else:
            self._env_edit.setPlainText(self._raw_env_text)
            self._env_edit.setReadOnly(False)

    def _on_store_secret_in_keychain(self) -> None:
        current_text = self._raw_env_text if self._hide_values_checkbox.isChecked() else self._env_edit.toPlainText()
        env = _parse_kv_lines(current_text)
        if not env:
            QMessageBox.information(self, "No Env Vars", "Add an env var (KEY=VALUE) first.")
            return

        key, ok = QInputDialog.getItem(self, "Store in Keychain", "Env var:", list(env.keys()), editable=False)
        if not ok or not key:
            return

        current_value = env[key]
        already_ref = current_value.startswith(("keyring:", "secret:"))
        default_name = f"{self._name_edit.text().strip() or 'mcp'}_{key}".lower()
        secret_name, ok = QInputDialog.getText(self, "Secret Name", "Store under this name in the OS keychain:", text=default_name)
        secret_name = secret_name.strip()
        if not ok or not secret_name:
            return

        value, ok = QInputDialog.getText(
            self,
            "Secret Value",
            f"Value for {key}:",
            QLineEdit.EchoMode.Password,
            "" if already_ref else current_value,
        )
        if not ok or not value:
            return

        set_secret(secret_name, value)
        self._raw_env_text = _replace_env_value(current_text, key, f"keyring:{secret_name}")
        if self._hide_values_checkbox.isChecked():
            self._on_hide_toggled(True)  # refresh the masked view from the new _raw_env_text
        else:
            self._env_edit.setPlainText(self._raw_env_text)
        QMessageBox.information(self, "Stored", f"{key} now references keyring secret {secret_name!r}.")

    def _on_add_group(self) -> None:
        name = self._new_group_edit.text().strip()
        if not name:
            return
        for row in range(self._groups_list.count()):
            if self._groups_list.item(row).text() == name:
                self._groups_list.item(row).setCheckState(Qt.CheckState.Checked)
                self._new_group_edit.clear()
                return
        item = QListWidgetItem(name, self._groups_list)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self._new_group_edit.clear()

    def _checked_items(self, widget: QListWidget) -> list[str]:
        return [
            widget.item(row).text()
            for row in range(widget.count())
            if widget.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A server needs a name.")
            return
        self.accept()

    def env_dict(self) -> dict[str, str]:
        text = self._raw_env_text if self._hide_values_checkbox.isChecked() else self._env_edit.toPlainText()
        return _parse_kv_lines(text)

    def result_config(self) -> McpServerConfig:
        return McpServerConfig(
            name=self._name_edit.text().strip(),
            command=self._command_edit.text().strip(),
            args=[line.strip() for line in self._args_edit.toPlainText().splitlines() if line.strip()],
            env=self.env_dict(),
            groups=self._checked_items(self._groups_list),
            skills=self._checked_items(self._skills_list),
            disabled_tools=self._existing.disabled_tools if self._existing else [],
            confirm_tools=self._existing.confirm_tools if self._existing else [],
            extra=self._existing.extra if self._existing else {},
        )


# --- Raw result inspector (Log tab) -----------------------------------------


class RawResultDialog(QDialog):
    """One MCP call's exact recorded content — JSON, base64 lengths noted,
    copyable. See this module's docstring for why this is reachable from
    the Log tab only in this pass."""

    def __init__(self, record: ToolCallRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Raw Result — {record.tool_name}")
        layout = QVBoxLayout(self)

        payload = {
            "tool_name": record.tool_name,
            "duration_seconds": round(record.duration_seconds, 3),
            "is_error": record.is_error,
            "error_message": record.error_message,
            "arguments": record.arguments,
            "content": record.content_preview,
        }
        text = json.dumps(payload, indent=2, default=str)

        self._view = QTextBrowser(self)
        self._view.setPlainText(text)
        layout.addWidget(self._view)

        buttons = QHBoxLayout()
        copy_button = QPushButton("Copy", self)
        copy_button.clicked.connect(lambda: self._copy(text))
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _copy(self, text: str) -> None:
        from aida.ui.qt._qt import QGuiApplication

        QGuiApplication.clipboard().setText(text)


# --- Groups editor -----------------------------------------------------------


class _AddGroupDialog(QDialog):
    """Prompts for a new group name plus which configured servers belong
    to it. The only way to bring a brand-new group into existence: a group
    has no separate registry (see ``aida.mcp.groups``'s docstring — it's
    derived purely from server membership), so a zero-member group can't
    be represented at all, and this dialog's OK button is disabled until
    at least one server is checked."""

    def __init__(self, mcp_config: McpConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Group")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Group name:", self))
        self._name_edit = QLineEdit(self)
        layout.addWidget(self._name_edit)

        layout.addWidget(QLabel("Servers in this group:", self))
        self._servers_list = QListWidget(self)
        for name in sorted(mcp_config.servers):
            item = QListWidgetItem(name, self._servers_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(self._servers_list)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name Required", "A group needs a name.")
            return
        if not self.selected_servers():
            QMessageBox.warning(self, "No Servers Selected", "Check at least one server to add to the group.")
            return
        self.accept()

    def group_name(self) -> str:
        return self._name_edit.text().strip()

    def selected_servers(self) -> list[str]:
        return [
            self._servers_list.item(row).text()
            for row in range(self._servers_list.count())
            if self._servers_list.item(row).checkState() == Qt.CheckState.Checked
        ]


class GroupsDialog(QDialog):
    """Add/rename/delete groups — a group has no separate registry (it's
    derived from who references it, see ``aida.mcp.groups``'s docstring),
    so this dialog is a thin front end over ``add_group``/``rename_group``/
    ``delete_group``.
    """

    def __init__(self, mcp_config: McpConfig, *, on_changed, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MCP Groups")
        self._mcp_config = mcp_config
        self._on_changed = on_changed

        layout = QVBoxLayout(self)
        self._list = QListWidget(self)
        layout.addWidget(self._list)
        self._refresh()

        buttons = QHBoxLayout()
        add_button = QPushButton("Add Group…", self)
        add_button.clicked.connect(self._on_add)
        buttons.addWidget(add_button)
        rename_button = QPushButton("Rename…", self)
        rename_button.clicked.connect(self._on_rename)
        buttons.addWidget(rename_button)
        delete_button = QPushButton("Delete…", self)
        delete_button.clicked.connect(self._on_delete)
        buttons.addWidget(delete_button)
        layout.addLayout(buttons)

        close_row = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_row.rejected.connect(self.accept)
        close_row.accepted.connect(self.accept)
        layout.addWidget(close_row)

    def _refresh(self) -> None:
        self._list.clear()
        for name in known_group_names(self._mcp_config):
            members = sorted(s.name for s in resolve_group(self._mcp_config, name))
            self._list.addItem(f"{name}  —  {', '.join(members)}")

    def _selected_group_name(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.text().split("  —  ")[0]

    def _on_add(self) -> None:
        if not self._mcp_config.servers:
            QMessageBox.information(
                self, "No Servers Configured", "Add an MCP server first — a group needs at least one member."
            )
            return
        dialog = _AddGroupDialog(self._mcp_config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.group_name()
        if name in known_group_names(self._mcp_config):
            answer = QMessageBox.question(
                self,
                "Group Already Exists",
                f"{name!r} already exists — add the selected server(s) to it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        add_group(self._mcp_config, name, dialog.selected_servers())
        save_mcp_config(self._mcp_config)
        self._refresh()
        self._on_changed()

    def _on_rename(self) -> None:
        old = self._selected_group_name()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename Group", f"Rename {old!r} to:")
        if not ok or not new.strip():
            return
        rename_group(self._mcp_config, old, new.strip())
        save_mcp_config(self._mcp_config)
        self._refresh()
        self._on_changed()

    def _on_delete(self) -> None:
        name = self._selected_group_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Delete Group",
            f"Remove group {name!r} from every server that references it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_group(self._mcp_config, name)
        save_mcp_config(self._mcp_config)
        self._refresh()
        self._on_changed()


# --- Skills browser ------------------------------------------------------


_SKILL_TEMPLATE = """# {name}

Describe what this skill teaches the model here.
"""


class SkillsBrowserDialog(QDialog):
    """List/preview/open-externally/new-from-template over ``~/.aida/skills/``
    (Phase 7). Per-server skills linkage is edited directly in
    ``ServerFormDialog``; per-workspace *extra* skills selection stays
    CLI-only (``aida workspace edit --skills``) — building a full GUI
    workspace editor is a pre-existing Phase 5 gap
    (``aida.ui.qt.main_window``'s own comments already note "no GUI 'new
    workspace' form exists yet"), out of scope to fix here.
    """

    def __init__(self, skills_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Skills")
        self._skills_dir = skills_dir

        layout = QVBoxLayout(self)
        self._list = QListWidget(self)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list)

        self._preview = QTextBrowser(self)
        layout.addWidget(self._preview)

        buttons = QHBoxLayout()
        self._open_button = QPushButton("Open in External Editor", self)
        self._open_button.clicked.connect(self._on_open_external)
        buttons.addWidget(self._open_button)
        new_button = QPushButton("New From Template…", self)
        new_button.clicked.connect(self._on_new_from_template)
        buttons.addWidget(new_button)
        layout.addLayout(buttons)

        close_row = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_row.rejected.connect(self.accept)
        close_row.accepted.connect(self.accept)
        layout.addWidget(close_row)

        self._refresh()

    def _refresh(self) -> None:
        self._list.clear()
        for info in list_skills(self._skills_dir):
            item = QListWidgetItem(info.name, self._list)
            item.setData(Qt.ItemDataRole.UserRole, str(info.path))

    def _selected_path(self) -> Path | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def _on_selection_changed(self, *_args: object) -> None:
        path = self._selected_path()
        if path is None:
            self._preview.setPlainText("")
            return
        try:
            self._preview.setMarkdown(path.read_text(encoding="utf-8"))
        except OSError as exc:
            self._preview.setPlainText(f"[could not read {path}: {exc}]")

    def _on_open_external(self) -> None:
        path = self._selected_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_new_from_template(self) -> None:
        name, ok = QInputDialog.getText(self, "New Skill", "Skill name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        path = self._skills_dir / f"{name}.md"
        if path.exists():
            QMessageBox.warning(self, "Already Exists", f"A skill named {name!r} already exists.")
            return
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(_SKILL_TEMPLATE.format(name=name), encoding="utf-8")
        self._refresh()


# --- Tools tab row -----------------------------------------------------------


class _ToolPermissionRow(QWidget):
    """One tool's enable/confirm checkboxes in the Tools tab."""

    def __init__(self, tool_name: str, *, disabled: bool, confirm: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tool_name = tool_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel(tool_name, self), stretch=1)
        self.enabled_checkbox = QCheckBox("Enabled", self)
        self.enabled_checkbox.setChecked(not disabled)
        layout.addWidget(self.enabled_checkbox)
        self.confirm_checkbox = QCheckBox("Confirm before run", self)
        self.confirm_checkbox.setChecked(confirm)
        layout.addWidget(self.confirm_checkbox)


# --- Main dialog ---------------------------------------------------------


class McpManagementDialog(QDialog):
    def __init__(self, settings: Settings, bridge, skills_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MCP Servers")
        self.resize(760, 520)
        self._settings = settings
        self._bridge = bridge
        self._skills_dir = skills_dir
        self._tool_rows: list[_ToolPermissionRow] = []

        outer = QHBoxLayout(self)

        left = QVBoxLayout()
        self._server_list = QListWidget(self)
        self._server_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._server_list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        left.addWidget(self._server_list)

        server_buttons = QVBoxLayout()
        for label, handler in [
            ("Add Server…", self._on_add),
            ("Add pyIrena…", self._on_add_pyirena),
            ("Remove…", self._on_remove),
            ("Start", self._on_start),
            ("Stop", self._on_stop),
            ("Restart", self._on_restart),
            ("Test Connection", self._on_test),
            ("Import mcp.json…", self._on_import),
            ("Groups…", self._on_groups),
            ("Skills…", self._on_skills),
        ]:
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            server_buttons.addWidget(button)
        server_buttons.addStretch(1)
        left.addLayout(server_buttons)
        outer.addLayout(left, stretch=1)

        right = QVBoxLayout()
        self._tabs = QTabWidget(self)

        self._details_box = QGroupBox("Details", self)
        details_layout = QVBoxLayout(self._details_box)
        self._details_label = QLabel(self)
        self._details_label.setWordWrap(True)
        self._details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_label)
        edit_button = QPushButton("Edit…", self)
        edit_button.clicked.connect(self._on_edit)
        details_layout.addWidget(edit_button)
        details_layout.addStretch(1)
        self._tabs.addTab(self._details_box, "Details")

        tools_tab = QWidget(self)
        tools_tab_layout = QVBoxLayout(tools_tab)
        tools_save = QPushButton("Save Tool Permissions", self)
        tools_save.clicked.connect(self._on_save_tool_permissions)
        tools_tab_layout.addWidget(tools_save)

        # A real pyIrena server can expose 100+ tools — without a scroll
        # area, one checkbox row per tool grew the tab (and the whole
        # dialog) tall enough to be unusable ("scales the panel as high as
        # list of tools"). The Save button stays outside the scroll area so
        # it's always reachable without scrolling back up.
        self._tools_container = QWidget(self)
        self._tools_layout = QVBoxLayout(self._tools_container)
        self._tools_layout.addStretch(1)
        tools_scroll = QScrollArea(self)
        tools_scroll.setWidgetResizable(True)
        tools_scroll.setWidget(self._tools_container)
        tools_tab_layout.addWidget(tools_scroll)

        self._tabs.addTab(tools_tab, "Tools")

        self._log_list = QListWidget(self)
        self._log_list.itemDoubleClicked.connect(self._on_log_double_clicked)
        self._log_records: list[ToolCallRecord] = []
        self._tabs.addTab(self._log_list, "Log")

        right.addWidget(self._tabs)
        outer.addLayout(right, stretch=2)

        if self._bridge is not None:
            self._bridge.mcp_server_status_changed.connect(self._on_status_changed)
            self._bridge.mcp_server_action_failed.connect(self._on_action_failed)
            self._bridge.mcp_connection_tested.connect(self._on_connection_tested)

        self._refresh_server_list()

    def done(self, result: int) -> None:
        """Disconnect from ``self._bridge`` before closing.

        Bug report: "Test connection spawns 4 dialogs with OK button, not
        just one." Root cause: `MainWindow.open_mcp_management_dialog`
        constructs a *new* `McpManagementDialog` every time the toolbar
        action is clicked, and each instance connected itself to the
        long-lived `bridge`'s signals in `__init__` — but nothing ever
        disconnected a closed dialog. A signal connection keeps the bound
        method's `self` (the dialog) alive, so every previously-opened,
        already-closed dialog instance stayed subscribed forever; opening
        the dialog N times and then clicking "Test Connection" once popped
        N modal message boxes, one per leaked instance, all reacting to the
        same single `mcp_connection_tested` emission. Same class of bug
        `aida.ui.qt.main_window.MainWindow._unwire_bridge_signals` already
        exists to prevent for bridge replacement; this is the equivalent
        fix for a dialog's own lifetime instead.
        """
        if self._bridge is not None:
            # Each disconnect is independently guarded — done() can
            # re-enter, and one signal already being disconnected must not
            # short-circuit the other two.
            for signal, slot in (
                (self._bridge.mcp_server_status_changed, self._on_status_changed),
                (self._bridge.mcp_server_action_failed, self._on_action_failed),
                (self._bridge.mcp_connection_tested, self._on_connection_tested),
            ):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(slot)
        super().done(result)

    # --- rendering -----------------------------------------------------------

    def _configs(self) -> dict[str, McpServerConfig]:
        return self._settings.mcp.servers

    def _selected_name(self) -> str | None:
        item = self._server_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_server_list(self) -> None:
        previous = self._selected_name()
        self._server_list.clear()
        for name in sorted(self._configs()):
            status = _status_for(name, self._bridge)
            item = QListWidgetItem(f"{name}  [{status}]", self._server_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == previous:
                self._server_list.setCurrentItem(item)
        if self._server_list.currentItem() is None and self._server_list.count():
            self._server_list.setCurrentRow(0)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        name = self._selected_name()
        server = self._configs().get(name) if name else None
        if server is None:
            self._details_label.setText("(no server selected)")
            self._clear_tool_rows()
            self._log_list.clear()
            return

        status = _status_for(name, self._bridge)
        detail_lines = [
            f"name: {server.name}",
            f"status: {status}",
            f"command: {server.command}",
            f"args: {' '.join(server.args) or '(none)'}",
            f"env: {', '.join(server.env) or '(none)'}",
            f"groups: {', '.join(server.groups) or '(none)'}",
            f"skills: {', '.join(server.skills) or '(none)'}",
        ]
        if status == "error":
            manager = self._bridge.mcp_manager if self._bridge is not None else None
            error = manager.start_errors.get(name) if manager is not None else None
            if error:
                # "Break a server on purpose: status shows error, log panel
                # shows why" — the failure dialog (_on_action_failed)
                # already surfaces this once, but it's gone the moment the
                # user dismisses it; showing it here too means re-selecting
                # the server keeps the reason visible.
                detail_lines.append(f"error: {error}")
        self._details_label.setText("\n".join(detail_lines))
        self._refresh_tools_tab(server)
        self._refresh_log_tab(name)

    def _clear_tool_rows(self) -> None:
        for row in self._tool_rows:
            row.setParent(None)
            row.deleteLater()
        self._tool_rows = []

    def _live_tool_names(self, name: str) -> list[str]:
        manager = self._bridge.mcp_manager if self._bridge is not None else None
        return manager.tool_names(name) if manager is not None else []

    def _refresh_tools_tab(self, server: McpServerConfig) -> None:
        self._clear_tool_rows()
        known_names = sorted(set(self._live_tool_names(server.name)) | set(server.disabled_tools) | set(server.confirm_tools))
        if not known_names:
            hint = QLabel("Start or Test Connection to discover this server's tools.", self._tools_container)
            self._tools_layout.insertWidget(self._tools_layout.count() - 1, hint)
            self._tool_rows.append(hint)  # reused as a "row" purely so _clear_tool_rows tears it down too
            return
        for tool_name in known_names:
            row = _ToolPermissionRow(
                tool_name,
                disabled=tool_name in server.disabled_tools,
                confirm=tool_name in server.confirm_tools,
                parent=self._tools_container,
            )
            self._tools_layout.insertWidget(self._tools_layout.count() - 1, row)
            self._tool_rows.append(row)

    def _refresh_log_tab(self, name: str) -> None:
        self._log_list.clear()
        self._log_records = []
        manager = self._bridge.mcp_manager if self._bridge is not None else None
        if manager is None:
            return
        for server_name, record in manager.recent_calls():
            if server_name != name:
                continue
            status = "error" if record.is_error else "ok"
            self._log_list.addItem(f"{record.tool_name}  {status}  {record.duration_seconds:.2f}s")
            self._log_records.append(record)

    # --- server actions --------------------------------------------------

    def _on_add(self) -> None:
        dialog = ServerFormDialog(mcp_config=self._settings.mcp, skills_dir=self._skills_dir, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.result_config()
        if config.name in self._configs():
            QMessageBox.warning(self, "Already Exists", f"A server named {config.name!r} already exists.")
            return
        self._settings.mcp.servers[config.name] = config
        save_mcp_config(self._settings.mcp)
        if self._bridge is not None:
            self._bridge.register_mcp_server(config)
        self._refresh_server_list()

    def add_pyirena(self) -> None:
        """Public entry point for the one-click pyIrena setup, so another
        dialog (``OnboardingDialog``) can trigger it without reaching for a
        private method or reimplementing any of it."""
        self._on_add_pyirena()

    def _on_add_pyirena(self) -> None:
        """One-click setup for the one MCP server this audience is
        practically guaranteed to want.

        Adding pyIrena by hand through ``ServerFormDialog`` means knowing
        that the executable is called ``pyirena-mcp``, finding its
        *absolute* path (a GUI app inherits no shell ``PATH``, so a bare
        name fails to launch with a confusing error), and knowing which
        env vars, group, and skills to attach. All of that is mechanical —
        see ``aida.mcp.pyirena_setup``. This button does it, then shows
        exactly what it configured rather than silently writing config:
        an MCP server is code AIDA will launch on this machine, so the
        user confirms before anything is saved.
        """
        candidates = find_pyirena_mcp()
        if not candidates:
            QMessageBox.information(
                self,
                "pyIrena Not Found",
                "No pyirena-mcp installation was found on this machine.\n\n"
                'Install it with:\n    pip install "pyirena[mcp]"\n\n'
                "It can go in this environment or in its own conda environment — AIDA "
                "talks to it over stdio, so they do not have to share an interpreter. "
                "Then click this button again, or use “Add Server…” and point it at "
                "the pyirena-mcp executable.",
            )
            return

        candidate = candidates[0]
        if len(candidates) > 1:
            labels = [found.display for found in candidates]
            choice, accepted = QInputDialog.getItem(
                self, "Choose pyIrena Installation", "Several were found — use:", labels, 0, False
            )
            if not accepted:
                return
            candidate = candidates[labels.index(choice)]

        name = PYIRENA_SERVER_NAME
        if name in self._configs():
            answer = QMessageBox.question(
                self,
                "Already Configured",
                f"An MCP server named {name!r} already exists. Replace its configuration "
                "with the freshly detected one?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        data_root = self._suggested_pyirena_data_root()
        config = pyirena_server_config(candidate, name=name, data_root=data_root)
        version = pyirena_version(candidate)

        summary = [
            f"Command:  {' '.join([config.command, *config.args])}",
            f"Found in: {candidate.source}",
            f"Version:  pyIrena {version}" if version else "Version:  (could not determine)",
            f"Group:    {', '.join(config.groups) or '(none)'}",
            f"Skills:   {', '.join(config.skills) or '(none)'}",
        ]
        if config.env:
            summary.append("Env:      " + ", ".join(f"{k}={v}" for k, v in config.env.items()))
        answer = QMessageBox.question(
            self,
            "Add pyIrena MCP Server",
            "AIDA will add this MCP server and launch it as a subprocess when a "
            "workspace enables it:\n\n" + "\n".join(summary) + "\n\nAdd it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._settings.mcp.servers[config.name] = config
        save_mcp_config(self._settings.mcp)
        installed = install_bundled_skills(config.skills)
        if self._bridge is not None:
            self._bridge.register_mcp_server(config)
        self._refresh_server_list()

        note = (
            f"\n\nInstalled the {', '.join(installed)} skill file(s) into your skills folder."
            if installed
            else ""
        )
        QMessageBox.information(
            self,
            "pyIrena Added",
            f"Configured {config.name!r} in the {', '.join(config.groups) or 'default'} group."
            f"{note}\n\nNext: set a workspace's MCP group to "
            f"{config.groups[0] if config.groups else 'none'!r} so the tools are actually "
            "offered to the model, then use “Test Connection” here to check it starts.",
        )

    def _suggested_pyirena_data_root(self) -> str | None:
        """The active workspace's first source folder, if there is one.

        ``PYIRENA_DATA_ROOT`` restricts every file pyirena-mcp can touch to
        one subtree — pyIrena's own docs call it strongly recommended when
        the server is exposed to an AI agent — and a workspace's source
        folder is by definition the data the user meant to work on. Falls
        back to unset (pyIrena's own default of "any absolute path") rather
        than guessing a path the user never named."""
        session = getattr(self._bridge, "session", None) if self._bridge is not None else None
        workspace_name = getattr(getattr(session, "recorder", None), "workspace_name", None)
        workspace = self._settings.workspaces.workspaces.get(workspace_name) if workspace_name else None
        if workspace and workspace.source_folders:
            return workspace.source_folders[0]
        return None

    def _on_edit(self) -> None:
        name = self._selected_name()
        server = self._configs().get(name) if name else None
        if server is None:
            return
        dialog = ServerFormDialog(mcp_config=self._settings.mcp, server=server, skills_dir=self._skills_dir, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_config()
        self._settings.mcp.servers[name] = updated
        save_mcp_config(self._settings.mcp)
        if self._bridge is not None:
            self._bridge.register_mcp_server(updated)
        self._refresh_server_list()

    def _on_remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Remove Server",
            f"Remove MCP server {name!r}? This stops it if running.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._settings.mcp.servers[name]
        save_mcp_config(self._settings.mcp)
        if self._bridge is not None:
            self._bridge.unregister_mcp_server(name)
        self._refresh_server_list()

    def _register_selected_with_bridge(self, server: McpServerConfig) -> None:
        """Every action that (re)connects to a server must first make sure
        the live ``McpManager`` actually knows its config — not just a
        server added via ``_on_add`` in *this* dialog session.
        ``_on_add``/``_on_edit`` already call ``register_mcp_server``, but a
        server loaded from ``mcp.json`` at app startup (the common case —
        every server the user has ever configured) was never registered
        with a fresh, lazily-created manager, so "Start" on it raised
        McpServerError('... is not configured') from
        ``McpManager.start_server``. Re-registering here is idempotent (a
        plain dict assignment) and also picks up any edit made since the
        manager last saw this config."""
        if self._bridge is not None:
            self._bridge.register_mcp_server(server)

    def _on_start(self) -> None:
        name = self._selected_name()
        server = self._configs().get(name) if name else None
        if server is not None and self._bridge is not None:
            self._register_selected_with_bridge(server)
            self._bridge.start_mcp_server(name)

    def _on_stop(self) -> None:
        name = self._selected_name()
        if name and self._bridge is not None:
            self._bridge.stop_mcp_server(name)

    def _on_restart(self) -> None:
        name = self._selected_name()
        server = self._configs().get(name) if name else None
        if server is not None and self._bridge is not None:
            self._register_selected_with_bridge(server)
            self._bridge.restart_mcp_server(name)

    def _on_test(self) -> None:
        name = self._selected_name()
        server = self._configs().get(name) if name else None
        if server is not None and self._bridge is not None:
            self._register_selected_with_bridge(server)
            self._bridge.test_mcp_connection(server)

    def _on_save_tool_permissions(self) -> None:
        name = self._selected_name()
        server = self._configs().get(name) if name else None
        if server is None:
            return
        disabled = [row.tool_name for row in self._tool_rows if isinstance(row, _ToolPermissionRow) and not row.enabled_checkbox.isChecked()]
        confirm = [row.tool_name for row in self._tool_rows if isinstance(row, _ToolPermissionRow) and row.confirm_checkbox.isChecked()]
        updated = McpServerConfig(
            name=server.name,
            command=server.command,
            args=server.args,
            env=server.env,
            groups=server.groups,
            skills=server.skills,
            disabled_tools=disabled,
            confirm_tools=confirm,
            extra=server.extra,
        )
        self._settings.mcp.servers[name] = updated
        save_mcp_config(self._settings.mcp)
        if self._bridge is not None:
            self._bridge.register_mcp_server(updated)
            # Disabled/confirm-flagged tools are only applied when a
            # server's tools are (re)built (McpManager._tools_for, at
            # start/restart time) — a server already running has its old
            # tool set already merged into the live session, and
            # registering the new config alone doesn't retroactively
            # re-filter it. Restarting is what makes a permission change
            # actually take effect immediately rather than only on the
            # next full session restart.
            if _status_for(name, self._bridge) == "running":
                self._bridge.restart_mcp_server(name)
        self._refresh_detail()

    def _on_import(self) -> None:
        path_str, _filter = QFileDialog.getOpenFileName(self, "Import mcp.json", "", "JSON (*.json)")
        if not path_str:
            return
        try:
            raw = json.loads(Path(path_str).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import Failed", f"Could not read {path_str}: {exc}")
            return

        result = merge_mcp_config(self._settings.mcp, raw)
        conflicts = result.skipped
        overwrite: set[str] = set()
        if conflicts:
            answer = QMessageBox.question(
                self,
                "Conflicting Servers",
                f"{len(conflicts)} server(s) already exist and were skipped: {', '.join(conflicts)}.\n\n"
                "Overwrite them with the imported versions instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                overwrite = set(conflicts)
                result = merge_mcp_config(self._settings.mcp, raw, overwrite=overwrite)

        self._settings.mcp = result.config
        save_mcp_config(self._settings.mcp)
        QMessageBox.information(
            self,
            "Import Complete",
            f"Added: {', '.join(result.added) or '(none)'}\nOverwritten: {', '.join(result.overwritten) or '(none)'}",
        )
        self._refresh_server_list()

    def _on_groups(self) -> None:
        dialog = GroupsDialog(self._settings.mcp, on_changed=self._refresh_server_list, parent=self)
        dialog.exec()

    def _on_skills(self) -> None:
        dialog = SkillsBrowserDialog(self._skills_dir, parent=self)
        dialog.exec()

    def _on_log_double_clicked(self, item: QListWidgetItem) -> None:
        row = self._log_list.row(item)
        if 0 <= row < len(self._log_records):
            RawResultDialog(self._log_records[row], parent=self).exec()

    # --- bridge signal handlers --------------------------------------------

    def _on_status_changed(self, _name: str) -> None:
        self._refresh_server_list()

    def _on_action_failed(self, name: str, error: str) -> None:
        QMessageBox.warning(self, "MCP Action Failed", f"{name}: {error}")
        self._refresh_server_list()

    def _on_connection_tested(self, name: str, result: ConnectionTestResult) -> None:
        if result.ok:
            QMessageBox.information(self, "Connection OK", f"{name}: {result.tool_count} tool(s), {result.elapsed_seconds:.2f}s")
        else:
            QMessageBox.warning(self, "Connection Failed", f"{name}: {result.error}")
        self._refresh_detail()


__all__ = ["GroupsDialog", "McpManagementDialog", "RawResultDialog", "ServerFormDialog", "SkillsBrowserDialog"]
