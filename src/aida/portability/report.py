"""Render an import/export result as text.

Shared by the CLI and the GUI so both say exactly the same thing — the
import report is the actual deliverable of this feature (a bundle carries
configuration; it cannot carry conda environments, installed MCP servers,
data folders or keychain entries), and it would be a poor outcome for the
two front ends to describe the same import differently.
"""

from __future__ import annotations

from aida.portability.bundle import ExportResult, ImportReport
from aida.portability.closure import ClosureResult
from aida.portability.contents import KIND_ORDER, BundleContents
from aida.portability.path_issues import ROLE_EXECUTABLE, PathIssue


def format_export(result: ExportResult) -> str:
    lines = [f"Wrote {result.path}"]
    interesting = {k: v for k, v in result.counts.items() if v}
    if interesting:
        lines.append("  " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in interesting.items()))
    tokenized = len(result.paths)
    if tokenized:
        lines.append(f"  {tokenized} machine-specific path(s) replaced with portable tokens")
    if result.redacted_env:
        lines.append("")
        lines.append("Redacted from mcp.json (never exported):")
        lines += [f"  {server}: {key}" for server, key in result.redacted_env]
    if result.secret_refs:
        lines.append("")
        lines.append("Secrets the target machine will need (set after importing):")
        lines += [f"  aida config secret set {ref}" for ref in result.secret_refs]
    if not result.include_personal:
        lines.append("")
        lines.append(
            "Personal context and workspace notes were excluded. "
            "Use --include-personal when moving to your own machine."
        )
    return "\n".join(lines)


def format_contents(contents: BundleContents) -> str:
    """List what a bundle holds, without importing it (`--list`).

    Grouped by kind in dependency order, so reading top to bottom shows the
    profiles and servers before the workspaces that use them. Each line is
    also a valid `--only` selector, which is the point of the format.
    """
    lines = [f"{contents.path}"]
    if contents.created_at:
        lines.append(
            f"  created {contents.created_at} by AIDA {contents.manifest.get('aida_version', '?')}"
        )
    if contents.include_personal:
        lines.append("  includes personal context and workspace notes")

    items = contents.items()
    if not items:
        lines.append("")
        lines.append("This bundle contains nothing importable.")
        return "\n".join(lines)

    by_kind: dict[str, list[tuple[str, str]]] = {}
    for item in items:
        by_kind.setdefault(item.kind, []).append((item.name, item.detail))
    width = max(len(name) for name, _ in ((n, d) for pairs in by_kind.values() for n, d in pairs))
    for kind in KIND_ORDER:
        entries = by_kind.get(kind)
        if not entries:
            continue
        lines.append("")
        lines.append(f"{kind} ({len(entries)}):")
        for name, detail in entries:
            # rstrip: a kind with no details (skills) would otherwise pad
            # every line out to the column width with trailing blanks.
            lines.append(f"  {name:<{width}}   {detail}".rstrip())

    lines.append("")
    lines.append("Import all of it:      aida config import <bundle>")
    lines.append("Or part of it:         aida config import <bundle> --only workspace:NAME")
    lines.append("                       (dependencies come along automatically)")
    if contents.secret_refs:
        lines.append("")
        lines.append("Secrets it expects (no values travel in a bundle):")
        lines += [f"  {ref}" for ref in contents.secret_refs]
    return "\n".join(lines)


def format_path_issues(issues: list[PathIssue]) -> str:
    """The paths that will not resolve here, as a remappable list."""
    if not issues:
        return "Every path in this bundle resolves on this machine."
    lines = ["Paths that do not resolve on this machine:"]
    for issue in issues:
        marker = "executable" if issue.role == ROLE_EXECUTABLE else "folder"
        lines.append("")
        lines.append(f"  {issue.value}   [{marker}]")
        lines.append(f"      {issue.problem}")
        lines.append(f"      used by: {', '.join(issue.where)}")
        if issue.suggestion:
            lines.append(f"      found on PATH: {issue.suggestion}")
    lines.append("")
    lines.append("Remap any of them with, e.g.:")
    lines.append(f"  --map '{issues[0].value}=/the/right/path'")
    lines.append("A mapping matches by prefix, so one entry can redirect a whole tree.")
    return "\n".join(lines)


def format_closure(contents: BundleContents, chosen: ClosureResult) -> str:
    """What a `--only` selection grew into, and why."""
    lines = [f"Selected {len(chosen.keys)} item(s) from {contents.path.name}"]
    if chosen.added:
        lines.append("")
        lines.append("Pulled in as dependencies:")
        for kind, name in sorted(chosen.added):
            lines.append(f"  {kind}: {name}")
    if chosen.missing:
        lines.append("")
        lines.append("Unsatisfied references:")
        lines += [f"  {problem}" for problem in chosen.missing]
    return "\n".join(lines)


def format_import(report: ImportReport) -> str:
    verb = "Would import" if report.dry_run else "Imported"
    lines = [f"{verb} {report.source}"]
    if report.created_at:
        lines.append(f"  bundle created {report.created_at}")
    if report.dry_run:
        lines.append("  PREVIEW — nothing was written")

    lines.extend(_section("Added", report.added))
    lines.extend(_section("Overwritten", report.overwritten))
    lines.extend(_section("Skipped (already present)", report.skipped))

    if report.selected and report.pulled_in:
        lines.append("")
        lines.append("Pulled in as dependencies of what you selected:")
        lines += [f"  {kind}: {name}" for kind, name in report.pulled_in]

    if report.renamed:
        lines.append("")
        lines.append("Renamed to avoid a collision:")
        lines += [f"  {old} -> {new}" for old, new in report.renamed]

    if report.app_settings_applied:
        lines.append("")
        lines.append("Applied the bundle's app settings to config.yaml.")

    if report.backups:
        lines.append("")
        lines.append("Backed up before writing:")
        lines += [f"  {path.name}" for path in report.backups]

    if report.unresolved_commands:
        lines.append("")
        lines.append("COULD NOT LOCATE ON THIS MACHINE — fix before use:")
        lines += [f"  {who}: {problem}" for who, problem in report.unresolved_commands]
        lines.append(
            "  (install the missing conda env / package, then correct the path in "
            "the MCP Servers dialog or mcp.json)"
        )

    if report.missing_folders:
        lines.append("")
        lines.append("Folders referenced but not present here:")
        lines += [f"  {folder}" for folder in report.missing_folders]

    if report.secret_refs or report.redacted_env:
        lines.append("")
        lines.append("SECRETS TO SET (none travel in a bundle):")
        lines += [f"  aida config secret set {ref}" for ref in report.secret_refs]
        lines += [
            f"  mcp.json: {server} needs env {key} filled in" for server, key in report.redacted_env
        ]

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines += [f"  {warning}" for warning in report.warnings]

    if report.total_added == 0 and not report.overwritten:
        lines.append("")
        scope = "selection" if report.selected else "bundle"
        lines.append(f"Nothing was added — every item in the {scope} is already configured here.")
    return "\n".join(lines)


def _section(title: str, bucket: dict[str, list[str]]) -> list[str]:
    if not any(bucket.values()):
        return []
    lines = ["", f"{title}:"]
    for kind in sorted(bucket):
        names = bucket[kind]
        if names:
            lines.append(f"  {kind}: {', '.join(sorted(names))}")
    return lines


__all__ = [
    "format_closure",
    "format_contents",
    "format_export",
    "format_import",
    "format_path_issues",
]
