"""Phase 4's stated acceptance criteria, automated end to end wherever a
real subprocess/filesystem test can stand in for a manual one:

    "Kill `aida chat` mid-answer; `aida conversations resume` continues it
    sensibly" / "A conversation with a pyirena-mcp plot, resumed next day,
    still shows/references the image artifact correctly" / "~/Documents/Aida/
    contains a readable MD transcript with a working image link"

Everything here is real except the LLM (a scripted MockProvider, same as
test_keystone_image_roundtrip.py) and the "kill": rather than SIGKILLing an
actual `aida chat` subprocess (which would only prove the OS can kill a
process), this drives the real ChatSession.send()/AgentLoop/McpManager code
path through a real MCP tool call that produces a real image artifact, then
simulates the crash by abandoning the session mid-turn — walking the same
async generator start_session's real caller (`_repl_loop`) would, but
stopping partway through and never calling `aclose()` — and checks that
everything already yielded before the abandonment is durably in the DB and
Markdown file on disk. That's exactly the "crash-safe enough" promise
documented in aida.persistence.recorder's module docstring: only the
in-flight text of the *interrupted* turn is lost, nothing before it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aida.cli.chat import start_session
from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.providers.mock import MockProvider, MockToolCall, MockTurn

MOCK_SERVER_PATH = Path(__file__).parent / "mock_mcp_server.py"


def _settings_with_mock_mcp() -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.mcp = McpConfig(
        servers={
            "mock-mcp": McpServerConfig(
                name="mock-mcp", command=sys.executable, args=[str(MOCK_SERVER_PATH)], groups=["analysis"]
            )
        }
    )
    return settings


@pytest.mark.asyncio
async def test_kill_mid_turn_then_resume_continues_sensibly_with_image_artifact(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_mock_mcp()

    # Turn 1 (fully persisted before the "kill"): asks for a plot, gets a
    # real PNG back via the real mock-mcp subprocess, then the model starts
    # a second turn of prose that gets cut off partway through — literally
    # "kill mid-answer", per the acceptance criterion's own wording. Turn 2
    # is deliberately long so it streams as several TextDelta chunks
    # (MockProvider chunks at 12 chars) — long enough to interrupt midway.
    IN_FLIGHT_TEXT = "here is the plot you asked for and a full write-up that never finishes"
    provider_before_kill = MockProvider(
        [
            MockTurn(text="let me get that plot", tool_calls=[MockToolCall(name="mock-mcp__get_image", id="call_1")]),
            MockTurn(text=IN_FLIGHT_TEXT),
        ]
    )
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: provider_before_kill)

    session, mcp_manager = await start_session(settings, profile_name="mock-profile", mcp_names=["mock-mcp"])
    conv_id = session.recorder.conversation_id
    try:
        events_seen = []
        saw_tool_call_finished = False
        post_tool_text_deltas = 0
        async for event in session.send("plot dataset X"):
            events_seen.append(event)
            name = type(event).__name__
            if name == "ToolCallFinished":
                saw_tool_call_finished = True
            elif name == "TextDelta" and saw_tool_call_finished:
                post_tool_text_deltas += 1
                # Stop a couple of chunks into turn 2's streaming text —
                # ChatSession.send()'s incremental-persistence code for a
                # given event only runs once the *next* event is requested
                # (that's how a lazy async generator works), so consuming a
                # few more events past the tool call/artifact guarantees
                # everything from turn 1 (including the artifact) has
                # actually been flushed to the DB before we abandon the
                # generator — while turn 2's assistant message is never even
                # appended to session.messages (AgentLoop only appends it
                # once the full text is finished), let alone persisted.
                if post_tool_text_deltas >= 2:
                    break
    finally:
        # A real kill -9 wouldn't call aclose() either — abandon the
        # provider connection uncleanly, but do release the sqlite handle
        # so the resumed session below can open its own connection to the
        # same file without a lock conflict.
        session.recorder.store.close()
        if mcp_manager is not None:
            await mcp_manager.aclose()

    # --- resume: a brand new process would do exactly this ---------------
    provider_after_resume = MockProvider([MockTurn(text="continuing after resume")])
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: provider_after_resume)

    resumed_session, resumed_mcp = await start_session(settings, resume_conversation_id=conv_id)
    try:
        # 1. History up to the kill point is intact: the user turn and the
        #    assistant's tool-calling message (with its tool_calls) and the
        #    tool result message are all there. Turn 2's in-flight text
        #    (IN_FLIGHT_TEXT) never finished streaming, so AgentLoop never
        #    appended an assistant message for it at all — correctly absent
        #    from history, exactly the "only the interrupted turn's
        #    in-flight text is lost" promise.
        contents = [m.content for m in resumed_session.messages]
        assert "plot dataset X" in contents
        assert not any(IN_FLIGHT_TEXT in (c or "") for c in contents)
        assert not any("here is the plot you asked for" in (c or "") for c in contents)
        tool_msg = next(m for m in resumed_session.messages if m.role == "tool" and m.tool_call_id == "call_1")
        assert "image/png" in tool_msg.content

        # 2. The image artifact itself still resolves to real bytes on disk
        #    ("still shows/references the image artifact correctly").
        artifacts = resumed_session.recorder.store.load_artifacts(conv_id)
        assert len(artifacts) == 1
        assert artifacts[0].kind == "ImageArtifact"
        assert artifacts[0].path is not None
        assert Path(artifacts[0].path).exists()

        # 3. The resumed session "continues it sensibly": a new message
        #    round-trips normally, on a completely fresh provider/loop.
        new_events = [e async for e in resumed_session.send("thanks, what's next?")]
        assert any(type(e).__name__ == "TextFinished" for e in new_events)
        assert resumed_session.messages[-1].content == "continuing after resume"

        # 4. "~/Documents/Aida/ contains a readable MD transcript with a
        #    working image link": the transcript file exists, mentions the
        #    conversation, and its image link is a real relative path that
        #    resolves to the artifact file actually on disk next to it.
        transcript_path = resumed_session.recorder.export_transcript()
        assert transcript_path.exists()
        text = transcript_path.read_text(encoding="utf-8")
        assert "plot dataset X" in text
        assert "continuing after resume" in text

        import re

        link_match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", text)
        assert link_match is not None, f"no markdown image link found in transcript:\n{text}"
        relative_link = link_match.group(1)
        resolved_image_path = (transcript_path.parent / relative_link).resolve()
        assert resolved_image_path.exists()
        assert resolved_image_path.read_bytes() == Path(artifacts[0].path).read_bytes()
    finally:
        await resumed_session.aclose()
        if resumed_mcp is not None:
            await resumed_mcp.aclose()


@pytest.mark.asyncio
async def test_two_workspaces_load_different_provider_mcp_skills_environments(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """Phase 4 acceptance criterion, driven through the real start_session
    path (not just aida.workspace.workspaces in isolation)."""
    skills_dir = aida_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "saxs-basics.md").write_text("SAXS is small-angle X-ray scattering.", encoding="utf-8")
    from aida.config import paths as paths_module

    monkeypatch.setattr(paths_module, "skills_dir", lambda: skills_dir)
    monkeypatch.setattr("aida.cli.chat.skills_dir", lambda: skills_dir)
    monkeypatch.setattr("aida.workspace.workspaces.skills_dir", lambda: skills_dir)

    settings = _settings_with_mock_mcp()
    settings.providers.profiles["plain-profile"] = ProviderProfile(
        name="plain-profile", kind="openai_compat", model="plain-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="mock-profile",
                mcp_group="analysis",
                skills=["saxs-basics"],
                system_prompt="You are a SAXS analysis assistant.",
            ),
            "plain-chat": WorkspaceConfig(name="plain-chat", profile="plain-profile", mcp_group="none", skills=[]),
        }
    )

    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))

    session_a, mcp_a = await start_session(settings, workspace_name="use-pyirena")
    session_b, mcp_b = await start_session(settings, workspace_name="plain-chat")
    try:
        assert session_a.profile_name == "mock-profile"
        assert session_b.profile_name == "plain-profile"

        assert mcp_a is not None and "mock-mcp__get_image" in session_a.tools
        assert mcp_b is None
        assert "mock-mcp__get_image" not in session_b.tools

        assert "small-angle X-ray scattering" in session_a.messages[0].content
        assert session_b.messages == [] or session_b.messages[0].role != "system" or (
            "small-angle X-ray scattering" not in session_b.messages[0].content
        )
    finally:
        await session_a.aclose()
        await session_b.aclose()
        if mcp_a is not None:
            await mcp_a.aclose()
        if mcp_b is not None:
            await mcp_b.aclose()
