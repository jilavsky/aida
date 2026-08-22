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
                f"{providers_yaml} (see PLAN.md / README for the profile format)",
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


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = [_check_python_version()]
    settings, config_result = _load_settings_safely()
    results.append(config_result)
    results.append(_check_writable("app_dir", paths.app_dir()))
    results.append(_check_writable("logs_dir", paths.logs_dir()))
    results.append(_check_writable("artifacts_dir", paths.artifacts_dir()))
    results.append(_check_writable("records_dir", _effective_records_dir(settings)))
    results.append(_check_keyring())
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
