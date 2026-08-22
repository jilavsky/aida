"""Phase 6's stated acceptance scenario, automated end to end: "read these
two files, write a summary Markdown report with one image" driven through
the real ``aida.cli.chat.start_session`` / ``ChatSession.send()`` path (the
same one ``aida chat`` and the GUI's ``ChatBridge`` use), not each of
``aida.workspace.files`` / ``aida.documents.readers`` / ``aida.documents.
writers`` / ``aida.documents.tools`` tested in isolation.

Only the LLM itself is scripted (a real ``MockProvider``, same pattern as
``test_phase4_acceptance.py``); everything else — the real
``SafetyGuard``-gated file tools, real file reads/writes, real Markdown
writer with a real sidecar image copy — runs for real against a real
``tmp_path`` workspace.

The one deliberate simplification: rather than spinning up a real MCP
subprocess to *produce* the embedded image (already covered end-to-end by
``test_phase4_acceptance.py``'s own image round-trip), the image artifact is
seeded directly into the session's shared ``ArtifactStore`` before the turn
starts — standing in for whatever earlier tool call would have produced it.
This test's own focus is the read -> write-document pipeline, not
MCP-produces-an-image (already proven elsewhere).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import ImageArtifact
from aida.cli.chat import start_session
from aida.config.settings import ProviderProfile, WorkspaceConfig, WorkspacesConfig, load_settings
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from tests.mock_mcp_server import TINY_PNG_BYTES


def _settings_with_workspace(source_dir: Path, target_dir: Path, *, safety: str = "relaxed"):
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-ws": WorkspaceConfig(
                name="use-ws",
                profile="mock-profile",
                mcp_group="none",
                source_folders=[str(source_dir)],
                target_folder=str(target_dir),
                sidecar_folder_name="figures",
                safety=safety,
            )
        }
    )
    return settings


@pytest.mark.asyncio
async def test_read_two_files_and_write_markdown_report_with_image(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "out"
    source_dir.mkdir()
    target_dir.mkdir()

    file_a = source_dir / "sample_a.txt"
    file_b = source_dir / "sample_b.txt"
    file_a.write_text("Sample A: q range 0.01-0.5, intensity peak at q=0.05", encoding="utf-8")
    file_b.write_text("Sample B: q range 0.02-0.4, intensity peak at q=0.08", encoding="utf-8")

    settings = _settings_with_workspace(source_dir, target_dir)
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(
                    text="let me read both files",
                    tool_calls=[
                        MockToolCall(name="read_file", id="call_a", arguments={"path": str(file_a)}),
                        MockToolCall(name="read_file", id="call_b", arguments={"path": str(file_b)}),
                    ],
                ),
                MockTurn(
                    text="now writing the report",
                    tool_calls=[
                        MockToolCall(
                            name="write_markdown_report",
                            id="call_write",
                            arguments={
                                "path": str(target_dir / "summary.md"),
                                "title": "Sample Comparison",
                                "body": "Sample A peaks at q=0.05; Sample B peaks at q=0.08.",
                                "image_artifact_ids": ["seeded-image"],
                            },
                        )
                    ],
                ),
                MockTurn(text="Done — wrote summary.md with the comparison plot."),
            ]
        ),
    )

    session, mcp_manager = await start_session(settings, workspace_name="use-ws")
    try:
        # Stand-in for an earlier MCP tool call's plot (see module docstring).
        session.recorder.artifact_store.save_image(
            ImageArtifact(data=TINY_PNG_BYTES, id="seeded-image", mime_type="image/png", filename="plot.png")
        )

        events = [
            e
            async for e in session.send(
                "Read sample_a.txt and sample_b.txt and write a comparison report with a plot."
            )
        ]

        tool_results = [e for e in events if type(e).__name__ == "ToolCallFinished"]
        assert len(tool_results) == 3  # 2 reads + 1 write
        assert not any(e.is_error for e in tool_results), [
            (e.tool_name, e.result) for e in tool_results if e.is_error
        ]

        # The write_markdown_report tool call actually produced the file.
        report_path = target_dir / "summary.md"
        assert report_path.exists()
        text = report_path.read_text(encoding="utf-8")
        assert "# Sample Comparison" in text
        assert "q=0.05" in text
        assert "q=0.08" in text
        assert "figures/" in text  # embedded image link, relative to the report

        sidecar_dir = target_dir / "figures"
        assert sidecar_dir.is_dir()
        image_files = list(sidecar_dir.iterdir())
        assert len(image_files) == 1
        assert image_files[0].read_bytes() == TINY_PNG_BYTES

        # The frontend contract: a FileArtifactCreated event for the report
        # (PLAN.md hard rule 3 — typed results, not a guessed-at string).
        file_events = [e for e in events if type(e).__name__ == "FileArtifactCreated"]
        assert any(Path(e.path) == report_path for e in file_events)

        # The read_file calls actually saw each file's real content (not
        # just "succeeded") — round-tripped through describe_for_model.
        read_results = {e.call_id: e.result for e in tool_results if e.tool_name == "read_file"}
        assert "q=0.05" in read_results["call_a"]
        assert "q=0.08" in read_results["call_b"]

        # The turn completed normally with a real final answer.
        assert session.messages[-1].role == "assistant"
        assert "Done" in session.messages[-1].content
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()


@pytest.mark.asyncio
async def test_write_outside_target_folder_is_denied_without_confirmation(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """The safety-model half of Phase 6's acceptance scenario: a write
    outside the workspace's allowed folders is denied when nothing approves
    it — the default CLI confirm callback would block on stdin, so this
    passes an explicit deny-everything callback instead (mirroring
    aida.workspace.safety.deny_all), and checks the file is never created."""
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "out"
    outside_dir = tmp_path / "outside"
    source_dir.mkdir()
    target_dir.mkdir()
    outside_dir.mkdir()

    settings = _settings_with_workspace(source_dir, target_dir, safety="confirm")
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(
                    tool_calls=[
                        MockToolCall(
                            name="write_markdown_report",
                            id="call_write",
                            arguments={"path": str(outside_dir / "leak.md"), "title": "Should Not Exist"},
                        )
                    ]
                ),
                MockTurn(text="could not write the file"),
            ]
        ),
    )

    async def _deny(_request) -> bool:
        return False

    session, mcp_manager = await start_session(settings, workspace_name="use-ws", confirm_callback=_deny)
    try:
        events = [e async for e in session.send("write a report to the outside folder")]
        tool_result = next(e for e in events if type(e).__name__ == "ToolCallFinished")
        assert tool_result.is_error
        assert not (outside_dir / "leak.md").exists()
    finally:
        await session.aclose()
        if mcp_manager is not None:
            await mcp_manager.aclose()
