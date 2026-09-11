"""Read a bundle's contents without importing it.

Tier 1 read each section inline as it merged it, which was enough for
"import everything". Selecting *part* of a bundle needs the contents as data
first — to list them, to compute a dependency closure over them
(``aida.portability.closure``), and to check their paths against this
machine (``aida.portability.path_issues``) — all before anything is written.

Nothing here touches ``~/.aida``: a ``BundleContents`` is a pure read of the
zip, safe to build for a file the user merely pointed at in a dialog.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from aida.config.settings import (
    KnowledgeConfig,
    McpConfig,
    ProvidersConfig,
    SchedulesConfig,
    WorkflowConfig,
    WorkspacesConfig,
)

KIND_PROFILE = "profile"
KIND_EMBEDDING = "embedding profile"
KIND_SERVER = "MCP server"
KIND_WORKSPACE = "workspace"
KIND_KNOWLEDGE = "knowledge base"
KIND_WORKFLOW = "workflow"
KIND_SCHEDULE = "schedule"
KIND_SKILL = "skill"
KIND_PROMPT = "prompt"

#: The order a picker lists kinds in — dependencies before the things that
#: depend on them, so reading top to bottom explains itself.
KIND_ORDER = (
    KIND_PROFILE,
    KIND_EMBEDDING,
    KIND_SERVER,
    KIND_SKILL,
    KIND_KNOWLEDGE,
    KIND_WORKSPACE,
    KIND_WORKFLOW,
    KIND_SCHEDULE,
    KIND_PROMPT,
)

#: Kinds a user may pick directly. Prompt files are excluded: they are not
#: independently meaningful (a prompt exists because a workspace names it)
#: and they arrive through the closure of the workspace that uses them.
SELECTABLE_KINDS = tuple(k for k in KIND_ORDER if k != KIND_PROMPT)

#: Top-level bundle members that are metadata, not content.
_METADATA_MEMBERS = frozenset({"manifest.json", "paths.json", "secrets.json", "README.txt"})


@dataclass(frozen=True)
class BundleItem:
    """One selectable thing in a bundle, as a picker wants to show it."""

    kind: str
    name: str
    #: A one-line summary — the model behind a profile, the folder behind a
    #: workspace. Without it a picker is a list of bare names and the user
    #: has to import blind to find out what they chose.
    detail: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.name)


@dataclass
class BundleContents:
    """Everything a bundle holds, parsed but not yet applied to anything."""

    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    app: dict[str, Any] = field(default_factory=dict)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    workspaces: WorkspacesConfig = field(default_factory=WorkspacesConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    schedules: SchedulesConfig = field(default_factory=SchedulesConfig)
    workflows: dict[str, WorkflowConfig] = field(default_factory=dict)
    #: Skill name -> the zip members holding it. A list, not one member: a
    #: skill may be a folder (``<name>/SKILL.md`` plus siblings), the second
    #: layout ``aida.core.context.skill_path`` resolves.
    skill_members: dict[str, list[str]] = field(default_factory=dict)
    #: Workflow name -> its zip member.
    workflow_members: dict[str, str] = field(default_factory=dict)
    #: Prompt-class file path (relative to ``~/.aida``) -> its zip member.
    #: Keyed by the path a workspace's ``system_prompt`` would name, so a
    #: lookup from a workspace is a plain dict hit.
    prompt_members: dict[str, str] = field(default_factory=dict)
    secret_refs: list[str] = field(default_factory=list)
    redacted_env: list[tuple[str, str]] = field(default_factory=list)
    paths_inventory: list[dict[str, str]] = field(default_factory=list)

    @property
    def bundle_version(self) -> int:
        return int(self.manifest.get("bundle_version", 1))

    @property
    def created_at(self) -> str:
        return str(self.manifest.get("created_at", ""))

    @property
    def include_personal(self) -> bool:
        return bool(self.manifest.get("include_personal", False))

    def items(self) -> list[BundleItem]:
        """Everything selectable, in ``KIND_ORDER``, each with a summary."""
        out: list[BundleItem] = []
        for name, profile in sorted(self.providers.profiles.items()):
            out.append(BundleItem(KIND_PROFILE, name, profile.model or profile.kind))
        for name, profile in sorted(self.providers.embedding_profiles.items()):
            out.append(BundleItem(KIND_EMBEDDING, name, profile.model or profile.kind))
        for name, server in sorted(self.mcp.servers.items()):
            groups = ", ".join(server.groups) if server.groups else "no group"
            out.append(BundleItem(KIND_SERVER, name, groups))
        for name in sorted(self.skill_members):
            out.append(BundleItem(KIND_SKILL, name))
        for name, kb in sorted(self.knowledge.knowledge_bases.items()):
            out.append(BundleItem(KIND_KNOWLEDGE, name, f"{len(kb.source_folders)} source(s)"))
        for name, workspace in sorted(self.workspaces.workspaces.items()):
            bits = [b for b in (workspace.profile, workspace.mcp_group) if b and b != "none"]
            out.append(BundleItem(KIND_WORKSPACE, name, " · ".join(bits)))
        for name, workflow in sorted(self.workflows.items()):
            out.append(BundleItem(KIND_WORKFLOW, name, f"{len(workflow.steps)} step(s)"))
        for name, schedule in sorted(self.schedules.schedules.items()):
            when = schedule.at or schedule.every or ""
            out.append(BundleItem(KIND_SCHEDULE, name, f"{schedule.workflow} {when}".strip()))
        return out

    def all_keys(self) -> set[tuple[str, str]]:
        """Every selectable item's key — the default selection ("all of
        it"), and the set a requested selection is validated against."""
        return {item.key for item in self.items()}

    def prompt_for_workspace(self, workspace_name: str) -> str | None:
        """The bundle member holding this workspace's ``system_prompt``
        file, if it names one that the bundle actually carries."""
        workspace = self.workspaces.workspaces.get(workspace_name)
        if workspace is None or not workspace.system_prompt:
            return None
        return workspace.system_prompt if workspace.system_prompt in self.prompt_members else None


