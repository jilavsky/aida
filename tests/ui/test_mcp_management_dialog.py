"""Integration tests for aida.ui.qt.mcp_management_dialog.McpManagementDialog
(Phase 7) — built against a real offscreen window + the real mock-mcp
subprocess, same fixture pattern as tests/ui/test_main_window.py and
tests/test_keystone_image_roundtrip.py: everything real except the LLM
(MockProvider).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from aida.config import secrets as secrets_module
from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
    load_settings,
)
from aida.core.confirmation import ConfirmAnswer
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from aida.ui.qt._qt import QDialog, QMessageBox, Qt
from aida.ui.qt.bridge import ChatBridge
from aida.ui.qt.mcp_management_dialog import (
    GroupsDialog,
    McpManagementDialog,
    RawResultDialog,
    ServerFormDialog,
    SkillsBrowserDialog,
    _AddGroupDialog,
    _ToolPermissionRow,
)
from tests.ui._qt_test_utils import pump_until

MOCK_SERVER_PATH = Path(__file__).resolve().parents[1] / "mock_mcp_server.py"


def _settings_with_profile(name: str = "mock-profile") -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(
        name=name, kind="openai_compat", model="mock-model"
    )
    return settings


def _mock_server_config(name: str = "mock-mcp") -> McpServerConfig:
    return McpServerConfig(name=name, command=sys.executable, args=[str(MOCK_SERVER_PATH)])


# --- construction / listing -------------------------------------------------


def test_dialog_with_no_bridge_shows_configured_servers(qapp, aida_home: Path):
    settings = load_settings()
    settings.mcp = McpConfig(servers={"pyirena": McpServerConfig(name="pyirena", command="/opt/x")})
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    assert dialog._server_list.count() == 1
    assert "pyirena" in dialog._server_list.item(0).text()
    assert "stopped" in dialog._server_list.item(0).text()


def test_dialog_with_no_servers_is_empty(qapp, aida_home: Path):
    dialog = McpManagementDialog(load_settings(), None, aida_home / "skills")
    assert dialog._server_list.count() == 0
    assert "no server selected" in dialog._details_label.text()


# --- add / edit / remove (config-only, no bridge needed) --------------------


def test_add_server_persists_to_settings_and_disk(qapp, aida_home: Path):
    from aida.config.settings import load_mcp_config

    settings = load_settings()
    dialog = McpManagementDialog(settings, None, aida_home / "skills")

    form = ServerFormDialog(mcp_config=settings.mcp, skills_dir=aida_home / "skills")
    form._name_edit.setText("pyirena")
    form._command_edit.setText("/opt/pyirena-mcp")
    form._args_edit.setPlainText("--stdio")
    form._env_edit.setPlainText("FOO=bar")
    form._on_add_group()  # no-op, empty text
    form._new_group_edit.setText("analysis")
    form._on_add_group()
    form.accept()

    config = form.result_config()
    assert config.name == "pyirena"
    assert config.groups == ["analysis"]
    assert config.env == {"FOO": "bar"}

    settings.mcp.servers[config.name] = config
    from aida.config.settings import save_mcp_config

    save_mcp_config(settings.mcp)
    dialog._refresh_server_list()

    assert "pyirena" in load_mcp_config(aida_home).servers
    assert dialog._server_list.count() == 1


def test_edit_server_via_dialog_action(qapp, aida_home: Path):
    from aida.config.settings import load_mcp_config

    settings = load_settings()
    settings.mcp = McpConfig(
        servers={"pyirena": McpServerConfig(name="pyirena", command="/old", groups=["a"])}
    )
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog._server_list.setCurrentRow(0)

    form = ServerFormDialog(
        mcp_config=settings.mcp,
        server=settings.mcp.servers["pyirena"],
        skills_dir=aida_home / "skills",
    )
    assert form._name_edit.isReadOnly(), "name must not be changeable on edit"
    form._command_edit.setText("/new")
    updated = form.result_config()
    assert updated.groups == ["a"], "existing groups preserved when the form wasn't touched there"

    settings.mcp.servers["pyirena"] = updated
    from aida.config.settings import save_mcp_config

    save_mcp_config(settings.mcp)
    dialog._refresh_server_list()

    assert load_mcp_config(aida_home).servers["pyirena"].command == "/new"


# --- B6: "Store Value in Keychain" -------------------------------------


def test_store_value_in_keychain_replaces_env_line_with_keyring_ref(
    qapp, aida_home: Path, monkeypatch
):
    """The env editor's new button: pick which KEY, name a secret, confirm
    its value -> the real value goes into the OS keychain and the env text
    is rewritten to reference it, leaving other env lines untouched."""
    from tests.test_secrets import _use_memory_backend

    _use_memory_backend(monkeypatch)
    settings = load_settings()
    form = ServerFormDialog(mcp_config=settings.mcp, skills_dir=aida_home / "skills")
    form._name_edit.setText("pyirena")
    form._env_edit.setPlainText("API_TOKEN=sk-plaintext-value\nOTHER=unchanged")

    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QInputDialog.getItem", lambda *a, **k: ("API_TOKEN", True)
    )
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QInputDialog.getText",
        lambda parent, title, label, *rest, **kw: (
            ("pyirena-token", True) if title == "Secret Name" else ("sk-plaintext-value", True)
        ),
    )
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information", lambda *a, **k: None
    )

    form._on_store_secret_in_keychain()

    assert secrets_module.get_secret("pyirena-token") == "sk-plaintext-value"
    config = form.result_config()
    assert config.env["API_TOKEN"] == "keyring:pyirena-token"
    assert config.env["OTHER"] == "unchanged"


def test_store_value_in_keychain_with_no_env_vars_is_a_safe_noop(
    qapp, aida_home: Path, monkeypatch
):
    settings = load_settings()
    form = ServerFormDialog(mcp_config=settings.mcp, skills_dir=aida_home / "skills")
    informed = []
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information",
        lambda *a, **k: informed.append(True),
    )
    form._on_store_secret_in_keychain()  # must not raise
    assert informed == [True]


def test_remove_server_with_confirmation(qapp, aida_home: Path, monkeypatch):
    from aida.config.settings import load_mcp_config

    settings = load_settings()
    settings.mcp = McpConfig(servers={"pyirena": McpServerConfig(name="pyirena", command="/x")})
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog._server_list.setCurrentRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._on_remove()

    assert dialog._server_list.count() == 0
    assert "pyirena" not in load_mcp_config(aida_home).servers


def test_remove_server_declined_keeps_it(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()
    settings.mcp = McpConfig(servers={"pyirena": McpServerConfig(name="pyirena", command="/x")})
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog._server_list.setCurrentRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog._on_remove()

    assert dialog._server_list.count() == 1


# --- import ---------------------------------------------------------------


def test_import_from_file_adds_new_server(qapp, aida_home: Path, tmp_path: Path, monkeypatch):
    settings = load_settings()
    dialog = McpManagementDialog(settings, None, aida_home / "skills")

    config_file = tmp_path / "claude_desktop.json"
    config_file.write_text(
        '{"mcpServers": {"bait": {"command": "/opt/bait-mcp", "disabled": false}}}'
    )
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(config_file), ""),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    dialog._on_import()

    assert "bait" in settings.mcp.servers
    assert settings.mcp.servers["bait"].extra == {"disabled": False}
    assert dialog._server_list.count() == 1


def test_import_conflict_prompts_and_respects_no(
    qapp, aida_home: Path, tmp_path: Path, monkeypatch
):
    settings = load_settings()
    settings.mcp = McpConfig(
        servers={"pyirena": McpServerConfig(name="pyirena", command="/existing")}
    )
    dialog = McpManagementDialog(settings, None, aida_home / "skills")

    config_file = tmp_path / "import.json"
    config_file.write_text('{"mcpServers": {"pyirena": {"command": "/imported"}}}')
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(config_file), ""),
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    dialog._on_import()

    assert settings.mcp.servers["pyirena"].command == "/existing", (
        "declining the overwrite prompt must not clobber"
    )


# --- groups editor ----------------------------------------------------------


def test_groups_dialog_add_creates_a_brand_new_group_from_selected_servers(
    qapp, aida_home: Path, monkeypatch
):
    """Regression: the Groups dialog had Rename/Delete but no way to
    actually create a new group short of opening a server's own edit form
    and typing it there, one server at a time."""
    settings = load_settings()
    settings.mcp = McpConfig(
        servers={
            "pyirena": McpServerConfig(name="pyirena", command="/x", groups=["analysis"]),
            "bait": McpServerConfig(name="bait", command="/y"),
        }
    )
    changed = []
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: changed.append(True))

    def fake_exec(self):
        self._name_edit.setText("everything")
        for row in range(self._servers_list.count()):
            self._servers_list.item(row).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_AddGroupDialog, "exec", fake_exec)
    dialog._on_add()

    assert settings.mcp.servers["pyirena"].groups == ["analysis", "everything"]
    assert settings.mcp.servers["bait"].groups == ["everything"]
    assert "everything" in [
        dialog._list.item(r).text().split("  —  ")[0] for r in range(dialog._list.count())
    ]
    assert changed == [True]


def test_groups_dialog_add_cancelled_makes_no_changes(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()
    settings.mcp = McpConfig(servers={"pyirena": McpServerConfig(name="pyirena", command="/x")})
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: None)

    monkeypatch.setattr(_AddGroupDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    dialog._on_add()

    assert settings.mcp.servers["pyirena"].groups == []


def test_groups_dialog_add_with_no_servers_configured_warns_instead_of_opening(
    qapp, aida_home: Path, monkeypatch
):
    settings = load_settings()
    settings.mcp = McpConfig(servers={})
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: None)

    opened = []
    monkeypatch.setattr(_AddGroupDialog, "exec", lambda self: opened.append(True))
    warned = []
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information",
        lambda *a, **k: warned.append(True),
    )
    dialog._on_add()

    assert warned == [True]
    assert opened == []  # never even constructed/shown the picker


def test_groups_dialog_rename(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()
    settings.mcp = McpConfig(
        servers={"pyirena": McpServerConfig(name="pyirena", command="/x", groups=["analysis"])}
    )
    changed = []
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: changed.append(True))
    dialog._list.setCurrentRow(0)

    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QInputDialog.getText", lambda *a, **k: ("full", True)
    )
    dialog._on_rename()

    assert settings.mcp.servers["pyirena"].groups == ["full"]
    assert changed == [True]


def test_groups_dialog_delete(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()
    settings.mcp = McpConfig(
        servers={"pyirena": McpServerConfig(name="pyirena", command="/x", groups=["analysis"])}
    )
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: None)
    dialog._list.setCurrentRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._on_delete()

    assert settings.mcp.servers["pyirena"].groups == []


# --- estimated tool count per group (PLAN.md §1.5) --------------------------


class _StubMcpManager:
    """Just enough of McpManager's public surface for _tool_count_for /
    GroupsDialog._tool_count_suffix — no real session, no subprocess."""

    def __init__(self, running: dict[str, list[str]]) -> None:
        self._running = running

    @property
    def running_server_names(self) -> list[str]:
        return list(self._running)

    def tool_names(self, name: str) -> list[str]:
        return self._running.get(name, [])


class _StubBridge:
    def __init__(self, mcp_manager) -> None:
        self.mcp_manager = mcp_manager


def test_groups_dialog_sums_tool_counts_across_running_members(qapp, aida_home: Path):
    settings = load_settings()
    settings.mcp = McpConfig(
        servers={
            "a": McpServerConfig(name="a", command="/x", groups=["g1"]),
            "b": McpServerConfig(name="b", command="/y", groups=["g1"]),
        }
    )
    bridge = _StubBridge(_StubMcpManager({"a": ["t1", "t2"], "b": ["t3"]}))
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: None, bridge=bridge)

    text = dialog._list.item(0).text()
    assert "3 tools" in text


def test_groups_dialog_notes_partial_running_members(qapp, aida_home: Path):
    settings = load_settings()
    settings.mcp = McpConfig(
        servers={
            "a": McpServerConfig(name="a", command="/x", groups=["g1"]),
            "b": McpServerConfig(name="b", command="/y", groups=["g1"]),
        }
    )
    bridge = _StubBridge(_StubMcpManager({"a": ["t1"]}))  # b not running
    dialog = GroupsDialog(settings.mcp, on_changed=lambda: None, bridge=bridge)

    text = dialog._list.item(0).text()
    assert "1 tool" in text
    assert "1/2 running" in text


def test_groups_dialog_notes_when_nothing_is_running(qapp, aida_home: Path):
    settings = load_settings()
    settings.mcp = McpConfig(servers={"a": McpServerConfig(name="a", command="/x", groups=["g1"])})

    dialog = GroupsDialog(settings.mcp, on_changed=lambda: None)  # no bridge at all

    text = dialog._list.item(0).text()
    assert "not running" in text


# --- live start/stop against a real mock-mcp subprocess ---------------------


def _make_bridge(qapp, loop_thread, settings, monkeypatch, script) -> ChatBridge:
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider(script))
    bridge = ChatBridge(loop_thread)
    bridge.start(settings, profile_name="mock-profile")
    # One real wait. The line that used to precede this ended in `or True`,
    # so it returned immediately and pumped nothing — the wait below was
    # always doing the work by itself.
    assert pump_until(qapp, lambda: bridge.session is not None, timeout=10.0)
    return bridge


def test_start_and_stop_a_server_updates_status_and_tools(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    settings = _settings_with_profile()
    settings.mcp = McpConfig(servers={"mock-mcp": _mock_server_config()})
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")])
    dialog = McpManagementDialog(settings, bridge, aida_home / "skills")
    try:
        dialog._server_list.setCurrentRow(0)
        assert "stopped" in dialog._server_list.item(0).text()

        dialog._on_start()
        assert pump_until(
            qapp, lambda: "running" in dialog._server_list.item(0).text(), timeout=10.0
        )
        assert any(k.startswith("mock-mcp__") for k in bridge.session.tools), (
            "live tools merged into the session"
        )

        dialog._server_list.setCurrentRow(0)
        assert pump_until(
            qapp,
            lambda: any(isinstance(r, _ToolPermissionRow) for r in dialog._tool_rows),
            timeout=5.0,
        )

        dialog._on_stop()
        assert pump_until(
            qapp, lambda: "stopped" in dialog._server_list.item(0).text(), timeout=10.0
        )
        assert not any(k.startswith("mock-mcp__") for k in bridge.session.tools), (
            "tools removed from the live session"
        )
    finally:
        bridge.shutdown()


def test_disabled_tool_is_absent_from_the_next_turns_schemas(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """ "Disable one pyirena tool; verify the model no longer sees it" —
    Phase 7 acceptance criterion, automated against the mock server."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(servers={"mock-mcp": _mock_server_config()})
    bridge = _make_bridge(
        qapp,
        loop_thread,
        settings,
        monkeypatch,
        [
            MockTurn(text="ok", tool_calls=[MockToolCall(name="mock-mcp__echo_text", id="c1")]),
            MockTurn(text="done"),
        ],
    )
    dialog = McpManagementDialog(settings, bridge, aida_home / "skills")
    try:
        dialog._server_list.setCurrentRow(0)
        dialog._on_start()
        assert pump_until(
            qapp, lambda: "running" in dialog._server_list.item(0).text(), timeout=10.0
        )

        dialog._server_list.setCurrentRow(0)
        assert pump_until(
            qapp,
            lambda: any(isinstance(r, _ToolPermissionRow) for r in dialog._tool_rows),
            timeout=5.0,
        )
        row = next(
            r
            for r in dialog._tool_rows
            if isinstance(r, _ToolPermissionRow) and r.tool_name == "always_fails"
        )
        row.enabled_checkbox.setChecked(False)
        dialog._on_save_tool_permissions()

        assert "always_fails" in settings.mcp.servers["mock-mcp"].disabled_tools
        assert pump_until(
            qapp,
            lambda: (
                "mock-mcp__always_fails" not in bridge.session.tools
                and "mock-mcp__echo_text" in bridge.session.tools
            ),
            timeout=10.0,
        )
    finally:
        bridge.shutdown()


