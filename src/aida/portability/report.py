"""Render an import/export result as text.

Shared by the CLI and the GUI so both say exactly the same thing — the
import report is the actual deliverable of this feature (a bundle carries
configuration; it cannot carry conda environments, installed MCP servers,
data folders or keychain entries), and it would be a poor outcome for the
two front ends to describe the same import differently.
"""

from __future__ import annotations

from aida.portability.bundle import ExportResult, ImportReport


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


def format_import(report: ImportReport) -> str:
    lines = [f"Imported {report.source}"]
    if report.created_at:
        lines.append(f"  bundle created {report.created_at}")

    lines.extend(_section("Added", report.added))
    lines.extend(_section("Overwritten", report.overwritten))
    lines.extend(_section("Skipped (already present)", report.skipped))

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
        lines.append("Nothing was added — every item in the bundle is already configured here.")
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


__all__ = ["format_export", "format_import"]
