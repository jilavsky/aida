"""Expand a partial selection into everything it needs to actually work.

Picking "just the `use-pyirena` workspace" out of a bundle and importing
literally that produces a workspace that fails validation on arrival: it
names a provider profile that wasn't imported, skills that aren't there, and
an `mcp_group` that resolves to no servers. A half-imported workspace is
worse than no workspace, because it looks configured.

So a selection is a *seed*, and this module takes its transitive closure
over the references AIDA's config format already has:

    workspace  -> profile, skills, knowledge bases, prompt file,
                  and every server whose `groups` contains its `mcp_group`
    MCP server -> the skills it attaches
    knowledge  -> its embedding profile
    workflow   -> its workspace, its profile, its mcp_group's servers
    schedule   -> its workflow

Iterated to a fixpoint, because those chain: a schedule pulls a workflow,
which pulls a workspace, which pulls a profile and three servers, one of
which attaches a skill.

A reference the *bundle itself* does not contain (a workspace naming a
profile that was never exported) is not an error here — it is reported, and
the import proceeds. The alternative, refusing the whole import, would make
one stale reference in a colleague's config block everything else in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aida.mcp.groups import NO_GROUP
from aida.portability.contents import (
    KIND_EMBEDDING,
    KIND_KNOWLEDGE,
    KIND_PROFILE,
    KIND_PROMPT,
    KIND_SCHEDULE,
    KIND_SERVER,
    KIND_SKILL,
    KIND_WORKFLOW,
    KIND_WORKSPACE,
    BundleContents,
)

Key = tuple[str, str]


@dataclass
class ClosureResult:
    """The expanded selection, plus what it was not able to satisfy."""

    #: Everything to import, seeds included.
    keys: set[Key] = field(default_factory=set)
    #: Items pulled in only because something selected needs them —
    #: ``keys - seeds``. A picker shows these as checked-because-required.
    added: set[Key] = field(default_factory=set)
    #: One line per reference the bundle cannot satisfy, ready for the
    #: import report.
    missing: list[str] = field(default_factory=list)

    def names(self, kind: str) -> set[str]:
        return {name for k, name in self.keys if k == kind}

    def has(self, kind: str, name: str) -> bool:
        return (kind, name) in self.keys


def expand_selection(
    contents: BundleContents,
    seeds: set[Key] | None = None,
    *,
    follow_dependencies: bool = True,
) -> ClosureResult:
    """Grow ``seeds`` into the full set of items to import.

    ``seeds=None`` means "everything in the bundle" — the Tier 1 behaviour,
    and still the default at every call site that does not offer a picker.
    ``follow_dependencies=False`` imports exactly what was asked for, for
    someone who means it (a deliberate "just this one skill, I have the
    rest").
    """
    available = contents.all_keys()
    # A seed naming something the bundle does not have is dropped rather
    # than raising: the CLI already reports unknown selectors by name
    # (`parse_selection`), and a GUI cannot produce one at all.
    selected = set(available) if seeds is None else {k for k in seeds if k in available}

    result = ClosureResult(keys=set(selected))
    if follow_dependencies:
        _grow(contents, result, available)
    # Prompt files are never seeds; they arrive with the workspace that
    # names them, whether or not dependencies are being followed — a
    # workspace without its prompt file silently uses the *path* as its
    # system prompt.
    for name in sorted(result.names(KIND_WORKSPACE)):
        member = contents.prompt_for_workspace(name)
        if member is not None:
            result.keys.add((KIND_PROMPT, member))
    result.added = result.keys - selected
    return result


def _grow(contents: BundleContents, result: ClosureResult, available: set[Key]) -> None:
    """Fixpoint over the reference graph. Bounded by the bundle's own size:
    each pass can only add keys already in ``available``, and a pass that
    adds nothing ends it."""
    missing: dict[str, None] = {}  # ordered set — the same broken reference
    # is usually reachable by more than one path, and repeating it in the
    # report helps nobody.

    def require(kind: str, name: str | None, needed_by: str) -> None:
        if not name or (kind == KIND_SERVER and name == NO_GROUP):
            return
        if (kind, name) in available:
            result.keys.add((kind, name))
        elif (kind, name) not in result.keys:
            missing[f"{needed_by} needs {kind} {name!r}, which is not in the bundle"] = None

    changed = True
    while changed:
        before = len(result.keys)

        for name in sorted(result.names(KIND_WORKSPACE)):
            workspace = contents.workspaces.workspaces.get(name)
            if workspace is None:
                continue
            label = f"workspace {name!r}"
            require(KIND_PROFILE, workspace.profile, label)
            for skill in workspace.skills:
                require(KIND_SKILL, skill, label)
            for kb in workspace.knowledge_bases:
                require(KIND_KNOWLEDGE, kb, label)
            _require_group(contents, result, available, missing, workspace.mcp_group, label)

        for name in sorted(result.names(KIND_SERVER)):
            server = contents.mcp.servers.get(name)
            if server is None:
                continue
            for skill in server.skills:
                require(KIND_SKILL, skill, f"MCP server {name!r}")

        for name in sorted(result.names(KIND_KNOWLEDGE)):
            kb = contents.knowledge.knowledge_bases.get(name)
            if kb is None:
                continue
            require(KIND_EMBEDDING, kb.embedding_profile, f"knowledge base {name!r}")

        for name in sorted(result.names(KIND_WORKFLOW)):
            workflow = contents.workflows.get(name)
            if workflow is None:
                continue
            label = f"workflow {name!r}"
            require(KIND_WORKSPACE, workflow.workspace, label)
            require(KIND_PROFILE, workflow.profile, label)
            _require_group(contents, result, available, missing, workflow.mcp_group, label)

        for name in sorted(result.names(KIND_SCHEDULE)):
            schedule = contents.schedules.schedules.get(name)
            if schedule is None:
                continue
            require(KIND_WORKFLOW, schedule.workflow, f"schedule {name!r}")

        changed = len(result.keys) != before

    result.missing = list(missing)


def _require_group(
    contents: BundleContents,
    result: ClosureResult,
    available: set[Key],
    missing: dict[str, None],
    group: str | None,
    needed_by: str,
) -> None:
    """Pull in every server that answers to ``group``.

    A group is not a thing that exists on its own — it is a label servers
    opt into (``aida.mcp.groups.resolve_group``), so the dependency is
    "whichever servers claim it", and a group nothing claims is exactly the
    "this workspace will have no MCP tools" case ``validate_workspace``
    already warns about.
    """
    if not group or group == NO_GROUP:
        return
    matched = [name for name, server in contents.mcp.servers.items() if group in server.groups]
    if not matched:
        missing[f"{needed_by} uses mcp_group {group!r}, which no server in the bundle provides"] = (
            None
        )
        return
    for name in matched:
        if (KIND_SERVER, name) in available:
            result.keys.add((KIND_SERVER, name))


def parse_selection(values: list[str], contents: BundleContents) -> tuple[set[Key], list[str]]:
    """Parse ``kind:name`` selectors from the command line.

    Kinds are matched leniently — ``workspace``, ``workspaces`` and
    ``mcp-server`` all work — because the canonical spellings include a
    space (``MCP server``, ``embedding profile``) and nobody should have to
    quote a shell argument to name one.
    """
    keys: set[Key] = set()
    problems: list[str] = []
    by_alias = _kind_aliases()
    known = contents.all_keys()
    for raw in values:
        entry = raw.strip()
        if not entry:
            continue
        if ":" not in entry:
            problems.append(f"{entry!r}: expected KIND:NAME, e.g. workspace:analysis")
            continue
        kind_text, _, name = entry.partition(":")
        kind = by_alias.get(_normalize(kind_text))
        if kind is None:
            problems.append(f"{entry!r}: unknown kind {kind_text!r}")
            continue
        name = name.strip()
        if (kind, name) not in known:
            problems.append(f"{entry!r}: no {kind} named {name!r} in this bundle")
            continue
        keys.add((kind, name))
    return keys, problems


def _normalize(text: str) -> str:
    return text.strip().lower().replace("-", " ").replace("_", " ")


def _kind_aliases() -> dict[str, str]:
    from aida.portability.contents import SELECTABLE_KINDS

    aliases: dict[str, str] = {}
    for kind in SELECTABLE_KINDS:
        canonical = _normalize(kind)
        aliases[canonical] = kind
        aliases[canonical + "s"] = kind
        aliases[canonical.replace(" ", "")] = kind
    # The most likely shorthands for the two-word kinds.
    aliases["server"] = KIND_SERVER
    aliases["servers"] = KIND_SERVER
    aliases["mcp"] = KIND_SERVER
    aliases["kb"] = KIND_KNOWLEDGE
    aliases["embedding"] = KIND_EMBEDDING
    return aliases


__all__ = ["ClosureResult", "Key", "expand_selection", "parse_selection"]
