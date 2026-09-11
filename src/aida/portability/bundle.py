"""Export an AIDA setup to a portable bundle, and import one back.

A bundle is a plain zip — stdlib ``zipfile``, no new dependency, openable
and readable without AIDA:

``manifest.json``
    ``bundle_version``, the AIDA version that wrote it, when, and whether
    personal fields were included.
``config/*.yaml``, ``config/mcp.json``
    The config files, with machine-specific paths tokenized
    (``aida.portability.paths_map``) and MCP ``env`` secrets **redacted**.
``skills/``, ``prompts/``, ``workflows/``
    Copied verbatim — pure content, portable as-is.
``paths.json``
    Every machine-specific path found, before and after tokenizing.
``secrets.json``
    The *names* of the secrets the imported config will need. Never values.
``README.txt``
    What the bundle is and what it deliberately omits.

**Three rules this module exists to enforce.**

1. *No secret value ever enters a bundle.* Provider secrets live in the OS
   keychain and are unexportable by construction, but ``mcp.json``'s ``env``
   block is plain text and routinely holds an API key — a real config on the
   author's machine had a Brave Search key sitting in it. Those are detected
   and redacted, and the key's *name* is reported so the user knows what to
   re-enter. See ``_redact_env``.
2. *Nothing personal leaves by default.* ``user_context``, the per-user
   context map, the active/known user names, and each workspace's private
   ``notes`` are excluded unless ``include_personal=True`` — which is for
   moving to your own second machine, not for sending to a colleague.
3. *Old bundles must always import.* ``manifest.json`` carries
   ``bundle_version`` and this reader accepts every version it has ever
   written, exactly the way ``aida.config.settings`` accepts every
   ``config.yaml`` it has ever written. A *newer* bundle is refused with a
   message naming the version, because silently ignoring sections we don't
   understand would import a setup that is quietly incomplete.

What a bundle deliberately does **not** contain: the conversation database,
artifacts, knowledge indexes, records, window geometry, or anything that
would carry one machine's screen layout onto another. Those are the
"full backup" scope in ``planning/portability.md`` §2, not this.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from aida import __version__ as _aida_version
from aida.config.paths import config_dir
from aida.config.settings import (
    AppConfig,
    KnowledgeBaseConfig,
    KnowledgeConfig,
    McpConfig,
    McpServerConfig,
    ProvidersConfig,
    SchedulesConfig,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
    save_app_config,
    save_knowledge_config,
    save_mcp_config,
    save_providers_config,
    save_schedules_config,
    save_workspaces_config,
)
from aida.portability.paths_map import PathInventoryEntry, PathMapper

#: Bumped only when the *layout* changes in a way a reader must know about.
#: Adding a new optional file does not need a bump — an older reader ignores
#: what it doesn't know, a newer reader defaults what isn't there.
BUNDLE_VERSION = 1

MANIFEST_NAME = "manifest.json"
PATHS_NAME = "paths.json"
SECRETS_NAME = "secrets.json"
README_NAME = "README.txt"

CONFLICT_POLICIES = ("skip", "overwrite", "rename")

# --------------------------------------------------------------------------
# config.yaml field classification
# --------------------------------------------------------------------------

#: Settings that describe *how the user works* and are worth carrying to
#: another machine. Path-valued entries here go through the tokenizer.
_APP_PORTABLE_FIELDS = (
    "log_level",
    "default_safety_mode",
    "allowed_folders",
    "records_dir",
    "scratch_dir",
    "max_agent_iterations",
    "max_context_tokens",
    "command_allowlist",
    "assistant_name",
    "scheduler_quiet_period_seconds",
    "scheduler_max_defer_seconds",
)

#: About a *person*, not a setup. Portable, but wrong to hand to a
#: colleague — included only with ``include_personal``.
_APP_PERSONAL_FIELDS = (
    "user_context",
    "user_contexts",
    "active_user",
    "known_users",
)

#: About one *screen*, and actively harmful to copy: a window position of
#: x=2563 is a second monitor the target machine does not have, and a font
#: size tuned for one display is wrong on another. Never exported.
_APP_MACHINE_FIELDS = (
    "window_width",
    "window_height",
    "window_x",
    "window_y",
    "font_size",
    "collapsed_panels",
    "splitter_sizes",
    "last_workspace_name",
    "last_profile_name",
)

_APP_PATH_FIELDS = ("records_dir", "scratch_dir")
_APP_PATH_LIST_FIELDS = ("allowed_folders",)

# --------------------------------------------------------------------------
# secret detection
# --------------------------------------------------------------------------

#: An ``mcp.json`` env key whose *name* says it holds a credential.
_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|passwd|credential|auth)"
)

#: A value that *looks* like a credential regardless of its key's name — a
#: long unbroken run of key-ish characters. Deliberately excludes anything
#: with a dot, colon or slash, so hostnames, addresses and paths
#: (``EPICS_CA_ADDR_LIST: 10.54.122.63:16661``) are never mistaken for keys.
_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-+=]{24,}$")

REDACTED_PLACEHOLDER = ""


def looks_secret(key: str, value: str) -> bool:
    """Whether one ``mcp.json`` env entry should be redacted from a bundle.

    Two independent tests, either sufficient: the key's name, and the
    value's shape. Belt and braces on purpose — this is the one check
    standing between "share my setup" and "email my API key", and a false
    positive costs the recipient one re-entry while a false negative leaks a
    credential.
    """
    if not value:
        return False
    return bool(_SECRET_KEY_RE.search(key)) or bool(_SECRET_VALUE_RE.match(value))


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass
class ExportResult:
    path: Path
    counts: dict[str, int] = field(default_factory=dict)
    #: ``(server_name, env_key)`` for every value redacted out of mcp.json.
    redacted_env: list[tuple[str, str]] = field(default_factory=list)
    #: Keychain refs the imported config will need values for.
    secret_refs: list[str] = field(default_factory=list)
    paths: list[PathInventoryEntry] = field(default_factory=list)
    include_personal: bool = False


@dataclass
class ImportReport:
    """What an import actually did, and what the user still has to do.

    The last three fields are the point of the whole feature: a bundle can
    carry configuration but it cannot carry conda environments, installed
    MCP servers, data folders, or keychain entries. Naming precisely what is
    missing on *this* machine is the deliverable.
    """

    added: dict[str, list[str]] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    overwritten: dict[str, list[str]] = field(default_factory=dict)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    app_settings_applied: bool = False
    #: ``(server_or_workspace, explanation)`` for an executable that could
    #: not be located here.
    unresolved_commands: list[tuple[str, str]] = field(default_factory=list)
    #: Folders the bundle references that don't exist on this machine.
    missing_folders: list[str] = field(default_factory=list)
    secret_refs: list[str] = field(default_factory=list)
    redacted_env: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    source: Path | None = None
    bundle_version: int = BUNDLE_VERSION
    created_at: str = ""

    def record(self, bucket: dict[str, list[str]], kind: str, name: str) -> None:
        bucket.setdefault(kind, []).append(name)

    @property
    def total_added(self) -> int:
        return sum(len(v) for v in self.added.values())


class BundleError(RuntimeError):
    """A bundle that cannot be read — missing manifest, unreadable zip, or a
    format version this AIDA does not understand."""


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def export_bundle(
    destination: str | Path,
    *,
    base_dir: Path | None = None,
    include_personal: bool = False,
) -> ExportResult:
    """Write a portable bundle of this install's configuration.

    ``base_dir`` overrides where the config files are read from (tests, and
    an eventual "export a different profile" use); it defaults to
    ``~/.aida``.
    """
    base = base_dir or config_dir()
    destination = Path(destination).expanduser()
    settings = load_settings(base)
    # Anchored on `base`, not on `app_dir()`: with an explicit `base_dir`
    # the two differ, and tokenizing against the wrong ~/.aida would emit
    # ${AIDA_HOME} for paths that have nothing to do with it.
    mapper = PathMapper(aida_home=base)
    result = ExportResult(path=destination, include_personal=include_personal)

    app_data = _export_app(settings.app, mapper, include_personal=include_personal)
    providers_data = settings.providers.to_dict()
    workspaces_data = _export_workspaces(
        settings.workspaces, mapper, include_personal=include_personal
    )
    mcp_data, redacted = _export_mcp(settings.mcp, mapper)
    knowledge_data = _export_knowledge(settings.knowledge, mapper)
    schedules_data = settings.schedules.to_dict()

    result.redacted_env = redacted
    result.secret_refs = _collect_secret_refs(settings.providers)
    result.paths = [entry for entry in mapper.inventory if entry.is_tokenized]

    skills_root = base / "skills"
    workflows_root = base / "workflows"

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                MANIFEST_NAME,
                _json(
                    {
                        "bundle_version": BUNDLE_VERSION,
                        "aida_version": _aida_version,
                        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "scope": "config",
                        "include_personal": include_personal,
                        "source_platform": os.name,
                    }
                ),
            )
            archive.writestr("config/app.yaml", _yaml(app_data))
            archive.writestr("config/providers.yaml", _yaml(providers_data))
            archive.writestr("config/workspaces.yaml", _yaml(workspaces_data))
            archive.writestr("config/mcp.json", _json(mcp_data))
            archive.writestr("config/knowledge.yaml", _yaml(knowledge_data))
            archive.writestr("config/schedules.yaml", _yaml(schedules_data))

            result.counts["skills"] = _add_tree(archive, skills_root, "skills", suffixes=(".md",))
            result.counts["workflows"] = _add_tree(
                archive, workflows_root, "workflows", suffixes=(".yaml",)
            )
            result.counts["prompts"] = _add_prompts(archive, base, settings.workspaces)
            result.counts["profiles"] = len(settings.providers.profiles)
            result.counts["embedding_profiles"] = len(settings.providers.embedding_profiles)
            result.counts["workspaces"] = len(settings.workspaces.workspaces)
            result.counts["mcp_servers"] = len(settings.mcp.servers)
            result.counts["knowledge_bases"] = len(settings.knowledge.knowledge_bases)
            result.counts["schedules"] = len(settings.schedules.schedules)

            archive.writestr(PATHS_NAME, _json([e.to_dict() for e in result.paths]))
            archive.writestr(
                SECRETS_NAME,
                _json(
                    {
                        "secret_refs": result.secret_refs,
                        "redacted_env": [
                            {"server": server, "key": key} for server, key in redacted
                        ],
                    }
                ),
            )
            archive.writestr(README_NAME, _readme(result))
        os.replace(tmp_name, destination)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return result


def _export_app(app: AppConfig, mapper: PathMapper, *, include_personal: bool) -> dict[str, Any]:
    full = app.to_dict()
    fields = list(_APP_PORTABLE_FIELDS)
    if include_personal:
        fields.extend(_APP_PERSONAL_FIELDS)
    data: dict[str, Any] = {"config_version": full.get("config_version")}
    for name in fields:
        value = full.get(name)
        if name in _APP_PATH_FIELDS:
            value = mapper.tokenize(value, f"app.{name}")
        elif name in _APP_PATH_LIST_FIELDS:
            value = mapper.tokenize_all(list(value or []), f"app.{name}")
        data[name] = value
    return data


def _export_workspaces(
    workspaces: WorkspacesConfig, mapper: PathMapper, *, include_personal: bool
) -> dict[str, Any]:
    data = workspaces.to_dict()
    for name, entry in data["workspaces"].items():
        where = f"workspaces.{name}"
        entry["source_folders"] = mapper.tokenize_all(
            list(entry.get("source_folders") or []), f"{where}.source_folders"
        )
        for path_field in ("target_folder", "templates_dir", "saved_scripts_dir"):
            entry[path_field] = mapper.tokenize(entry.get(path_field), f"{where}.{path_field}")
        entry["python_interpreter"] = mapper.tokenize(
            entry.get("python_interpreter"), f"{where}.python_interpreter"
        )
        if not include_personal:
            entry["notes"] = ""
    return data


def _export_mcp(mcp: McpConfig, mapper: PathMapper) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    data = mcp.to_dict()
    redacted: list[tuple[str, str]] = []
    for name, entry in data["mcpServers"].items():
        where = f"mcp.{name}"
        entry["command"] = mapper.tokenize(entry.get("command"), f"{where}.command")
        entry["args"] = [
            mapper.tokenize(arg, f"{where}.args[{i}]") or arg
            for i, arg in enumerate(entry.get("args") or [])
        ]
        entry["env"], server_redacted = _redact_env(name, entry.get("env") or {})
        redacted.extend(server_redacted)
    return data, redacted


def _redact_env(server: str, env: dict[str, str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Strip credential-looking values out of one server's ``env``, keeping
    the *keys* so the imported config still shows what needs filling in."""
    cleaned: dict[str, str] = {}
    redacted: list[tuple[str, str]] = []
    for key, value in env.items():
        text = "" if value is None else str(value)
        if looks_secret(key, text):
            cleaned[key] = REDACTED_PLACEHOLDER
            redacted.append((server, key))
        else:
            cleaned[key] = text
    return cleaned, redacted