def test_confirm_flagged_tool_triggers_the_modal_even_in_relaxed_workspace(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """ "Mark a bait_mcp write-tool 'confirm before run'; confirmation
    appears even in a relaxed workspace" — the MCP per-tool confirm gate is
    independent of SafetyGuard's own mode. Reuses the same
    _on_confirmation_requested modal path already proven for file-safety
    confirmations (see tests/ui/test_main_window.py's own coverage)."""
    from aida.config.settings import WorkspaceConfig, WorkspacesConfig
    from aida.ui.qt.main_window import MainWindow

    settings = _settings_with_profile()
    settings.mcp = McpConfig(servers={"mock-mcp": _mock_server_config()})
    settings.mcp.servers["mock-mcp"].confirm_tools = ["echo_text"]
    settings.mcp.servers["mock-mcp"].groups = ["analysis"]
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "ws": WorkspaceConfig(
                name="ws", profile="mock-profile", mcp_group="analysis", safety="relaxed"
            )
        }
    )

    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(text="ok", tool_calls=[MockToolCall(name="mock-mcp__echo_text", id="c1")]),
                MockTurn(text="done"),
            ]
        ),
    )
    asked: list[str] = []

    def _fake_ask_confirmation(self, request):
        asked.append(request.detail)
        return ConfirmAnswer.ALLOW_ONCE

    monkeypatch.setattr(
        "aida.ui.qt.main_window.MainWindow._ask_confirmation", _fake_ask_confirmation
    )

    window = MainWindow(settings, loop_thread, start_kwargs={"workspace_name": "ws"})
    try:
        assert pump_until(
            qapp, lambda: window.statusBar().currentMessage().startswith("Ready"), timeout=10.0
        )
        window.input_box.set_text("echo something")
        window.input_box._send_button.click()
        assert pump_until(
            qapp,
            lambda: any("confirm" in t.lower() or "echo_text" in t for t in asked),
            timeout=10.0,
        )
    finally:
        window.close()


