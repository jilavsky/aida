"""Load, validate, and default AIDA's YAML/JSON config files.

Pattern (pyIrena rule, PLAN.md §10.3 / Phase 1 tasks): **old configs must
always load.** Every field has a default; a config file that predates a new
field simply gets that field's default rather than failing to load. Config
schema versioning (``config_version``) is present from day one so future
migrations have something to key off of.

No secret value is ever read from or written to these files — see
``aida.config.secrets``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aida.config.paths import config_dir

CURRENT_CONFIG_VERSION = 1


# --------------------------------------------------------------------------
# config.yaml
# --------------------------------------------------------------------------


@dataclass
class AppConfig:
    """General settings: paths, safety default, UI prefs, log level.

    ``window_*``/``font_size`` (Phase 5): the Qt GUI's persisted window
    state — ``None`` for any ``window_*`` field means "let Qt pick" (first
    run, or a monitor arrangement that no longer fits), so the GUI never
    fails to launch on a stale/foreign geometry.
    """

    config_version: int = CURRENT_CONFIG_VERSION
    records_dir: str | None = None  # None -> aida.config.paths.default_records_dir()
    log_level: str = "INFO"
    default_safety_mode: str = "confirm"  # "relaxed" | "confirm"
    # Phase 6: folders implicitly allowed for every workspace/session, on
    # top of that workspace's own source_folders/target_folder — e.g. a
    # shared reference library the user wants every workspace to be able
    # to read without configuring it per-workspace. Empty by default (no
    # implicit access beyond what each workspace already grants itself).
    # Editable via this config file for v1, same as everything else in
    # Settings dialog v1 that doesn't have its own editor yet.
    allowed_folders: list[str] = field(default_factory=list)
    theme: str = "system"
    window_width: int | None = None
    window_height: int | None = None
    window_x: int | None = None
    window_y: int | None = None
    font_size: int = 11
    # GUI session-restore (bug report: "app does not seem to open with last
    # set of settings"): the most recently active workspace/profile, updated
    # every time a session actually starts successfully (MainWindow's
    # _on_session_ready). aida-gui falls back to these when launched with no
    # --workspace/--profile flag, so the app reopens where the user left off
    # instead of landing on "No profile given". Either can be None (no
    # workspace was active, or no session has ever started yet).
    last_workspace_name: str | None = None
    last_profile_name: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in (data or {}).items() if k in known}
        if "allowed_folders" in filtered:
            filtered["allowed_folders"] = list(filtered["allowed_folders"] or [])
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "records_dir": self.records_dir,
            "log_level": self.log_level,
            "default_safety_mode": self.default_safety_mode,
            "allowed_folders": self.allowed_folders,
            "theme": self.theme,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "window_x": self.window_x,
            "window_y": self.window_y,
            "font_size": self.font_size,
            "last_workspace_name": self.last_workspace_name,
            "last_profile_name": self.last_profile_name,
        }


# --------------------------------------------------------------------------
# providers.yaml
# --------------------------------------------------------------------------


@dataclass
class ProviderProfile:
    """A named provider profile. NO secrets inline — secret refs only."""

    name: str
    kind: str = "openai_compat"  # "openai_compat" | "anthropic"
    base_url: str | None = None
    model: str = ""
    secret_ref: str | None = None  # key into aida.config.secrets, not a value
    capability_notes: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ProviderProfile:
        return cls(
            name=name,
            kind=data.get("kind", "openai_compat"),
            base_url=data.get("base_url"),
            model=data.get("model", ""),
            secret_ref=data.get("secret_ref"),
            capability_notes=data.get("capability_notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base_url": self.base_url,
            "model": self.model,
            "secret_ref": self.secret_ref,
            "capability_notes": self.capability_notes,
        }


@dataclass
class ProvidersConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    profiles: dict[str, ProviderProfile] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvidersConfig:
        data = data or {}
        profiles = {
            name: ProviderProfile.from_dict(name, pdata)
            for name, pdata in (data.get("profiles") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            profiles=profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
        }


# --------------------------------------------------------------------------
# workspaces.yaml
# --------------------------------------------------------------------------


@dataclass
class WorkspaceConfig:
    name: str
    profile: str | None = None
    source_folders: list[str] = field(default_factory=list)
    target_folder: str | None = None
    sidecar_folder_name: str = "figures"
    mcp_group: str = "none"
    skills: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    safety: str = "confirm"  # "relaxed" | "confirm"

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> WorkspaceConfig:
        return cls(
            name=name,
            profile=data.get("profile"),
            source_folders=list(data.get("source_folders", [])),
            target_folder=data.get("target_folder"),
            sidecar_folder_name=data.get("sidecar_folder_name", "figures"),
            mcp_group=data.get("mcp_group", "none"),
            skills=list(data.get("skills", [])),
            system_prompt=data.get("system_prompt"),
            safety=data.get("safety", "confirm"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "source_folders": self.source_folders,
            "target_folder": self.target_folder,
            "sidecar_folder_name": self.sidecar_folder_name,
            "mcp_group": self.mcp_group,
            "skills": self.skills,
            "system_prompt": self.system_prompt,
            "safety": self.safety,
        }


@dataclass
class WorkspacesConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    workspaces: dict[str, WorkspaceConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspacesConfig:
        data = data or {}
        workspaces = {
            name: WorkspaceConfig.from_dict(name, wdata)
            for name, wdata in (data.get("workspaces") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            workspaces=workspaces,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "workspaces": {name: w.to_dict() for name, w in self.workspaces.items()},
        }


# --------------------------------------------------------------------------
# mcp.json (standard-style + aida extras)
# --------------------------------------------------------------------------


@dataclass
class McpServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> McpServerConfig:
        return cls(
            name=name,
            command=data.get("command", ""),
            args=list(data.get("args", [])),
            env=dict(data.get("env", {})),
            groups=list(data.get("groups", [])),
            skills=list(data.get("skills", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "groups": self.groups,
            "skills": self.skills,
        }


@dataclass
class McpConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    servers: dict[str, McpServerConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> McpConfig:
        data = data or {}
        servers = {
            name: McpServerConfig.from_dict(name, sdata)
            for name, sdata in (data.get("mcpServers") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            servers=servers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "mcpServers": {name: s.to_dict() for name, s in self.servers.items()},
        }


# --------------------------------------------------------------------------
# Loading / saving
# --------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh) or {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def load_app_config(base_dir: Path | None = None) -> AppConfig:
    path = (base_dir or config_dir()) / "config.yaml"
    return AppConfig.from_dict(_read_yaml(path))


def save_app_config(cfg: AppConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "config.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_providers_config(base_dir: Path | None = None) -> ProvidersConfig:
    path = (base_dir or config_dir()) / "providers.yaml"
    return ProvidersConfig.from_dict(_read_yaml(path))


def save_providers_config(cfg: ProvidersConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "providers.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_workspaces_config(base_dir: Path | None = None) -> WorkspacesConfig:
    path = (base_dir or config_dir()) / "workspaces.yaml"
    return WorkspacesConfig.from_dict(_read_yaml(path))


def save_workspaces_config(cfg: WorkspacesConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "workspaces.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_mcp_config(base_dir: Path | None = None) -> McpConfig:
    path = (base_dir or config_dir()) / "mcp.json"
    return McpConfig.from_dict(_read_json(path))


def save_mcp_config(cfg: McpConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "mcp.json"
    _write_json(path, cfg.to_dict())
    return path


@dataclass
class Settings:
    """Bundle of everything loaded from ``~/.aida`` for one process."""

    app: AppConfig
    providers: ProvidersConfig
    workspaces: WorkspacesConfig
    mcp: McpConfig


def load_settings(base_dir: Path | None = None) -> Settings:
    """Load all four config files, defaulting anything missing.

    Also ensures the files exist on disk on first run (writing out the
    defaults), per the Phase 1 acceptance criterion "first run creates
    ~/.aida with valid default configs".
    """
    base = base_dir or config_dir()
    app = load_app_config(base)
    providers = load_providers_config(base)
    workspaces = load_workspaces_config(base)
    mcp = load_mcp_config(base)

    if not (base / "config.yaml").exists():
        save_app_config(app, base)
    if not (base / "providers.yaml").exists():
        save_providers_config(providers, base)
    if not (base / "workspaces.yaml").exists():
        save_workspaces_config(workspaces, base)
    if not (base / "mcp.json").exists():
        save_mcp_config(mcp, base)

    return Settings(app=app, providers=providers, workspaces=workspaces, mcp=mcp)