def _export_knowledge(knowledge: KnowledgeConfig, mapper: PathMapper) -> dict[str, Any]:
    data = knowledge.to_dict()
    for name, entry in data["knowledge_bases"].items():
        entry["source_folders"] = mapper.tokenize_all(
            list(entry.get("source_folders") or []), f"knowledge.{name}.source_folders"
        )
    return data


def _collect_secret_refs(providers: ProvidersConfig) -> list[str]:
    refs = {p.secret_ref for p in providers.profiles.values() if p.secret_ref}
    refs |= {p.secret_ref for p in providers.embedding_profiles.values() if p.secret_ref}
    return sorted(refs)


def _add_tree(
    archive: zipfile.ZipFile, root: Path, prefix: str, *, suffixes: tuple[str, ...]
) -> int:
    """Copy a folder of plain content files into the bundle verbatim.

    Handles both skill layouts (``<name>.md`` and ``<name>/SKILL.md`` with
    its sibling files) by simply walking everything with a matching suffix.
    """
    if not root.is_dir():
        return 0
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path.name == "README.md" and path.parent == root:
            continue  # the bundled-skills README, not a skill
        archive.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
        count += 1
    return count


def _add_prompts(archive: zipfile.ZipFile, base: Path, workspaces: WorkspacesConfig) -> int:
    """Copy prompt files into the bundle.

    Two sources, unioned: everything under ``~/.aida/prompts/`` (PLAN.md's
    own illustrative layout, and where the GUI's "load from file" button
    points people), plus any *relative* ``system_prompt`` a workspace names
    that resolves to a real file under ``~/.aida``. The second is what makes
    the workspace actually work after import — a workspace whose prompt file
    stayed behind is one whose ``system_prompt`` silently becomes the
    literal string ``prompts/pyirena.md``
    (``aida.workspace.workspaces.resolve_system_prompt``).
    """
    wanted: dict[str, Path] = {}
    prompts_root = base / "prompts"
    if prompts_root.is_dir():
        for path in sorted(prompts_root.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".md", ".txt"):
                wanted[f"prompts/{path.relative_to(prompts_root).as_posix()}"] = path
    for workspace in workspaces.workspaces.values():
        value = workspace.system_prompt
        if not value:
            continue
        candidate = Path(value)
        if candidate.is_absolute() or value.startswith("~"):
            continue  # an absolute prompt file is a path problem, not a content one
        resolved = base / candidate
        if resolved.is_file():
            wanted[PurePosixPath(candidate.as_posix()).as_posix()] = resolved
    for arcname, path in sorted(wanted.items()):
        archive.write(path, arcname)
    return len(wanted)


