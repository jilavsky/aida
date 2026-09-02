"""Tests for ``aida run`` (aida.cli.run) — the headless single-turn CLI,
Phase 10 layer 1.
"""

from __future__ import annotations

import json
from pathlib import Path

from aida.cli.run import EXIT_CONFIG_ERROR, EXIT_OK, EXIT_STEP_FAILED, main
from aida.config.settings import (
    ProviderProfile,
    Settings,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
)
from aida.providers.mock import MockProvider, MockToolCall, MockTurn


def _settings() -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", safety="relaxed")
        }
    )
    return settings


def test_run_prints_reply_and_exits_zero(monkeypatch, aida_home: Path, records_home: Path, capsys):
    monkeypatch.setattr("aida.cli.run.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hello there")]))

    code = main(["--workspace", "use-ws", "say hi"])

    assert code == EXIT_OK
    assert "hello there" in capsys.readouterr().out


def test_run_json_output_shape(monkeypatch, aida_home: Path, records_home: Path, capsys):
    monkeypatch.setattr("aida.cli.run.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hello there")]))

    code = main(["--workspace", "use-ws", "--json", "say hi"])

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["reply"] == "hello there"
    assert payload["stop_reason"] == "stop"
    assert payload["conversation_id"]


def test_run_reads_prompt_from_stdin_when_omitted(monkeypatch, aida_home: Path, records_home: Path, capsys):
    monkeypatch.setattr("aida.cli.run.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="from stdin")]))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("what time is it?"))

    code = main(["--workspace", "use-ws"])

    assert code == EXIT_OK
    assert "from stdin" in capsys.readouterr().out


def test_run_no_prompt_at_all_is_a_config_error(monkeypatch, aida_home: Path, records_home: Path, capsys):
    monkeypatch.setattr("aida.cli.run.load_settings", _settings)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    code = main(["--workspace", "use-ws"])

    assert code == EXIT_CONFIG_ERROR


def test_run_unknown_workspace_is_a_config_error(monkeypatch, aida_home: Path, records_home: Path, capsys):
    monkeypatch.setattr("aida.cli.run.load_settings", _settings)

    code = main(["--workspace", "does-not-exist", "hi"])

    assert code == EXIT_CONFIG_ERROR
    assert "does-not-exist" in capsys.readouterr().err


def test_run_agent_error_exits_step_failed(monkeypatch, aida_home: Path, records_home: Path, capsys):
    monkeypatch.setattr("aida.cli.run.load_settings", _settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(error="boom")]))

    code = main(["--workspace", "use-ws", "hi"])

    assert code == EXIT_STEP_FAILED
    assert "boom" in capsys.readouterr().err


def _settings_with_target(tmp_path: Path, *, safety: str) -> Settings:
    settings = _settings()
    target = tmp_path / "target"
    target.mkdir()
    settings.workspaces.workspaces["use-ws"].target_folder = str(target)
    settings.workspaces.workspaces["use-ws"].safety = safety
    return settings


def test_run_without_yes_in_allowed_declines_an_in_bounds_write(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path, capsys
):
    """End-to-end: without ``--yes-in-allowed``, ``write_file`` inside the
    workspace's own target folder is still declined — fail-with-message is
    the default. A declined confirmation never raises past
    ``session.send()`` (``ConfirmationDenied``'s docstring: ``AgentLoop``
    turns it into an ordinary tool error the model can see and react to),
    so this only proves the wiring actually reaches that point — the write
    must not have happened."""
    settings = _settings_with_target(tmp_path, safety="confirm")
    target = Path(settings.workspaces.workspaces["use-ws"].target_folder)
    monkeypatch.setattr("aida.cli.run.load_settings", lambda: settings)
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(
                    tool_calls=[
                        MockToolCall(
                            name="write_file",
                            arguments={"path": str(target / "x.md"), "content": "hi"},
                        )
                    ]
                ),
                MockTurn(text="could not write the file"),
            ]
        ),
    )

    code = main(["--workspace", "use-ws", "write a file"])

    assert code == EXIT_OK  # the model recovered and answered normally
    assert not (target / "x.md").exists()


def test_run_yes_in_allowed_approves_an_in_bounds_write(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    settings = _settings_with_target(tmp_path, safety="confirm")
    target = Path(settings.workspaces.workspaces["use-ws"].target_folder)
    monkeypatch.setattr("aida.cli.run.load_settings", lambda: settings)
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(
                    tool_calls=[
                        MockToolCall(
                            name="write_file",
                            arguments={"path": str(target / "x.md"), "content": "hi"},
                        )
                    ]
                ),
                MockTurn(text="wrote it"),
            ]
        ),
    )

    code = main(["--workspace", "use-ws", "--yes-in-allowed", "write a file"])

    assert code == EXIT_OK
    assert (target / "x.md").read_text(encoding="utf-8") == "hi"


def test_run_preapprove_tool_approves_a_confirm_before_run_mcp_tool(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Not a full MCP integration (real subprocess is out of scope here,
    already covered by test_mcp_manager.py) — just confirms main() actually
    threads --preapprove-tool through to the confirm callback rather than
    dropping it."""
    from aida.core.headless import build_headless_confirm_callback

    settings = _settings()
    monkeypatch.setattr("aida.cli.run.load_settings", lambda: settings)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    captured: dict = {}

    def _capturing_build(*, yes_in_allowed, preapproved_tools=None):
        captured["preapproved_tools"] = preapproved_tools
        return build_headless_confirm_callback(yes_in_allowed=yes_in_allowed, preapproved_tools=preapproved_tools)

    monkeypatch.setattr("aida.cli.run.build_headless_confirm_callback", _capturing_build)

    code = main(["--workspace", "use-ws", "--preapprove-tool", "server__tool", "hi"])

    assert code == EXIT_OK
    assert captured["preapproved_tools"] == {"server__tool"}
