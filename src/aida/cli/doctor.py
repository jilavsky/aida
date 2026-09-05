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
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from aida.config import paths
from aida.config.secrets import env_var_name, keyring_available
from aida.config.settings import Settings, load_settings
from aida.core.context import CONTEXT_SAFETY_FRACTION
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


def _check_secrets_non_interactive(settings: Settings | None) -> list[CheckResult]:
    """Phase 10: can each profile's secret actually be read from an
    unattended process (``aida run``, a schedule)?

    A profile without ``secret_ref`` (a local Ollama endpoint, say) needs
    no secret at all and is skipped. For the rest: the env-var override
    (``AIDA_SECRET_<PROFILE>``, ``aida.config.secrets``) always works
    non-interactively, on every platform — the OS keychain does not: a
    macOS keychain item not yet granted to this binary raises an
    unanswerable "allow access?" GUI prompt the first time a new process
    reads it, a Windows Credential Manager entry is unreachable from a task
    configured to run while logged out, and Linux's Secret Service needs an
    unlocked session keyring a cron job outside a graphical session may not
    have (planning/phase10_scheduling_design.md §3.2). This check cannot
    detect "would hang on a keychain prompt" without actually triggering
    one, which ``aida doctor`` must never risk doing — so it reports
    "reachable via the OS keychain" as informational, not a guarantee, and
    always names the env var that sidesteps the question entirely.
    """
    if settings is None or not settings.providers.profiles:
        return []

    from aida.config.secrets import get_secret

    results: list[CheckResult] = []
    for profile in settings.providers.profiles.values():
        if not profile.secret_ref:
            continue
        var_name = env_var_name(profile.secret_ref)
        if os.environ.get(var_name) is not None:
            results.append(
                CheckResult(f"secret_headless:{profile.name}", True, f"set via ${var_name} — safe for unattended use")
            )
            continue
        found = get_secret(profile.secret_ref) is not None
        if found:
            results.append(
                CheckResult(
                    f"secret_headless:{profile.name}",
                    True,
                    f"found in the OS keychain (not ${var_name}) — reachable interactively; for `aida run`/a "
                    f"schedule, verify it also works unattended, or set ${var_name} to be certain",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"secret_headless:{profile.name}",
                    False,
                    f"no secret found for {profile.secret_ref!r} — set it with `aida config secret set "
                    f"{profile.secret_ref}` or export ${var_name}",
                )
            )
    return results


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


def _check_orphan_attachments(settings: Settings | None) -> CheckResult:
    """Attachment folders with no conversation behind them.

    The backstop for "deleting a chat deletes its documents". Every orphan
    is a copy of a document somebody believed they had deleted — left by an
    interrupted delete, a hand-removed database, or a records folder that
    moved before the paths were recorded. Reported, never removed here:
    `aida conversations gc` does that, so a diagnostic command never
    deletes anything on its own.
    """
    from aida.persistence.cleanup import find_orphan_attachment_dirs
    from aida.persistence.store import ConversationStore

    try:
        records_dir = _effective_records_dir(settings)
        store = ConversationStore()
        try:
            orphans = find_orphan_attachment_dirs(store, records_dir=records_dir)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never crash the report
        return CheckResult("orphan_attachments", True, f"skipped — could not check ({exc})")

    if not orphans:
        return CheckResult("orphan_attachments", True, "no leftover attachment folders")
    names = ", ".join(o.name for o in orphans[:5])
    more = f" (+{len(orphans) - 5} more)" if len(orphans) > 5 else ""
    return CheckResult(
        "orphan_attachments",
        False,
        f"{len(orphans)} attachment folder(s) with no conversation: {names}{more} — "
        f"these hold copies of documents whose chat is gone; "
        f"run `aida conversations gc` to remove them",
    )


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