def _readme(result: ExportResult) -> str:
    lines = [
        "AIDA setup bundle",
        "=================",
        "",
        "Import with:   aida config import <this-file>",
        "        or:    AIDA GUI -> File -> Import Setup...",
        "",
        "WHAT IS IN HERE",
        "  Provider profiles, workspaces, MCP server definitions, knowledge-base",
        "  definitions, schedules, workflows, skills and prompt files.",
        "  Machine-specific paths have been replaced with ${HOME}, ${AIDA_HOME}",
        "  and ${CONDA_ENV:<name>} tokens, expanded again on import.",
        "",
        "WHAT IS DELIBERATELY NOT IN HERE",
        "  - Secrets. No API key, token or password is in this file. The names of",
        "    the secrets you will need are listed in secrets.json; set each with",
        "    `aida config secret set <name>` after importing.",
        "  - Conversations, artifacts, knowledge indexes and transcripts.",
        "  - Window size/position, font size and other per-screen settings.",
    ]
    if not result.include_personal:
        lines.append("  - Personal context and private workspace notes.")
    lines += [
        "",
        "WHAT A BUNDLE CANNOT CARRY",
        "  conda environments and the MCP servers installed in them, Ollama models,",
        "  Node/npx packages, and your data folders. Install those separately; the",
        "  import report names the ones this bundle expects.",
        "",
    ]
    if result.redacted_env:
        lines.append("REDACTED FROM mcp.json (re-enter on the target machine)")
        lines += [f"  {server}: {key}" for server, key in result.redacted_env]
        lines.append("")
    if result.secret_refs:
        lines.append("SECRETS THE IMPORTED CONFIG WILL NEED")
        lines += [f"  aida config secret set {ref}" for ref in result.secret_refs]
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def read_manifest(source: str | Path) -> dict[str, Any]:
    """Read and validate a bundle's manifest without importing anything —
    used by the GUI to preview a file the user picked, and by ``import`` to
    fail early on a bundle it cannot read."""
    path = Path(source).expanduser()
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(MANIFEST_NAME)
    except FileNotFoundError as exc:
        raise BundleError(f"no such bundle: {path}") from exc
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleError(f"not a readable AIDA bundle: {path} ({exc})") from exc
    except KeyError as exc:
        raise BundleError(
            f"{path} is a zip file but not an AIDA bundle (no {MANIFEST_NAME})"
        ) from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BundleError(f"{path}: {MANIFEST_NAME} is not valid JSON ({exc})") from exc
    version = manifest.get("bundle_version", 1)
    if not isinstance(version, int) or version > BUNDLE_VERSION:
        raise BundleError(
            f"{path} was written by a newer AIDA (bundle_version {version}, this "
            f"AIDA understands up to {BUNDLE_VERSION}) — upgrade AIDA to import it"
        )
    return manifest


