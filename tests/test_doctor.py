from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.doctor import format_report, run_checks
from aida.config.settings import (
    AppConfig,
    ProviderProfile,
    ProvidersConfig,
    save_app_config,
    save_providers_config,
)
from aida.providers.profiles import ProfileValidation


def test_doctor_reports_working_setup(aida_home: Path, records_home: Path):
    results = run_checks()
    names = {r.name for r in results}
    assert "python_version" in names
    assert "config_files" in names
    assert "keyring" in names
    # A fresh, isolated AIDA_HOME with a writable temp dir should pass every
    # writability and config-loading check.
    for r in results:
        if r.name in {"app_dir", "logs_dir", "artifacts_dir", "records_dir", "config_files"}:
            assert r.ok, f"{r.name} unexpectedly failed: {r.detail}"


def test_doctor_flags_broken_config(aida_home: Path, records_home: Path):
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "config.yaml").write_text("not: [valid: yaml: at: all", encoding="utf-8")

    results = run_checks()
    config_result = next(r for r in results if r.name == "config_files")
    assert not config_result.ok


def test_format_report_contains_summary_line(aida_home: Path, records_home: Path):
    results = run_checks()
    text = format_report(results)
    assert "checks passed" in text
    assert "AIDA doctor report" in text


# --- provider checks go through the real provider layer ---------------------


def test_provider_check_uses_validate_profile_not_a_raw_http_head(
    aida_home: Path, records_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression: doctor used to send a bare ``urllib`` HEAD at each
    profile's ``base_url`` and call any non-2xx "unreachable" — which
    reports a perfectly healthy Ollama/LM Studio as broken (they answer
    404/405 to a HEAD on ``/v1``) and says nothing about whether the model
    or the key actually work. ``validate_profile`` has done this properly
    since Phase 2 and was simply never wired in."""
    save_providers_config(
        ProvidersConfig(
            profiles={"ollama": ProviderProfile(name="ollama", base_url="http://localhost:11434/v1", model="qwen")}
        ),
        aida_home,
    )

    called: list[str] = []

    async def fake_validate(profile, **kwargs):
        called.append(profile.name)
        return ProfileValidation(name=profile.name, ok=True, detail="reachable (openai_compat, model=qwen)")

    monkeypatch.setattr("aida.cli.doctor.validate_profile", fake_validate)

    results = run_checks()

    assert called == ["ollama"], "doctor must validate through the provider layer"
    provider_result = next(r for r in results if r.name == "provider:ollama")
    assert provider_result.ok
    assert "model=qwen" in provider_result.detail


def test_provider_check_reports_an_unreachable_profile_without_crashing(
    aida_home: Path, records_home: Path, monkeypatch: pytest.MonkeyPatch
):
    save_providers_config(
        ProvidersConfig(profiles={"argo": ProviderProfile(name="argo", kind="anthropic", model="claude")}), aida_home
    )

    async def fake_validate(profile, **kwargs):
        return ProfileValidation(name=profile.name, ok=False, detail="no response within 10s")

    monkeypatch.setattr("aida.cli.doctor.validate_profile", fake_validate)

    results = run_checks()
    provider_result = next(r for r in results if r.name == "provider:argo")
    assert not provider_result.ok
    assert "no response" in provider_result.detail


def test_records_dir_check_honors_the_configured_override(aida_home: Path, records_home: Path):
    """Regression: doctor checked the *default* records dir, reporting
    "writable" for a directory the user's config never touches while saying
    nothing about the one it does."""
    configured = records_home / "elsewhere" / "AidaRecords"
    save_app_config(AppConfig(records_dir=str(configured)), aida_home)

    results = run_checks()

    records_result = next(r for r in results if r.name == "records_dir")
    assert str(configured) in records_result.detail
    assert configured.exists()


# --- pyirena MCP check ----------------------------------------------------


def _candidate(command: str = "/opt/envs/pyirena/bin/pyirena-mcp"):
    from aida.mcp.pyirena_setup import PyirenaMcpCandidate

    return PyirenaMcpCandidate(command=command, source="conda env 'pyirena'")


def test_pyirena_check_is_never_a_failure_when_not_installed(aida_home, monkeypatch):
    """A user running AIDA for document work must not see a red FAIL for a
    package they deliberately did not install."""
    from aida.cli import doctor

    monkeypatch.setattr("aida.cli.doctor.find_pyirena_mcp", list)
    result = doctor._check_pyirena_mcp(None)

    assert result.ok
    assert 'pip install "pyirena[mcp]"' in result.detail


def test_pyirena_check_names_the_fix_when_installed_but_unconfigured(aida_home, monkeypatch):
    """The failure a new user is least equipped to diagnose: the server is
    perfectly installed while mcp.json has never heard of it, which looks
    identical from the chat window."""
    from aida.cli import doctor
    from aida.config.settings import load_settings

    monkeypatch.setattr("aida.cli.doctor.find_pyirena_mcp", lambda: [_candidate()])
    monkeypatch.setattr("aida.cli.doctor.pyirena_version", lambda _c: "1.1.0")

    result = doctor._check_pyirena_mcp(load_settings())

    assert result.ok
    assert "NOT configured" in result.detail
    assert "aida mcp add-pyirena" in result.detail


def test_pyirena_check_reports_a_configured_server(aida_home, monkeypatch):
    from aida.cli import doctor
    from aida.config.settings import McpServerConfig, load_settings

    monkeypatch.setattr("aida.cli.doctor.find_pyirena_mcp", lambda: [_candidate()])
    monkeypatch.setattr("aida.cli.doctor.pyirena_version", lambda _c: "1.1.0")
    settings = load_settings()
    settings.mcp.servers["pyirena"] = McpServerConfig(
        name="pyirena", command="/opt/envs/pyirena/bin/pyirena-mcp"
    )

    result = doctor._check_pyirena_mcp(settings)

    assert result.ok
    assert "configured as pyirena" in result.detail


def test_pyirena_check_recognizes_the_python_dash_m_launch_form(aida_home, monkeypatch):
    """A server configured as `python -m pyirena.mcp.server` has no
    "pyirena" in its command at all — matching only on the executable name
    would report it as unconfigured and tell the user to add a duplicate."""
    from aida.cli import doctor
    from aida.config.settings import McpServerConfig, load_settings

    monkeypatch.setattr("aida.cli.doctor.find_pyirena_mcp", list)
    settings = load_settings()
    settings.mcp.servers["scattering"] = McpServerConfig(
        name="scattering", command="/env/bin/python", args=["-m", "pyirena.mcp.server"]
    )

    result = doctor._check_pyirena_mcp(settings)

    assert "configured as scattering" in result.detail
