"""Tests for aida.ui.qt.app — the ``aida-gui`` entry point's argument
parsing and last-workspace/profile fallback precedence.

``main()`` itself (real QApplication + app.exec() event loop) isn't driven
here — tests/ui/test_main_window.py already exercises the same
MainWindow(settings, loop_thread, start_kwargs=...) construction path this
module's ``main()`` calls, via the offscreen qapp/loop_thread fixtures.
This file is specifically about ``_resolve_start_kwargs``'s precedence
logic, which is why it was pulled out into its own testable function.
"""

from __future__ import annotations

from aida.config.settings import load_settings
from aida.ui.qt.app import _build_parser, _resolve_start_kwargs


def _parse(*argv):
    return _build_parser().parse_args(list(argv))


def test_no_flags_falls_back_to_last_workspace_and_profile(aida_home, records_home):
    settings = load_settings()
    settings.app.last_workspace_name = "use-pyirena"
    settings.app.last_profile_name = "local-lmstudio"

    kwargs = _resolve_start_kwargs(_parse(), settings)

    assert kwargs["workspace_name"] == "use-pyirena"
    assert kwargs["profile_name"] == "local-lmstudio"


def test_explicit_workspace_flag_overrides_last_workspace(aida_home, records_home):
    settings = load_settings()
    settings.app.last_workspace_name = "old-workspace"

    kwargs = _resolve_start_kwargs(_parse("--workspace", "new-workspace"), settings)

    assert kwargs["workspace_name"] == "new-workspace"


def test_explicit_profile_flag_overrides_last_profile(aida_home, records_home):
    settings = load_settings()
    settings.app.last_profile_name = "old-profile"

    kwargs = _resolve_start_kwargs(_parse("--profile", "new-profile"), settings)

    assert kwargs["profile_name"] == "new-profile"


def test_no_flags_and_nothing_saved_yet_is_none(aida_home, records_home):
    settings = load_settings()  # fresh — last_workspace_name/last_profile_name both None

    kwargs = _resolve_start_kwargs(_parse(), settings)

    assert kwargs["workspace_name"] is None
    assert kwargs["profile_name"] is None


def test_skills_and_mcp_flags_still_parsed_normally(aida_home, records_home):
    settings = load_settings()
    kwargs = _resolve_start_kwargs(
        _parse("--skills", "a, b", "--mcp-group", "analysis", "--mcp", "x,y"), settings
    )
    assert kwargs["skill_names"] == ["a", "b"]
    assert kwargs["mcp_group"] == "analysis"
    assert kwargs["mcp_names"] == ["x", "y"]