def import_bundle(
    source: str | Path,
    *,
    base_dir: Path | None = None,
    conflict: str = "skip",
    apply_app_settings: bool = False,
) -> ImportReport:
    """Merge a bundle into this install.

    ``conflict`` is one of ``"skip"`` (the default — never destructive),
    ``"overwrite"``, or ``"rename"`` (import as ``name (imported)``).
    ``config.yaml``'s own settings are applied only with
    ``apply_app_settings``: a colleague's ``default_safety_mode: relaxed``
    and iteration cap should not land on someone's install as a side effect
    of importing their workspaces.
    """
    if conflict not in CONFLICT_POLICIES:
        raise ValueError(f"conflict must be one of {CONFLICT_POLICIES}, got {conflict!r}")
    path = Path(source).expanduser()
    manifest = read_manifest(path)
    base = base_dir or config_dir()
    settings = load_settings(base)
    mapper = PathMapper(aida_home=base)
    report = ImportReport(
        source=path,
        bundle_version=int(manifest.get("bundle_version", 1)),
        created_at=str(manifest.get("created_at", "")),
    )

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())

        secrets_blob = _read_json_member(archive, SECRETS_NAME) or {}
        report.secret_refs = list(secrets_blob.get("secret_refs") or [])
        report.redacted_env = [
            (entry.get("server", ""), entry.get("key", ""))
            for entry in secrets_blob.get("redacted_env") or []
        ]

        # (file, worth_backing_up). A fresh install's config files exist but
        # are empty defaults written by `load_settings`; backing those up
        # would put five meaningless `.bak-` files in the report of the very
        # case this feature exists for — setting up a new machine.
        touched: list[Path] = []
        backup_worthy: set[Path] = set()

        def touch(path: Path, had_content: bool) -> None:
            touched.append(path)
            if had_content:
                backup_worthy.add(path)

        # -- providers ---------------------------------------------------
        providers_data = _read_yaml_member(archive, "config/providers.yaml")
        if providers_data:
            had = bool(settings.providers.profiles or settings.providers.embedding_profiles)
            incoming = ProvidersConfig.from_dict(providers_data)
            changed = _merge_named(
                incoming.profiles, settings.providers.profiles, "profile", conflict, report
            )
            changed |= _merge_named(
                incoming.embedding_profiles,
                settings.providers.embedding_profiles,
                "embedding profile",
                conflict,
                report,
            )
            if changed:
                touch(base / "providers.yaml", had)

        # -- mcp servers -------------------------------------------------
        mcp_data = _read_json_member(archive, "config/mcp.json")
        if mcp_data:
            had = bool(settings.mcp.servers)
            incoming_mcp = McpConfig.from_dict(mcp_data)
            resolved = {
                name: _resolve_server(name, server, mapper, report)
                for name, server in incoming_mcp.servers.items()
            }
            if _merge_named(resolved, settings.mcp.servers, "MCP server", conflict, report):
                touch(base / "mcp.json", had)

        # -- workspaces --------------------------------------------------
        workspaces_data = _read_yaml_member(archive, "config/workspaces.yaml")
        if workspaces_data:
            had = bool(settings.workspaces.workspaces)
            incoming_ws = WorkspacesConfig.from_dict(workspaces_data)
            resolved_ws = {
                name: _resolve_workspace(ws, mapper, report)
                for name, ws in incoming_ws.workspaces.items()
            }
            if _merge_named(
                resolved_ws, settings.workspaces.workspaces, "workspace", conflict, report
            ):
                touch(base / "workspaces.yaml", had)

        # -- knowledge bases ---------------------------------------------
        knowledge_data = _read_yaml_member(archive, "config/knowledge.yaml")
        if knowledge_data:
            had = bool(settings.knowledge.knowledge_bases)
            incoming_kb = KnowledgeConfig.from_dict(knowledge_data)
            resolved_kb = {
                name: _resolve_knowledge(kb, mapper, report)
                for name, kb in incoming_kb.knowledge_bases.items()
            }
            if _merge_named(
                resolved_kb,
                settings.knowledge.knowledge_bases,
                "knowledge base",
                conflict,
                report,
            ):
                touch(base / "knowledge.yaml", had)

        # -- schedules ----------------------------------------------------
        schedules_data = _read_yaml_member(archive, "config/schedules.yaml")
        if schedules_data:
            had = bool(settings.schedules.schedules)
            incoming_sched = SchedulesConfig.from_dict(schedules_data)
            if _merge_named(
                incoming_sched.schedules,
                settings.schedules.schedules,
                "schedule",
                conflict,
                report,
            ):
                touch(base / "schedules.yaml", had)

        # -- app settings --------------------------------------------------
        app_data = _read_yaml_member(archive, "config/app.yaml")
        if app_data and apply_app_settings:
            _apply_app_settings(settings.app, app_data, mapper, report)
            # Always worth a backup: config.yaml is never meaningfully empty,
            # and this is the one section that overwrites rather than merges.
            touch(base / "config.yaml", True)
            report.app_settings_applied = True
        elif app_data:
            report.warnings.append(
                "app settings (safety mode, allowed folders, iteration caps, records/scratch "
                "folders) are in the bundle but were not applied — re-run with --app-settings "
                "to apply them"
            )

        # -- back up before the first write --------------------------------
        for target in dict.fromkeys(p for p in touched if p in backup_worthy):
            backup = _backup(target)
            if backup is not None:
                report.backups.append(backup)

        if base / "providers.yaml" in touched:
            save_providers_config(settings.providers, base)
        if base / "mcp.json" in touched:
            save_mcp_config(settings.mcp, base)
        if base / "workspaces.yaml" in touched:
            save_workspaces_config(settings.workspaces, base)
        if base / "knowledge.yaml" in touched:
            save_knowledge_config(settings.knowledge, base)
        if base / "schedules.yaml" in touched:
            save_schedules_config(settings.schedules, base)
        if base / "config.yaml" in touched:
            save_app_config(settings.app, base)

        # -- content files ---------------------------------------------------
        _extract_tree(archive, names, "skills/", base / "skills", "skill", conflict, report)
        _extract_tree(
            archive, names, "workflows/", base / "workflows", "workflow", conflict, report
        )
        _extract_tree(archive, names, "prompts/", base / "prompts", "prompt", conflict, report)

    _validate_imported(base, report)
    return report


