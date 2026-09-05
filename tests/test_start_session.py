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

import sqlite3
from pathlib import Path

import pytest

from aida.cli.chat import (
    ChatSession,
    UnknownProfileError,
    UnknownWorkspaceError,
    _build_parser,
    start_session,
)
from aida.config.settings import (
    EmbeddingProfile,
    KnowledgeBaseConfig,
    KnowledgeConfig,
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.core.session import _ensure_workspace_folders
from aida.persistence.recorder import ConversationNotFoundError
from aida.providers.mock import MockProvider, MockTurn
from aida.providers.mock_embeddings import MockEmbeddings
from aida.providers.profiles import UnknownProviderKindError


class _FakeMcpManager:
    """Stands in for aida.mcp.manager.McpManager so these tests don't spawn
    real subprocesses — McpManager's own behavior is covered by
    test_mcp_manager.py. Records what it was constructed with so tests can
    assert on precedence."""

    instances: list[_FakeMcpManager] = []
    #: Settable per-test (reset by the autouse fixture below) so a test can
    #: prove start_session actually folds McpManager.server_instructions()
    #: into the session's system message.
    instructions_to_report: dict[str, str] = {}

    def __init__(self, servers, *, artifact_store=None, confirm_callback=None, scratch_dir=None) -> None:
        self.servers = servers
        self.artifact_store = artifact_store
        self.confirm_callback = confirm_callback
        self.scratch_dir = scratch_dir
        self.running_server_names: list[str] = [s.name for s in servers]
        self.start_errors: dict[str, str] = {}
        self.closed = False
        _FakeMcpManager.instances.append(self)

    async def start_all(self) -> dict:
        return {}

    def skills(self) -> list[str]:
        return []

    def server_instructions(self) -> dict[str, str]:
        return _FakeMcpManager.instructions_to_report

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_mcp_instances():
    _FakeMcpManager.instances.clear()
    _FakeMcpManager.instructions_to_report = {}
    yield
    _FakeMcpManager.instances.clear()
    _FakeMcpManager.instructions_to_report = {}


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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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
async def test_start_session_tells_the_model_its_source_and_target_folders(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """Regression: "Agent seems to have no understanding of Source and
    Target folders" — the system message must actually name them."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    source = tmp_path / "usaxs_data"
    target = tmp_path / "out"
    ws = _workspace(source_folders=[str(source)], target_folder=str(target), safety="relaxed")
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        content = session.messages[0].content
        assert str(source) in content
        assert str(target) in content
        assert "relaxed" in content.lower()
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_tells_the_model_its_python_interpreter(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Bug report: the model resorted to a raw `python3 -c "..."` shell
    probe (via run_command, needing confirmation) just to discover which
    interpreter/packages it had — the system message must name the
    configured interpreter directly instead."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    ws = _workspace(python_interpreter="/opt/miniconda3/envs/aievaluator/bin/python", command_allowlist=["git status"])
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        content = session.messages[0].content
        assert "/opt/miniconda3/envs/aievaluator/bin/python" in content
        assert "git status" in content
        assert "run_python_script" in content
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_tells_the_model_its_name_and_the_user_before_the_workspace_prompt(
    monkeypatch, aida_home: Path, records_home: Path
):
    """B15 — user request: "should we inject name Aida ... so agent can be
    easier addressed... should we somehow also inject user name and some
    small user info?" Global identity/user framing must reach the model,
    and land ahead of the workspace's own system_prompt ("You are a
    workspace assistant.") — a domain persona shouldn't open the
    conversation before the model knows its own name."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))
    settings.app.user_context = "Jan, beamline scientist at APS."

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        content = session.messages[0].content
        assert "Aida" in content
        assert "Jan, beamline scientist at APS." in content
        assert content.index("Aida") < content.index("workspace assistant")
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_no_coding_context_when_scripting_disabled(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    ws = _workspace(scripting_enabled=False)
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        content = session.messages[0].content
        assert "# Python execution" not in content
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_folds_mcp_server_instructions_into_system_message(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Regression: session.initialize()'s ``instructions`` field (a FastMCP
    server author's own LLM-facing usage guidance — pyirena-mcp ships a
    detailed one) used to be discarded entirely; it must now reach the
    model via McpManager.server_instructions()."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)
    _FakeMcpManager.instructions_to_report = {"ws-server": "Call pyirena_summarize_folder first."}

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))
    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert "Call pyirena_summarize_folder first." in session.messages[0].content
        assert "ws-server" in session.messages[0].content
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_passes_confirm_callback_to_mcp_manager(monkeypatch, aida_home: Path, records_home: Path):
    """Regression: McpManager's own per-tool "confirm before run" gate
    (Phase 7) is only reachable in real usage if start_session actually
    hands it the session's confirm_callback — the same one SafetyGuard
    gets. Before this was wired up, McpManager fell back to its own
    deny_all default, silently refusing every confirm-flagged MCP tool no
    matter what the user answered."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    async def my_confirm(_request):
        return True

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))
    session, mcp_manager = await start_session(settings, workspace_name="use-ws", confirm_callback=my_confirm)
    try:
        assert mcp_manager.confirm_callback is my_confirm
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_explicit_profile_overrides_workspace_profile(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="reply 1")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))

    first_session, first_mcp = await start_session(settings, workspace_name="use-ws")
    conv_id = first_session.recorder.conversation_id
    _ = [e async for e in first_session.send("remember this")]
    await first_session.aclose()
    if first_mcp is not None:
        await first_mcp.aclose()

    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="reply 2")]))
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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
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


@pytest.mark.asyncio
async def test_start_session_always_allows_writes_under_aida_artifacts_dir(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Bug report: writing under ~/.aida/artifacts (AIDA's own generated-
    output folder, distinct from the rest of ~/.aida which holds config/
    secrets refs/the DB) triggered a confirmation prompt like any other
    outside-allowed-folders path, even though nothing user-configured
    should need to name it explicitly. It's always allowed now, with no
    workspace and no configured allowed_folders at all — but the mode still
    has to be relaxed for that to mean "no prompt" (same as any other
    allowed-folders case; see test_start_session_global_allowed_folders_apply_without_workspace)."""
    from aida.config.paths import artifacts_dir

    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()
    settings.app.default_safety_mode = "relaxed"

    confirm_calls = []

    async def _never_called(request):
        confirm_calls.append(request)
        return False

    session, _mcp_manager = await start_session(
        settings, profile_name="mock-profile", confirm_callback=_never_called
    )
    try:
        target = artifacts_dir() / "note.txt"
        result = await session.tools["write_file"].func({"path": str(target), "content": "hello"})
        assert not result.is_error
        assert target.read_text(encoding="utf-8") == "hello"
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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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


# --- Phase 8: resolving a workspace's knowledge_bases into ActiveKnowledgeBases


@pytest.mark.asyncio
async def test_start_session_resolves_workspace_knowledge_bases(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)
    monkeypatch.setattr("aida.core.session.build_embeddings_provider", lambda profile: MockEmbeddings())

    settings = _settings(
        workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace(knowledge_bases=["usaxs-docs"])}),
        knowledge=KnowledgeConfig(
            knowledge_bases={
                "usaxs-docs": KnowledgeBaseConfig(
                    name="usaxs-docs", source_folders=["/data/docs"], embedding_profile="embed-profile"
                )
            }
        ),
    )
    settings.providers.embedding_profiles["embed-profile"] = EmbeddingProfile(
        name="embed-profile", kind="openai_compat", model="embed-model"
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert [kb.name for kb in session.active_knowledge_bases] == ["usaxs-docs"]
        assert session.active_knowledge_bases[0].embedding_profile_name == "embed-profile"
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_warns_and_skips_unknown_knowledge_base_name(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    settings = _settings(
        workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace(knowledge_bases=["does-not-exist"])}),
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert session.active_knowledge_bases == []
        assert "unknown knowledge base 'does-not-exist'" in capsys.readouterr().out
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_warns_and_skips_knowledge_base_without_embedding_profile(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    settings = _settings(
        workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace(knowledge_bases=["usaxs-docs"])}),
        knowledge=KnowledgeConfig(
            knowledge_bases={"usaxs-docs": KnowledgeBaseConfig(name="usaxs-docs", source_folders=["/data/docs"])}
        ),
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert session.active_knowledge_bases == []
        assert "no embedding_profile configured" in capsys.readouterr().out
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_warns_and_skips_knowledge_base_with_unknown_embedding_profile(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    settings = _settings(
        workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace(knowledge_bases=["usaxs-docs"])}),
        knowledge=KnowledgeConfig(
            knowledge_bases={
                "usaxs-docs": KnowledgeBaseConfig(
                    name="usaxs-docs", source_folders=["/data/docs"], embedding_profile="does-not-exist"
                )
            }
        ),
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert session.active_knowledge_bases == []
        assert "unknown embedding profile 'does-not-exist'" in capsys.readouterr().out
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_warns_and_skips_knowledge_base_with_unbuildable_embedding_profile(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    settings = _settings(
        workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace(knowledge_bases=["usaxs-docs"])}),
        knowledge=KnowledgeConfig(
            knowledge_bases={
                "usaxs-docs": KnowledgeBaseConfig(
                    name="usaxs-docs", source_folders=["/data/docs"], embedding_profile="embed-profile"
                )
            }
        ),
    )
    settings.providers.embedding_profiles["embed-profile"] = EmbeddingProfile(
        name="embed-profile", kind="totally-unknown"
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        assert session.active_knowledge_bases == []
        assert "totally-unknown" in capsys.readouterr().out
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_start_session_no_workspace_knowledge_bases_configured_resolves_nothing(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    session, _mcp_manager = await start_session(_settings(), profile_name="mock-profile")
    try:
        assert session.active_knowledge_bases == []
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_aclose_closes_active_knowledge_base_resources(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    closed = []

    class _TrackingEmbeddings(MockEmbeddings):
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr("aida.core.session.build_embeddings_provider", lambda profile: _TrackingEmbeddings())

    settings = _settings(
        workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace(knowledge_bases=["usaxs-docs"])}),
        knowledge=KnowledgeConfig(
            knowledge_bases={
                "usaxs-docs": KnowledgeBaseConfig(
                    name="usaxs-docs", source_folders=["/data/docs"], embedding_profile="embed-profile"
                )
            }
        ),
    )
    settings.providers.embedding_profiles["embed-profile"] = EmbeddingProfile(
        name="embed-profile", kind="openai_compat", model="embed-model"
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    conn = session.active_knowledge_bases[0].connection
    await session.aclose()
    if mcp_manager is not None:
        await mcp_manager.aclose()

    assert closed == [True]
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_ensure_workspace_folders_ignores_unset_target(tmp_path: Path):
    ws = _workspace(source_folders=[], target_folder=None)
    _ensure_workspace_folders(ws)  # no folders configured -> nothing to do, no error


@pytest.mark.asyncio
async def test_start_session_leaves_already_existing_folders_alone(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

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


def test_ensure_workspace_folders_does_not_fabricate_an_unmounted_source_tree(tmp_path: Path, capsys):
    """A source folder whose *parent* is also missing almost always means a
    network share isn't mounted. Creating it with parents=True would leave
    empty local directories shadowing the real mount point, so the agent
    would report an empty data folder instead of "not mounted"."""
    unmounted = tmp_path / "Volumes" / "usaxs_data" / "RUN_2026_08"
    ws = _workspace(source_folders=[str(unmounted)], target_folder=None)

    _ensure_workspace_folders(ws)

    assert not unmounted.exists()
    assert not unmounted.parent.exists()
    assert "not creating it" in capsys.readouterr().out


def test_ensure_workspace_folders_still_creates_a_source_folder_beside_existing_ones(tmp_path: Path):
    """The original request ("can we create the folders if they do not
    exist?") still holds where the parent is really there — that's a folder
    the user just hasn't populated yet, not an absent mount."""
    ws = _workspace(source_folders=[str(tmp_path / "incoming")], target_folder=None)

    _ensure_workspace_folders(ws)

    assert (tmp_path / "incoming").is_dir()


def test_ensure_workspace_folders_creates_a_target_folder_with_parents(tmp_path: Path):
    """A target folder is an output location the user named, not a mount —
    creating the whole path is the intended convenience."""
    target = tmp_path / "reports" / "2026" / "august"
    ws = _workspace(source_folders=[], target_folder=str(target))

    _ensure_workspace_folders(ws)

    assert target.is_dir()


@pytest.mark.asyncio
async def test_start_session_shuts_down_mcp_servers_when_the_session_fails_to_build(
    monkeypatch, aida_home: Path, records_home: Path
):
    """The cleanup block used to catch only UnknownProfileError, but
    ChatSession's constructor can fail several other ways — a typo'd
    ``kind:`` in providers.yaml (UnknownProviderKindError from
    build_provider), an unreadable skills file. Any of those escaping left
    every MCP server this call had just launched running as an orphaned
    subprocess for the life of the process."""

    def _boom(profile):
        raise UnknownProviderKindError("profile 'ws-profile' has unknown kind 'openai'")

    monkeypatch.setattr("aida.core.session.build_provider", _boom)
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": _workspace()}))

    with pytest.raises(UnknownProviderKindError):
        await start_session(settings, workspace_name="use-ws")

    assert _FakeMcpManager.instances, "the fake manager should have been constructed"
    assert all(instance.closed for instance in _FakeMcpManager.instances)


# --- the user organization axis (planning/multiuser_plan.md) -------------


@pytest.mark.asyncio
async def test_start_session_stamps_the_active_user_on_a_new_conversation(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()

    session, _ = await start_session(settings, profile_name="mock-profile", user="jan")
    try:
        assert session.recorder.user == "jan"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_falls_back_to_the_configured_active_user(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()
    settings.app.active_user = "from-config"

    session, _ = await start_session(settings, profile_name="mock-profile")
    try:
        assert session.recorder.user == "from-config"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_records_no_user_by_default(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Every install that has not opted in keeps today's behaviour."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        assert session.recorder.user is None
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_resuming_never_relabels_someone_elses_conversation(
    monkeypatch, aida_home: Path, records_home: Path
):
    """A conversation belongs to whoever created it, even if a different
    name is active at the machine now. Re-stamping on resume would quietly
    move another person's work into the current user's bucket."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()

    first, _ = await start_session(settings, profile_name="mock-profile", user="eva")
    conversation_id = first.recorder.conversation_id
    _ = [e async for e in first.send("hello")]
    await first.aclose()

    resumed, _ = await start_session(
        settings, profile_name="mock-profile", resume_conversation_id=conversation_id, user="jan"
    )
    try:
        assert resumed.recorder.user == "eva"
    finally:
        await resumed.aclose()


@pytest.mark.asyncio
async def test_start_session_creates_the_expanded_workspace_folder_not_a_literal_one(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """End-to-end proof that `{user}` is expanded before *anything* reads
    the workspace's folders: `_ensure_workspace_folders` creates the real
    directory, and no folder literally named `{user}` is left behind."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    monkeypatch.setattr("aida.core.session.McpManager", _FakeMcpManager)

    ws = _workspace(target_folder=str(tmp_path / "out" / "{user}"), safety="relaxed")
    settings = _settings(workspaces=WorkspacesConfig(workspaces={"use-ws": ws}))

    session, _ = await start_session(settings, workspace_name="use-ws", user="Jan Ilavsky")
    try:
        assert (tmp_path / "out" / "jan-ilavsky").is_dir()
        assert not (tmp_path / "out" / "{user}").exists()
        # ...and the model is told the real path, not the template.
        content = session.messages[0].content
        assert str(tmp_path / "out" / "jan-ilavsky") in content
        assert "{user}" not in content
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_start_session_expands_the_user_in_the_records_dir(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """The records dir is where transcripts live — and, once the attachment
    store lands, copies of attached documents. It is the path most worth
    splitting per user on a shared machine."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    settings = _settings()
    settings.app.records_dir = str(tmp_path / "records" / "{user}")

    session, _ = await start_session(settings, profile_name="mock-profile", user="jan")
    try:
        assert (tmp_path / "records" / "jan").is_dir()
        assert session.recorder.records_dir == tmp_path / "records" / "jan"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_session_send_keeps_attached_documents(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """End to end: a document attached to a turn is copied into the
    conversation's own folder and outlives the original."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))
    source = tmp_path / "Downloads" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4 pretend")

    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        _ = [e async for e in session.send("what does this say?", attachment_paths=[str(source)])]
        kept = Path(session.recorder.attachments_dir()) / "paper.pdf"
        assert kept.exists()
        source.unlink()
        assert kept.read_bytes() == b"%PDF-1.4 pretend"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_session_send_without_attachments_creates_no_folder(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))
    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        _ = [e async for e in session.send("just talking")]
        assert (session.recorder.records_dir / "attachments").exists() is False
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_failed_attachment_copy_never_fails_the_turn(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """The content is already in the message; losing the turn over a
    bookkeeping copy would be far worse than not keeping the file."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))
    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        events = [
            e async for e in session.send("here", attachment_paths=[str(tmp_path / "never-existed.pdf")])
        ]
        assert events, "the turn must still have run"
        assert session.messages[-1].role == "assistant"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_figure_tools_are_registered_and_see_this_conversations_folder(
    monkeypatch, aida_home: Path, records_home: Path
):
    """They are built after the recorder, since the attachments folder
    depends on the conversation id — a regression here would silently drop
    both tools from every session."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        assert "list_document_figures" in session.tools
        assert "get_document_figure" in session.tools
        result = await session.tools["list_document_figures"].func({"document": "nothing.pdf"})
        assert result.is_error
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_looking_up_figures_never_creates_an_attachments_row(
    monkeypatch, aida_home: Path, records_home: Path
):
    """The tools ask for the attachments folder on every call; that lookup
    must stay a pure read, or an attachment-free conversation would end up
    recorded as having had one."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        await session.tools["list_document_figures"].func({"document": "nothing.pdf"})
        store = session.recorder.store
        assert store.get_conversation(session.recorder.conversation_id).attachments_path is None
        assert not (session.recorder.records_dir / "attachments").exists()
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_attachment_text_reaches_the_conversation_folder(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """End to end for the plumbing that was missing: the extracted text has
    to travel with the path, or the attachments folder holds a PDF and no
    record of what the model was actually given."""
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))
    source = tmp_path / "Downloads" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4 pretend")

    session, _ = await start_session(_settings(), profile_name="mock-profile")
    try:
        _ = [
            e
            async for e in session.send(
                "summarise this",
                attachment_paths=[str(source)],
                attachment_texts={str(source): "The full extracted text."},
            )
        ]
        folder = Path(session.recorder.attachments_dir())
        assert (folder / "paper.pdf").exists()
        assert (folder / "paper.pdf.md").read_text() == "The full extracted text."
    finally:
        await session.aclose()
