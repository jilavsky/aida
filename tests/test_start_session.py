"""Tests for aida.cli.chat.start_session — the shared session-startup logic
Phase 4 added for both ``aida chat`` and ``aida conversations resume``.

Focuses on the parts that are genuinely new/risky: workspace-vs-explicit-flag
precedence, the error paths (unknown profile/workspace/conversation), and
resume rebuilding history + defaulting workspace/profile from the stored
conversation. McpManager itself is already covered end-to-end (real
subprocess) by test_mcp_manager.py, so here it's faked out to keep these
tests fast and to isolate start_session's own precedence logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.chat import (
    ChatSession,
    UnknownProfileError,
    UnknownWorkspaceError,
    _build_parser,
    _ensure_workspace_folders,
    start_session,
)
from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.persistence.recorder import ConversationNotFoundError
from aida.providers.mock import MockProvider, MockTurn


class _FakeMcpManager:
    """Stands in for aida.mcp.manager.McpManager so these tests don't spawn
    real subprocesses — McpManager's own behavior is covered by
    test_mcp_manager.py. Records what it was constructed with so tests can
    assert on precedence."""

    instances: list[_FakeMcpManager] = []

    def __init__(self, servers, *, artifact_store=None) -> None:
        self.servers = servers
        self.artifact_store = artifact_store
        self.running_server_names: list[str] = [s.name for s in servers]
        self.start_errors: dict[str, str] = {}
        self.closed = False
        _FakeMcpManager.instances.append(self)

    async def start_all(self) -> dict:
        return {}

    def skills(self) -> list[str]:
        return []

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_mcp_instances():
    _FakeMcpManager.instances.clear()
    yield
    _FakeMcpManager.instances.clear()


def _settings(**overrides) -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.providers.profiles["ws-profile"] = ProviderProfile(
        name="ws-profile", kind="openai_compat", model="ws-model"
    )
    settings.mcp = McpConfig(
        servers={
            "ws-server": McpServerConfig(name="ws-server", command="ws-mcp", groups=["ws-group"]),
            "explicit-server": McpServerConfig(name="explicit-server", command="explicit-mcp", groups=["other"]),
        }
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _workspace(**overrides) -> WorkspaceConfig:
    defaults = dict(
        name="use-ws",
        profile="ws-profile",
        mcp_group="ws-group",
        skills=[],
        source_folders=[],
        target_folder=None,
        safety="confirm",
        system_prompt="You are a workspace assistant.",
    )
    defaults.update(overrides)
    return WorkspaceConfig(**defaults)


@pytest.mark.asyncio
async def test_start_session_explicit_profile_no_workspace_creates_recorder(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()

    session, mcp_manager = await start_session(settings, profile_name="mock-profile")
    try:
        assert isinstance(session, ChatSession)
        assert session.profile_name == "mock-profile"
        assert session.recorder is not None
        assert mcp_manager is None  # no --mcp/--mcp-group/workspace given, none started
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_unknown_profile_raises(aida_home: Path, records_home: Path):
    settings = _settings()
    with pytest.raises(UnknownProfileError):
        await start_session(settings, profile_name="does-not-exist")


@pytest.mark.asyncio
async def test_start_session_unknown_workspace_raises(aida_home: Path, records_home: Path):
    settings = _settings(workspaces=WorkspacesConfig())
    with pytest.raises(UnknownWorkspaceError):
        await start_session(settings, workspace_name="does-not-exist")


@pytest.mark.asyncio
async def test_start_session_workspace_supplies_profile_prompt_and_mcp(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))
    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert session.profile_name == "ws-profile"
        assert session.messages[0].role == "system"
        assert "workspace assistant" in session.messages[0].content
        assert mcp_manager is not None
        assert [s.name for s in mcp_manager.servers] == ["ws-server"]
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_explicit_profile_overrides_workspace_profile(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))
    session, mcp_manager = await start_session(
        settings, workspace_name="use-ws", profile_name="mock-profile"
    )
    try:
        assert session.profile_name == "mock-profile"  # explicit flag wins over workspace's
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_explicit_mcp_overrides_workspace_group(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))
    session, mcp_manager = await start_session(
        settings, workspace_name="use-ws", mcp_names=["explicit-server"]
    )
    try:
        # workspace's ws-group (-> ws-server) is ignored; only the explicit
        # server list is honored.
        assert [s.name for s in mcp_manager.servers] == ["explicit-server"]
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_resume_loads_history_and_defaults_workspace_profile(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="reply 1")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))

    first_session, first_mcp = await start_session(settings, workspace_name="use-ws")
    conv_id = first_session.recorder.conversation_id
    _ = [e async for e in first_session.send("remember this")]
    await first_session.aclose()
    if first_mcp is not None:
        await first_mcp.aclose()

    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="reply 2")]))
    resumed_session, resumed_mcp = await start_session(settings, resume_conversation_id=conv_id)
    try:
        assert resumed_session.recorder.conversation_id == conv_id
        assert any(m.content == "remember this" for m in resumed_session.messages)
        # workspace/profile were not re-specified — resumed from the stored conversation.
        assert resumed_session.profile_name == "ws-profile"
        assert resumed_mcp is not None
        assert [s.name for s in resumed_mcp.servers] == ["ws-server"]
    finally:
        await resumed_session.aclose()


@pytest.mark.asyncio
async def test_start_session_resume_unknown_conversation_raises(aida_home: Path, records_home: Path):
    settings = _settings()
    with pytest.raises(ConversationNotFoundError):
        await start_session(settings, resume_conversation_id="does-not-exist")


# --- Phase 6: SafetyGuard construction + file/document tool merging --------


@pytest.mark.asyncio
async def test_start_session_merges_file_and_document_tools_by_default(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Even with no workspace configured, start_session always wires the
    native file/document tools in (against an empty allowed-folders set —
    everything just requires confirmation, per SafetyGuard's outside-bounds
    behavior)."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()

    session, _mcp_manager = await start_session(settings, profile_name="mock-profile")
    try:
        for name in ("list_directory", "read_file", "write_file", "delete_file"):
            assert name in session.tools
        assert "write_markdown_report" in session.tools
        assert "write_docx_report" in session.tools
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_relaxed_workspace_allows_writes_without_confirmation(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """The SafetyGuard built from the workspace's own source/target folders
    + safety mode is what actually gates the merged file tools — a
    'relaxed' workspace should let a write inside its own target folder
    through without ever calling the confirm callback."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    target = tmp_path / "target"
    target.mkdir()
    ws = _workspace(target_folder=str(target), safety="relaxed")
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    confirm_calls = []

    async def _never_called(request):
        confirm_calls.append(request)
        return False

    session, mcp_manager = await start_session(
        settings, workspace_name="use-ws", confirm_callback=_never_called
    )
    try:
        result = await session.tools["write_file"].func(
            {"path": str(target / "note.txt"), "content": "hello"}
        )
        assert not result.is_error
        assert (target / "note.txt").read_text(encoding="utf-8") == "hello"
        assert confirm_calls == []  # relaxed mode + inside allowed folder -> no prompt
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_confirm_workspace_requires_confirmation_inside_folder(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """'confirm' mode (the default) gates writes even *inside* the
    workspace's own allowed folders — the custom confirm_callback passed to
    start_session must be the one consulted."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    target = tmp_path / "target"
    target.mkdir()
    ws = _workspace(target_folder=str(target), safety="confirm")
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    confirm_calls = []

    async def _approve(request):
        confirm_calls.append(request)
        return True

    session, mcp_manager = await start_session(
        settings, workspace_name="use-ws", confirm_callback=_approve
    )
    try:
        result = await session.tools["write_file"].func(
            {"path": str(target / "note.txt"), "content": "hello"}
        )
        assert not result.is_error
        assert len(confirm_calls) == 1
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_global_allowed_folders_apply_without_workspace(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """settings.app.allowed_folders (Phase 6) is layered on even when no
    workspace is in play at all."""
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    shared = tmp_path / "shared"
    shared.mkdir()
    settings = _settings()
    settings.app.allowed_folders = [str(shared)]
    settings.app.default_safety_mode = "relaxed"

    confirm_calls = []

    async def _never_called(request):
        confirm_calls.append(request)
        return False

    session, _mcp_manager = await start_session(
        settings, profile_name="mock-profile", confirm_callback=_never_called
    )
    try:
        result = await session.tools["write_file"].func(
            {"path": str(shared / "note.txt"), "content": "hello"}
        )
        assert not result.is_error
        assert confirm_calls == []
    finally:
        await session.aclose()


def test_build_parser_accepts_workspace_flag():
    args = _build_parser().parse_args(["--workspace", "use-ws"])
    assert args.workspace == "use-ws"
    assert args.profile == ""


def test_build_parser_profile_optional_when_workspace_given():
    # Phase 2/3 required --profile; Phase 4 makes it optional since a
    # workspace can supply one instead.
    args = _build_parser().parse_args([])
    assert args.profile == ""
    assert args.workspace == ""


# --- Phase 6 bugfix: auto-create a workspace's source/target folders ------
# ("Can we create the folders if they do not exist? I need to populate them
# at some point.")


@pytest.mark.asyncio
async def test_start_session_creates_missing_source_and_target_folders(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    source = tmp_path / "not-yet-created-source"
    target = tmp_path / "not-yet-created-target"
    assert not source.exists()
    assert not target.exists()

    ws = _workspace(source_folders=[str(source)], target_folder=str(target), safety="relaxed")
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert source.is_dir()
        assert target.is_dir()
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


def test_ensure_workspace_folders_warns_instead_of_raising_on_failure(tmp_path: Path, capsys):
    # A folder whose *parent path component* is an existing plain file can
    # never be created — mkdir(parents=True) raises NotADirectoryError (an
    # OSError subclass). This must warn, not propagate and abort session
    # startup.
    blocked_file = tmp_path / "blocked_file"
    blocked_file.write_text("not a directory", encoding="utf-8")
    unreachable = blocked_file / "sub"
    ws = _workspace(source_folders=[str(unreachable)], target_folder=None)

    _ensure_workspace_folders(ws)  # must not raise

    captured = capsys.readouterr()
    assert "warning: could not create folder" in captured.out
    assert str(unreachable) in captured.out


def test_ensure_workspace_folders_ignores_unset_target(tmp_path: Path):
    ws = _workspace(source_folders=[], target_folder=None)
    _ensure_workspace_folders(ws)  # no folders configured -> nothing to do, no error


@pytest.mark.asyncio
async def test_start_session_leaves_already_existing_folders_alone(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.cli.chat.McpManager", _FakeMcpManager)

    target = tmp_path / "already-there"
    target.mkdir()
    marker = target / "keep-me.txt"
    marker.write_text("hello", encoding="utf-8")

    ws = _workspace(target_folder=str(target), safety="relaxed")
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert marker.read_text(encoding="utf-8") == "hello"  # untouched
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()