def _resolve_server(
    name: str, server: McpServerConfig, mapper: PathMapper, report: ImportReport
) -> McpServerConfig:
    command, problem = mapper.expand_executable(server.command)
    if problem:
        report.unresolved_commands.append((f"MCP server {name!r}", problem))
    server.command = command or server.command
    server.args = [mapper.expand(arg) or arg for arg in server.args]
    return server


def _resolve_workspace(
    workspace: WorkspaceConfig, mapper: PathMapper, report: ImportReport
) -> WorkspaceConfig:
    workspace.source_folders = mapper.expand_all(workspace.source_folders)
    workspace.target_folder = mapper.expand(workspace.target_folder)
    workspace.templates_dir = mapper.expand(workspace.templates_dir)
    workspace.saved_scripts_dir = mapper.expand(workspace.saved_scripts_dir)
    if workspace.python_interpreter:
        interpreter, problem = mapper.expand_executable(workspace.python_interpreter)
        if problem:
            report.unresolved_commands.append(
                (f"workspace {workspace.name!r} python_interpreter", problem)
            )
        workspace.python_interpreter = interpreter
    return workspace


def _resolve_knowledge(
    kb: KnowledgeBaseConfig, mapper: PathMapper, report: ImportReport
) -> KnowledgeBaseConfig:
    kb.source_folders = mapper.expand_all(kb.source_folders)
    return kb