def test_breaking_a_server_on_purpose_shows_error_status_and_why(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """ "Break a server on purpose (bad path): status shows error, log
    panel shows why" — Phase 7 acceptance criterion."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(
        servers={
            "broken": McpServerConfig(name="broken", command="definitely-not-a-real-executable")
        }
    )
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")])
    dialog = McpManagementDialog(settings, bridge, aida_home / "skills")
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: None
    )  # the failure dialog itself isn't under test here
    try:
        dialog._server_list.setCurrentRow(0)
        dialog._on_start()

        assert pump_until(qapp, lambda: "error" in dialog._server_list.item(0).text(), timeout=10.0)
        dialog._server_list.setCurrentRow(0)
        assert "error:" in dialog._details_label.text()
        assert "broken" in dialog._details_label.text()
    finally:
        bridge.shutdown()


def test_raw_inspector_shows_image_content_for_a_plot_call(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """ "Raw inspector shows the exact ImageContent response for a plot
    call" — Phase 7 acceptance criterion, exercised against the mock
    server's get_image tool (stands in for a real pyirena plot call, same
    substitution every prior phase's mock-mcp tests make)."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(servers={"mock-mcp": _mock_server_config()})
    bridge = _make_bridge(
        qapp,
        loop_thread,
        settings,
        monkeypatch,
        [
            MockTurn(text="ok", tool_calls=[MockToolCall(name="mock-mcp__get_image", id="c1")]),
            MockTurn(text="done"),
        ],
    )
    dialog = McpManagementDialog(settings, bridge, aida_home / "skills")
    try:
        dialog._server_list.setCurrentRow(0)
        dialog._on_start()
        assert pump_until(
            qapp, lambda: "running" in dialog._server_list.item(0).text(), timeout=10.0
        )

        bridge.send("plot it")
        assert pump_until(
            qapp,
            lambda: any(
                record.tool_name == "get_image"
                for _server, record in bridge.mcp_manager.recent_calls()
            ),
            timeout=10.0,
        )
        dialog._refresh_log_tab(
            "mock-mcp"
        )  # the log tab only refreshes on selection/status-change, not every call

        record = next(r for r in dialog._log_records if r.tool_name == "get_image")
        raw = RawResultDialog(record)
        text = raw._view.toPlainText()
        payload = json.loads(text)
        image_entries = [c for c in payload["content"] if c["type"] == "image"]
        assert len(image_entries) == 1
        assert image_entries[0]["mime_type"] == "image/png"
        assert image_entries[0]["base64_length"] > 0
        assert (
            "base64" not in text.lower().split("base64_length")[0][-20:]
        )  # never the raw data itself
    finally:
        bridge.shutdown()


# --- regression: real pyIrena/Argo bug reports ------------------------------


def test_tools_tab_is_scrollable_not_ever_growing(qapp, aida_home: Path):
    """Bug report: opening a server's Tools tab with 100+ tools "scales
    the panel as high as list of tools" — unusable. The tab's row list must
    live inside a QScrollArea, not directly in a plain layout the tab
    (and therefore the whole dialog) would grow to fit."""
    from aida.ui.qt._qt import QScrollArea

    settings = load_settings()
    settings.mcp = McpConfig(
        servers={
            "pyirena": McpServerConfig(
                name="pyirena", command="/x", disabled_tools=[f"tool_{i}" for i in range(150)]
            )
        }
    )
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog._server_list.setCurrentRow(0)

    assert len(dialog._tool_rows) == 150
    scroll_areas = dialog._tabs.findChildren(QScrollArea)
    assert scroll_areas, "the Tools tab must contain a QScrollArea"
    assert scroll_areas[0].widget() is dialog._tools_container
    assert scroll_areas[0].widgetResizable() is True


def test_closing_the_dialog_disconnects_it_from_the_bridge(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """Bug report: "Test connection spawns 4 dialogs with OK button, not
    just one" — opening the MCP Servers dialog repeatedly (without closing
    it disconnecting from the bridge first) left every previous instance
    still subscribed to mcp_connection_tested, so one Test Connection click
    popped one modal per still-connected leaked dialog. A closed dialog
    must stop reacting to bridge signals entirely."""
    settings = _settings_with_profile()
    settings.mcp = McpConfig(servers={"mock-mcp": _mock_server_config()})
    bridge = _make_bridge(qapp, loop_thread, settings, monkeypatch, [MockTurn(text="hi")])
    try:
        first = McpManagementDialog(settings, bridge, aida_home / "skills")
        first.done(0)  # simulate closing it, same hook QDialog.exec()/close() go through
        second = McpManagementDialog(settings, bridge, aida_home / "skills")

        popped: list[str] = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: popped.append("info"))
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: popped.append("warn"))

        second._server_list.setCurrentRow(0)
        second._on_test()
        assert pump_until(qapp, lambda: len(popped) >= 1, timeout=10.0)

        assert len(popped) == 1, f"expected exactly one dialog, got {len(popped)}"
    finally:
        bridge.shutdown()