def read_contents(source: str | Path) -> BundleContents:
    """Parse a bundle. Raises ``BundleError`` for anything unreadable or
    written by a newer AIDA — the same gate ``import_bundle`` goes through,
    so a GUI can preview a file with exactly the errors an import would
    give."""
    # Imported here rather than at module scope: bundle.py imports this
    # module for its reader, so a top-level import back would be a cycle.
    from aida.portability.bundle import read_manifest

    path = Path(source).expanduser()
    manifest = read_manifest(path)
    contents = BundleContents(path=path, manifest=manifest)

    with zipfile.ZipFile(path) as archive:
        contents.app = _yaml_member(archive, "config/app.yaml")
        contents.providers = ProvidersConfig.from_dict(
            _yaml_member(archive, "config/providers.yaml")
        )
        contents.workspaces = WorkspacesConfig.from_dict(
            _yaml_member(archive, "config/workspaces.yaml")
        )
        contents.mcp = McpConfig.from_dict(_json_member(archive, "config/mcp.json"))
        contents.knowledge = KnowledgeConfig.from_dict(
            _yaml_member(archive, "config/knowledge.yaml")
        )
        contents.schedules = SchedulesConfig.from_dict(
            _yaml_member(archive, "config/schedules.yaml")
        )

        secrets = _json_member(archive, "secrets.json")
        contents.secret_refs = list(secrets.get("secret_refs") or [])
        contents.redacted_env = [
            (entry.get("server", ""), entry.get("key", ""))
            for entry in secrets.get("redacted_env") or []
        ]
        raw_paths = _json_member(archive, "paths.json", default=[])
        contents.paths_inventory = raw_paths if isinstance(raw_paths, list) else []

        for member in archive.namelist():
            if member.endswith("/") or member in _METADATA_MEMBERS:
                continue
            if member.startswith("config/"):
                continue
            if member.startswith("skills/"):
                name = _skill_name(member)
                if name:
                    contents.skill_members.setdefault(name, []).append(member)
                continue
            if member.startswith("workflows/") and member.endswith(".yaml"):
                name = PurePosixPath(member).stem
                contents.workflow_members[name] = member
                contents.workflows[name] = WorkflowConfig.from_dict(
                    name, _yaml_member(archive, member)
                )
                continue
            # Everything else is a prompt-class content file, kept at the
            # path it will occupy under ~/.aida. Keyed that way because a
            # workspace's `system_prompt` names exactly this string, and it
            # is resolved relative to the config dir
            # (aida.workspace.workspaces._system_prompt_file_path).
            contents.prompt_members[member] = member

    for members in contents.skill_members.values():
        members.sort()
    return contents


def _skill_name(member: str) -> str | None:
    """``skills/saxs.md`` -> ``saxs``; ``skills/saxs/SKILL.md`` and any of
    its siblings -> ``saxs``."""
    relative = PurePosixPath(member[len("skills/") :])
    parts = relative.parts
    if not parts:
        return None
    if len(parts) == 1:
        return relative.stem if relative.suffix else None
    return parts[0]


def _yaml_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        raw = archive.read(name)
    except KeyError:
        return {}
    try:
        return yaml.safe_load(raw.decode("utf-8")) or {}
    except yaml.YAMLError:
        return {}


def _json_member(archive: zipfile.ZipFile, name: str, default: Any = None) -> Any:
    try:
        raw = archive.read(name)
    except KeyError:
        return {} if default is None else default
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {} if default is None else default


__all__ = [
    "KIND_EMBEDDING",
    "KIND_KNOWLEDGE",
    "KIND_ORDER",
    "KIND_PROFILE",
    "KIND_PROMPT",
    "KIND_SCHEDULE",
    "KIND_SERVER",
    "KIND_SKILL",
    "KIND_WORKFLOW",
    "KIND_WORKSPACE",
    "SELECTABLE_KINDS",
    "BundleContents",
    "BundleItem",
    "read_contents",
]