def _apply_app_settings(
    app: AppConfig, data: dict[str, Any], mapper: PathMapper, report: ImportReport
) -> None:
    """Copy the bundle's portable ``config.yaml`` fields onto this install's
    ``AppConfig``, expanding paths. Machine fields are never in a bundle, so
    there is nothing to filter out here — but iterate the allowlist rather
    than the incoming keys anyway, so a hand-edited bundle cannot set a
    field this feature never intended to carry."""
    for name in (*_APP_PORTABLE_FIELDS, *_APP_PERSONAL_FIELDS):
        if name not in data:
            continue
        value = data[name]
        if name in _APP_PATH_FIELDS:
            value = mapper.expand(value)
        elif name in _APP_PATH_LIST_FIELDS:
            value = mapper.expand_all(list(value or []))
        if hasattr(app, name):
            setattr(app, name, value)


def _merge_named(
    incoming: dict[str, Any],
    target: dict[str, Any],
    kind: str,
    conflict: str,
    report: ImportReport,
) -> bool:
    """Merge one name-keyed config section, honouring the conflict policy.

    Returns whether anything changed — the caller uses that to decide
    whether the file is worth backing up and rewriting at all, so an import
    that collides with everything leaves the config files byte-identical
    and un-backed-up.
    """
    changed = False
    for name in sorted(incoming):
        value = incoming[name]
        if name not in target:
            target[name] = value
            report.record(report.added, kind, name)
            changed = True
            continue
        if conflict == "skip":
            report.record(report.skipped, kind, name)
        elif conflict == "overwrite":
            target[name] = value
            report.record(report.overwritten, kind, name)
            changed = True
        else:  # rename
            new_name = _unique_name(target, name)
            _rename_in_place(value, new_name)
            target[new_name] = value
            report.record(report.added, kind, new_name)
            report.renamed.append((name, new_name))
            changed = True
    return changed