# --- one-click pyIrena setup ---------------------------------------------


def test_add_pyirena_explains_the_install_when_nothing_is_found(qapp, aida_home, monkeypatch):
    """A dead-end message is the failure mode this button exists to avoid —
    it must say what to install, not just "not found"."""
    from aida.config.settings import load_settings

    monkeypatch.setattr("aida.ui.qt.mcp_management_dialog.find_pyirena_mcp", list)
    shown: list[tuple] = []
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args),
    )

    settings = load_settings()
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog.add_pyirena()

    assert shown, "the user must be told something"
    assert 'pip install "pyirena[mcp]"' in shown[0][2]
    assert not settings.mcp.servers


def test_add_pyirena_confirms_before_writing_anything(qapp, aida_home, monkeypatch):
    """An MCP server is code AIDA launches on this machine — declining the
    confirmation must leave mcp.json untouched."""
    from aida.config.settings import load_settings
    from aida.mcp.pyirena_setup import PyirenaMcpCandidate
    from aida.ui.qt._qt import QMessageBox

    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.find_pyirena_mcp",
        lambda: [PyirenaMcpCandidate(command="/opt/pyirena-mcp", source="PATH")],
    )
    monkeypatch.setattr("aida.ui.qt.mcp_management_dialog.pyirena_version", lambda _c: "1.1.0")
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    settings = load_settings()
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog.add_pyirena()

    assert not settings.mcp.servers


