"""Find the paths in a bundle that will not work on *this* machine.

Tier 1 discovered these during the import itself and reported them
afterwards, which is the right time to learn about them and the wrong time
to fix them. This module answers the same question *before* anything is
written, so the CLI can print a table and the import dialog can offer a
"use this instead" column.

Two classes of problem, deliberately kept distinct because the fix differs:

``executable``
    An MCP server ``command``, or a workspace's ``python_interpreter``. The
    fix is usually installing something (a conda env, a package), and there
    is often a good guess available from ``PATH``.
``folder``
    A source/target/templates/scratch folder, or a knowledge base's sources.
    The fix is almost always "it is somewhere else here", and there is no
    guessing it — which is exactly why a mapping table is worth having.

A missing folder is *not* an error. A workspace whose data folder is on a
network mount that happens to be offline is a normal state at a beamline,
and AIDA already treats it as a warning everywhere else
(``validate_workspace``). Listing it here is an offer to remap it, not a
demand.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from aida.portability.closure import ClosureResult
from aida.portability.contents import (
    KIND_KNOWLEDGE,
    KIND_SERVER,
    KIND_WORKSPACE,
    BundleContents,
)
from aida.portability.paths_map import PathMapper

ROLE_EXECUTABLE = "executable"
ROLE_FOLDER = "folder"


@dataclass
class PathIssue:
    """One bundle-side path that needs a decision, and everything a user
    needs to make it."""

    #: The value as the bundle stores it — possibly still tokenized. This is
    #: the key a `--map`/dialog override is written against, so it stays
    #: stable no matter what this machine resolves it to.
    value: str
    role: str
    #: Dotted config locations referencing this value, e.g.
    #: ``workspace 'analysis' source_folders``. More than one when several
    #: fields share a folder, which is common.
    where: list[str] = field(default_factory=list)
    problem: str = ""
    #: A best guess at the right value here, or ``None`` when guessing would
    #: be dishonest. Pre-fills the dialog's editable cell.
    suggestion: str | None = None

    @property
    def resolved_hint(self) -> str:
        return self.suggestion or ""


def inspect_paths(
    contents: BundleContents,
    mapper: PathMapper,
    selection: ClosureResult | None = None,
    *,
    include_app: bool = False,
) -> list[PathIssue]:
    """Every path in the selected part of ``contents`` that will not resolve
    here, deduplicated by value.

    ``mapper`` carries whatever overrides have been supplied so far, so
    calling this again after the user fills in the table shows the shrinking
    list of remaining problems — which is what makes the dialog's table feel
    like it is doing something.
    """
    issues: dict[str, PathIssue] = {}

    def add(value: str | None, role: str, where: str, problem: str, suggestion: str | None) -> None:
        if not value:
            return
        existing = issues.get(value)
        if existing is not None:
            if where not in existing.where:
                existing.where.append(where)
            return
        issues[value] = PathIssue(
            value=value, role=role, where=[where], problem=problem, suggestion=suggestion
        )

    def check_folder(value: str | None, where: str) -> None:
        if not value:
            return
        expanded = mapper.expand(value)
        if expanded and Path(expanded).expanduser().exists():
            return
        add(value, ROLE_FOLDER, where, f"not present here (would be {expanded})", None)

    def check_executable(value: str | None, where: str) -> None:
        if not value:
            return
        resolved, problem = mapper.expand_executable(value)
        if not problem:
            return
        guess = shutil.which(Path(str(resolved or value).replace("\\", "/")).name)
        add(value, ROLE_EXECUTABLE, where, problem, guess)

    wanted_workspaces = _names(selection, KIND_WORKSPACE, contents.workspaces.workspaces)
    for name in sorted(wanted_workspaces):
        workspace = contents.workspaces.workspaces[name]
        label = f"workspace {name!r}"
        for folder in workspace.source_folders:
            check_folder(folder, f"{label} source_folders")
        check_folder(workspace.target_folder, f"{label} target_folder")
        check_folder(workspace.templates_dir, f"{label} templates_dir")
        check_folder(workspace.saved_scripts_dir, f"{label} saved_scripts_dir")
        check_executable(workspace.python_interpreter, f"{label} python_interpreter")

    for name in sorted(_names(selection, KIND_SERVER, contents.mcp.servers)):
        server = contents.mcp.servers[name]
        check_executable(server.command, f"MCP server {name!r} command")

    for name in sorted(_names(selection, KIND_KNOWLEDGE, contents.knowledge.knowledge_bases)):
        kb = contents.knowledge.knowledge_bases[name]
        for folder in kb.source_folders:
            check_folder(folder, f"knowledge base {name!r} source_folders")

    if include_app:
        # Only when the app settings are actually going to be applied —
        # otherwise these paths are inert and listing them is noise.
        check_folder(contents.app.get("records_dir"), "app records_dir")
        check_folder(contents.app.get("scratch_dir"), "app scratch_dir")
        for folder in contents.app.get("allowed_folders") or []:
            check_folder(folder, "app allowed_folders")

    # Executables first: a missing command breaks a server outright, while a
    # missing folder is often just a mount that is not up yet.
    return sorted(issues.values(), key=lambda i: (i.role != ROLE_EXECUTABLE, i.value))


def _names(selection: ClosureResult | None, kind: str, available: dict[str, object]) -> set[str]:
    if selection is None:
        return set(available)
    return {name for name in selection.names(kind) if name in available}


def parse_overrides(values: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse ``FROM=TO`` mapping arguments from the command line.

    Split on the *first* ``=``: a Windows replacement path has no ``=`` in
    it, but a tokenized source could in principle, and "everything after the
    first separator is the replacement" is the rule people expect from
    ``VAR=value`` syntax everywhere else.
    """
    overrides: dict[str, str] = {}
    problems: list[str] = []
    for raw in values:
        entry = raw.strip()
        if not entry:
            continue
        source, separator, target = entry.partition("=")
        if not separator or not source.strip() or not target.strip():
            problems.append(
                f"{entry!r}: expected FROM=TO, e.g. '${{HOME}}/Experiments=/data/usaxs'"
            )
            continue
        overrides[source.strip()] = target.strip()
    return overrides, problems


__all__ = [
    "ROLE_EXECUTABLE",
    "ROLE_FOLDER",
    "PathIssue",
    "inspect_paths",
    "parse_overrides",
]