def _unique_name(existing: dict[str, Any], name: str) -> str:
    candidate = f"{name} (imported)"
    counter = 2
    while candidate in existing:
        candidate = f"{name} (imported {counter})"
        counter += 1
    return candidate


def _rename_in_place(value: Any, new_name: str) -> None:
    """Every config dataclass in this section keeps its own key as a
    ``name`` field; a renamed import has to carry the new key or the next
    save writes a mapping whose key and ``name`` disagree."""
    if hasattr(value, "name"):
        value.name = new_name


def _extract_tree(
    archive: zipfile.ZipFile,
    names: set[str],
    prefix: str,
    target_root: Path,
    kind: str,
    conflict: str,
    report: ImportReport,
) -> None:
    """Copy one content folder out of the bundle.

    Member names are validated rather than trusted: a zip is an untrusted
    input and ``../`` in an entry name is the classic way to write outside
    the directory you meant to extract into. Same reasoning as
    ``aida.artifacts.store._safe_filename``, which hardens the equivalent
    path for MCP-supplied artifact filenames.
    """
    for member in sorted(n for n in names if n.startswith(prefix) and not n.endswith("/")):
        relative = member[len(prefix) :]
        if not _safe_relative(relative):
            report.warnings.append(f"ignored unsafe entry in bundle: {member}")
            continue
        destination = target_root / Path(relative)
        label = PurePosixPath(relative).stem if "/" not in relative else relative
        if destination.exists():
            if conflict == "skip":
                report.record(report.skipped, kind, label)
                continue
            if conflict == "rename":
                destination = _unique_file(destination)
                report.renamed.append((label, destination.stem))
                report.record(report.added, kind, destination.stem)
            else:
                report.record(report.overwritten, kind, label)
        else:
            report.record(report.added, kind, label)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _safe_relative(relative: str) -> bool:
    if not relative or relative.startswith("/") or relative.startswith("\\"):
        return False
    parts = PurePosixPath(relative).parts
    return bool(parts) and not any(part in ("..", ".") for part in parts)