def test_add_pyirena_writes_the_server_and_installs_its_skills(qapp, aida_home, monkeypatch):
    from aida.config.settings import load_settings
    from aida.mcp.pyirena_setup import PyirenaMcpCandidate
    from aida.ui.qt._qt import QMessageBox

    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.find_pyirena_mcp",
        lambda: [PyirenaMcpCandidate(command="/opt/pyirena-mcp", source="PATH")],
    )
    monkeypatch.setattr("aida.ui.qt.mcp_management_dialog.pyirena_version", lambda _c: "1.1.0")
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information", lambda *a, **k: None
    )

    settings = load_settings()
    dialog = McpManagementDialog(settings, None, aida_home / "skills")
    dialog.add_pyirena()

    server = settings.mcp.servers["pyirena"]
    assert server.command == "/opt/pyirena-mcp"
    assert server.groups == ["pyirena-analysis"]
    assert "pyirena-usage" in server.skills


# --- Skills browser: install bundled skills (PLAN.md §1.5) -----------------


def test_skills_browser_install_bundled_copies_the_shipped_samples(
    qapp, aida_home: Path, monkeypatch
):
    infos = []
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information",
        lambda self, title, text: infos.append((title, text)),
    )

    skills_dir = aida_home / "skills"
    dialog = SkillsBrowserDialog(skills_dir)
    dialog._on_install_bundled()

    assert (skills_dir / "saxs-basics.md").exists()
    assert (skills_dir / "pyirena-usage.md").exists()
    assert (skills_dir / "review-checklist.md").exists()
    names = {dialog._list.item(i).text() for i in range(dialog._list.count())}
    assert {"saxs-basics", "pyirena-usage", "review-checklist"} <= names
    assert len(infos) == 1
    assert infos[0][0] == "Skills Installed"


def test_skills_browser_install_bundled_a_second_time_says_nothing_to_do(
    qapp, aida_home: Path, monkeypatch
):
    infos = []
    monkeypatch.setattr(
        "aida.ui.qt.mcp_management_dialog.QMessageBox.information",
        lambda self, title, text: infos.append((title, text)),
    )

    skills_dir = aida_home / "skills"
    dialog = SkillsBrowserDialog(skills_dir)
    dialog._on_install_bundled()
    infos.clear()

    dialog._on_install_bundled()

    assert len(infos) == 1
    assert infos[0][0] == "Nothing To Install"
