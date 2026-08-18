"""``aida doctor`` — environment and configuration diagnostics.

Reports, per PLAN.md Phase 1 acceptance criteria:

- Python version
- config file status/validity (config.yaml, providers.yaml, workspaces.yaml,
  mcp.json)
- keyring availability
- reachable provider endpoints (ping only — Phase 1 has no provider layer
  yet, so this is a placeholder that reports "not configured" rather than
  failing)
- writable dirs (``~/.aida`` and its subdirectories, records dir)

Designed to be used both as a CLI command and importable for tests: the
report is built as a list of ``CheckResult`` and formatting is separate from
checking, so tests can assert on structured results without parsing text.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from aida.config import paths
from aida.config.secrets import keyring_available
from aida.config.settings import Settings, load_settings


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
    """Ping-only checks for configured provider profiles.

    Phase 1 ships no provider layer, so with zero profiles configured this
    reports an informational pass rather than a failure — providers arrive
    in Phase 2. If settings failed to load at all, that is already reported
    by the ``config_files`` check, so this is skipped rather than duplicated.
    """
    if settings is None:
        return []

    profiles = settings.providers.profiles
    if not profiles:
        return [
            CheckResult(
                "provider_endpoints",
                True,
                "no provider profiles configured yet (expected before Phase 2)",
            )
        ]

    results: list[CheckResult] = []
    for name, profile in profiles.items():
        # Ping-only, best-effort: Phase 1 does not depend on network access,
        # so a failed/absent connection is reported, not raised.
        try:
            import urllib.request

            if not profile.base_url:
                results.append(
                    CheckResult(f"provider:{name}", True, "no base_url set (SDK default)")
                )
                continue
            req = urllib.request.Request(profile.base_url, method="HEAD")
            urllib.request.urlopen(req, timeout=2)  # noqa: S310
            results.append(CheckResult(f"provider:{name}", True, f"reachable: {profile.base_url}"))
        except Exception as exc:  # noqa: BLE001
            results.append(
                CheckResult(f"provider:{name}", False, f"unreachable: {profile.base_url} ({exc})")
            )
    return results


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = [_check_python_version()]
    settings, config_result = _load_settings_safely()
    results.append(config_result)
    results.append(_check_writable("app_dir", paths.app_dir()))
    results.append(_check_writable("logs_dir", paths.logs_dir()))
    results.append(_check_writable("artifacts_dir", paths.artifacts_dir()))
    results.append(_check_writable("records_dir", paths.ensure_records_dir()))
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
