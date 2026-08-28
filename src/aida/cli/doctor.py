"""``aida doctor`` — environment and configuration diagnostics.

Reports, per PLAN.md Phase 1 acceptance criteria:

- Python version
- config file status/validity (config.yaml, providers.yaml, workspaces.yaml,
  mcp.json)
- keyring availability
- reachable provider endpoints — a real per-profile check through the
  provider layer (``aida.providers.profiles.validate_profile``), each under
  its own timeout
- writable dirs (``~/.aida`` and its subdirectories, and the *configured*
  records dir)

Designed to be used both as a CLI command and importable for tests: the
report is built as a list of ``CheckResult`` and formatting is separate from
checking, so tests can assert on structured results without parsing text.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from aida.config import paths
from aida.config.secrets import keyring_available
from aida.config.settings import Settings, load_settings
from aida.mcp.pyirena_setup import find_pyirena_mcp, pyirena_version
from aida.providers.profiles import ProfileValidation, validate_profile


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    return CheckResult(
        "python_version",
        ok,
        f"Python {sys.version.split()[0]}" + ("" if ok else " (need >= 3.11)"),
    )


def _check_writable(name: str, path: Path) -> CheckResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".aida_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult(name, True, f"writable: {path}")
    except OSError as exc:
        return CheckResult(name, False, f"NOT writable: {path} ({exc})")


def _load_settings_safely() -> tuple[Settings | None, CheckResult]:
    """Load settings once, turning a bad config into a failed check instead
    of an exception — every other check that needs settings reuses this
    result rather than calling ``load_settings()`` again unguarded."""
    try:
        settings = load_settings()
        return settings, CheckResult(
            "config_files",
            True,
            "config.yaml, providers.yaml, workspaces.yaml, mcp.json loaded "
            f"(config_version={settings.app.config_version})",
        )
    except Exception as exc:  # noqa: BLE001 - doctor must never crash on bad config
        return None, CheckResult("config_files", False, f"failed to load: {exc}")


def _check_keyring() -> CheckResult:
    ok = keyring_available()
    return CheckResult(
        "keyring",
        ok,
        "keyring backend available" if ok else "no usable keyring backend found",
    )


def _check_provider_endpoints(settings: Settings | None) -> list[CheckResult]:
    """Real reachability checks for configured provider profiles.

    This used to send a bare ``urllib`` HEAD request at each profile's
    ``base_url`` and call any non-2xx "unreachable" — which reports a
    perfectly healthy Ollama or LM Studio as broken (they answer 404/405 to
    a HEAD on ``/v1``), and told you nothing at all about whether the model
    name or the API key actually work. It also predated the provider layer
    entirely: ``aida.providers.profiles.validate_profile`` has done this
    properly since Phase 2, through each SDK's own client
    (``models.list()`` for OpenAI-compatible endpoints, a 1-token message
    for Anthropic/Argo), and was simply never wired in here.

    Still best-effort and never raising: an unreachable or hung endpoint is
    a reported failed check, not an exception, and each profile is checked
    under its own timeout so one dead host can't stall the whole report.
    If settings failed to load at all, that is already reported by the
    ``config_files`` check, so this is skipped rather than duplicated.
    """
    if settings is None:
        return []

    profiles = settings.providers.profiles
    if not profiles:
        providers_yaml = paths.config_dir() / "providers.yaml"
        return [
            CheckResult(
                "provider_endpoints",
                True,
                "no provider profiles configured yet — add one by editing "
                f"{providers_yaml}, or use the GUI's Providers… dialog (profile format: docs/providers-and-secrets.md)",
            )
        ]

    async def _validate_all() -> list[ProfileValidation]:
        return [await validate_profile(profile) for profile in profiles.values()]

    try:
        validations = asyncio.run(_validate_all())
    except Exception as exc:  # noqa: BLE001 - doctor must never crash on a provider problem
        return [CheckResult("provider_endpoints", False, f"provider check failed to run: {exc}")]

    return [CheckResult(f"provider:{v.name}", v.ok, v.detail) for v in validations]


def _effective_records_dir(settings: Settings | None) -> Path:
    """The records dir the app will actually use — honoring
    ``config.yaml``'s ``records_dir`` override. Checking the *default*
    location instead (what this did before) reports "writable" for a
    directory the user's config never touches, and stays silent about the
    one it does."""
    configured = settings.app.records_dir if settings is not None else None
    return paths.ensure_records_dir(Path(configured) if configured else None)


def _check_pyirena_mcp(settings: Settings | None) -> CheckResult:
    """Is pyIrena's MCP server installed, and is AIDA actually configured to
    use it?

    pyIrena is the one MCP server this audience is practically guaranteed to
    want, and "my tools aren't there" is the failure a new user is least
    equipped to diagnose — the server can be perfectly installed while
    ``mcp.json`` has never heard of it, which looks identical from the chat
    window. So this check reports the *combination* of the two, and always
    names the one command that fixes whichever half is missing.

    Never a hard failure. A user with no interest in pyIrena (running AIDA
    for document work, say) must not see a red FAIL for a package they
    deliberately did not install, so "not installed" reports OK with an
    explanation — this check exists to inform, not to grade.
    """
    configured = []
    if settings is not None:
        configured = [
            server.name
            for server in settings.mcp.servers.values()
            if "pyirena" in Path(server.command).name.lower()
            or any("pyirena" in arg for arg in server.args)
        ]

    candidates = find_pyirena_mcp()

    if configured and candidates:
        version = pyirena_version(candidates[0])
        suffix = f" (pyIrena {version} found on this machine)" if version else ""
        return CheckResult(
            "pyirena_mcp", True, f"configured as {', '.join(configured)}{suffix}"
        )
    if configured:
        return CheckResult(
            "pyirena_mcp",
            True,
            f"configured as {', '.join(configured)}, but no pyirena-mcp was found on this "
            "machine — if it stops starting, re-run `aida mcp add-pyirena --force`",
        )
    if candidates:
        version = pyirena_version(candidates[0])
        suffix = f" (pyIrena {version})" if version else ""
        return CheckResult(
            "pyirena_mcp",
            True,
            f"installed{suffix} at {candidates[0].command} but NOT configured in AIDA — "
            "add it with `aida mcp add-pyirena` (or the MCP dialog's \"Add pyIrena…\" button)",
        )
    return CheckResult(
        "pyirena_mcp",
        True,
        'not installed (optional) — `pip install "pyirena[mcp]"`, then `aida mcp add-pyirena`',
    )


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = [_check_python_version()]
    settings, config_result = _load_settings_safely()
    results.append(config_result)
    results.append(_check_writable("app_dir", paths.app_dir()))
    results.append(_check_writable("logs_dir", paths.logs_dir()))
    results.append(_check_writable("artifacts_dir", paths.artifacts_dir()))
    results.append(_check_writable("records_dir", _effective_records_dir(settings)))
    results.append(_check_keyring())
    results.append(_check_pyirena_mcp(settings))
    results.extend(_check_provider_endpoints(settings))
    return results


def format_report(results: list[CheckResult]) -> str:
    lines = ["AIDA doctor report", "=" * 19]
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        lines.append(f"[{mark}] {r.name}: {r.detail}")
    n_fail = sum(1 for r in results if not r.ok)
    lines.append("")
    lines.append(f"{len(results) - n_fail}/{len(results)} checks passed.")
    return "\n".join(lines)


def main() -> int:
    results = run_checks()
    print(format_report(results))
    return 1 if any(not r.ok for r in results) else 0