def _check_context_windows(settings: Settings | None) -> CheckResult:
    """PLAN.md §1.3 / planning/context_management.md §3.5: a profile with
    no ``context_window`` set falls back to the global
    ``AppConfig.max_context_tokens`` — fine for most models, but the one
    combination this exists to catch is a real window *smaller* than that
    default once tool schemas are counted (a 128k local model is unsafe on
    the 120k global default the moment ~10k of pyirena-mcp schemas are
    added). Informative, never a hard FAIL — same "inform, don't grade"
    shape as the pyIrena check: not every user needs per-profile tuning,
    and a profile that never touches a big MCP group is fine on the
    default either way."""
    if settings is None:
        return CheckResult("context_windows", True, "skipped — config failed to load")
    profiles = settings.providers.profiles
    if not profiles:
        return CheckResult("context_windows", True, "no provider profiles configured yet")

    unset = sorted(name for name, profile in profiles.items() if profile.context_window is None)
    configured = {name: profile.context_window for name, profile in profiles.items() if profile.context_window}

    notes = []
    if unset:
        notes.append(
            f"{', '.join(unset)}: no context_window set, falls back to the global "
            f"max_context_tokens ({settings.app.max_context_tokens:,})"
        )
    if configured:
        smallest_name, smallest_window = min(configured.items(), key=lambda item: item[1])
        if settings.app.max_context_tokens > smallest_window:
            notes.append(
                f"the global max_context_tokens ({settings.app.max_context_tokens:,}) is larger than "
                f"{smallest_name!r}'s configured context_window ({smallest_window:,}) — any other profile "
                "still falling back to that global default could be using a real window smaller than it"
            )
    if not notes:
        return CheckResult("context_windows", True, "every profile has an explicit context_window set")
    return CheckResult("context_windows", True, "; ".join(notes))


def _check_max_tokens_vs_context_window(settings: Settings | None) -> CheckResult:
    """Catches a specific, easy-to-hit mix-up between ``max_tokens`` and
    ``context_window`` (see ``ProviderProfile``'s docstring): someone reads
    "max tokens" as the model's total window and sets it to that model's
    full context size — e.g. a 262k-context Ollama model with
    ``max_tokens: 262000`` — instead of leaving it unset (a safe 4096
    default) or a modest reply budget like 4096-16000.

    That single mistake breaks history budgeting unconditionally:
    ``aida.core.context.history_budget`` computes
    ``context_window * 0.85 - max_tokens - tool_schema_tokens``, and once
    ``max_tokens`` alone is close to or larger than the safety-adjusted
    window, the result is negative *before any tool schema is counted* —
    every turn clamps to ``MIN_HISTORY_BUDGET`` (8000 tokens) regardless of
    which MCP group is active, which looks like "my context budget is
    tiny" even though the configured window is huge. Unlike
    ``_check_context_windows``'s "no context_window set" (a sensible
    default that's merely suboptimal), there's no legitimate reading of
    this combination — nobody wants next to nothing reserved for history —
    so this is the one context-window check that FAILs."""
    if settings is None:
        return CheckResult("max_tokens_vs_context_window", True, "skipped — config failed to load")
    profiles = settings.providers.profiles
    if not profiles:
        return CheckResult("max_tokens_vs_context_window", True, "no provider profiles configured yet")

    bad = []
    for name, profile in profiles.items():
        if profile.max_tokens is None or profile.context_window is None:
            continue
        usable = int(profile.context_window * CONTEXT_SAFETY_FRACTION)
        if profile.max_tokens >= usable:
            bad.append(
                f"{name!r}: max_tokens ({profile.max_tokens:,}) leaves no room in context_window "
                f"({profile.context_window:,}) for history — max_tokens caps only the reply's "
                "OUTPUT length, it is not the model's total window; unset it (4096 default) or use "
                "a modest reply budget like 4096-16000, not the context_window value"
            )
    if not bad:
        return CheckResult(
            "max_tokens_vs_context_window", True, "no profile's max_tokens crowds out its context_window"
        )
    return CheckResult("max_tokens_vs_context_window", False, "; ".join(bad))


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = [_check_python_version()]
    settings, config_result = _load_settings_safely()
    results.append(config_result)
    results.append(_check_writable("app_dir", paths.app_dir()))
    results.append(_check_writable("logs_dir", paths.logs_dir()))
    results.append(_check_writable("artifacts_dir", paths.artifacts_dir()))
    results.append(_check_writable("records_dir", _effective_records_dir(settings)))
    results.append(_check_keyring())
    results.append(_check_orphan_attachments(settings))
    results.append(_check_pyirena_mcp(settings))
    results.append(_check_context_windows(settings))
    results.append(_check_max_tokens_vs_context_window(settings))
    results.extend(_check_secrets_non_interactive(settings))
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