def _unique_file(path: Path) -> Path:
    counter = 1
    candidate = path.with_name(f"{path.stem} (imported){path.suffix}")
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f"{path.stem} (imported {counter}){path.suffix}")
    return candidate


def _backup(path: Path) -> Path | None:
    """Timestamped copy of a config file about to be rewritten, following
    the ``mcp.json.bak-<stamp>`` convention already used in ``~/.aida``."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while backup.exists():
        counter += 1
        backup = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
    shutil.copy2(path, backup)
    return backup


def _validate_imported(base: Path, report: ImportReport) -> None:
    """Run the existing validators over the merged config.

    This is the honest half of an import: a bundle can carry a workspace but
    not the conda env its MCP server lives in, nor the data folder it reads.
    Rather than invent new checks, reuse the ones the app already runs —
    ``validate_workspace`` already reports unknown profiles, empty MCP
    groups, missing skill files and unreachable folders.
    """
    # Imported here, not at module scope: aida.workspace's package __init__
    # reaches aida.mcp, and importing it eagerly would drag the MCP layer
    # into every `aida config export`.
    from aida.workspace.workspaces import validate_workspace

    settings = load_settings(base)
    imported = set(report.added.get("workspace", []))
    imported |= set(report.overwritten.get("workspace", []))
    seen: set[str] = set()
    for name in sorted(imported):
        workspace = settings.workspaces.workspaces.get(name)
        if workspace is None:
            continue
        validation = validate_workspace(settings, workspace)
        if not validation.ok:
            report.warnings.append(f"workspace {name!r}: {validation.detail}")
        for warning in validation.warnings:
            report.warnings.append(f"workspace {name!r}: {warning}")
        for folder in workspace.source_folders:
            if folder not in seen and not Path(folder).expanduser().exists():
                seen.add(folder)
                report.missing_folders.append(folder)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def _yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False)


def _read_yaml_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        raw = archive.read(name)
    except KeyError:
        return {}
    return yaml.safe_load(raw.decode("utf-8")) or {}


def _read_json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        raw = archive.read(name)
    except KeyError:
        return {}
    try:
        return json.loads(raw.decode("utf-8")) or {}
    except json.JSONDecodeError:
        return {}


__all__ = [
    "BUNDLE_VERSION",
    "CONFLICT_POLICIES",
    "BundleError",
    "ExportResult",
    "ImportReport",
    "export_bundle",
    "import_bundle",
    "looks_secret",
    "read_manifest",
]
