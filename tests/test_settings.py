from __future__ import annotations

import json
from pathlib import Path

import yaml

from aida.config.settings import (
    AppConfig,
    KnowledgeConfig,
    McpConfig,
    ProvidersConfig,
    WorkspacesConfig,
    load_app_config,
    load_mcp_config,
    load_providers_config,
    load_settings,
    load_workspaces_config,
    save_app_config,
    save_mcp_config,
    save_providers_config,
    save_workspaces_config,
)


def test_load_settings_first_run_writes_defaults(aida_home: Path):
    settings = load_settings()

    assert (aida_home / "config.yaml").exists()
    assert (aida_home / "providers.yaml").exists()
    assert (aida_home / "workspaces.yaml").exists()
    assert (aida_home / "mcp.json").exists()
    assert (aida_home / "knowledge.yaml").exists()

    assert settings.app.config_version == 1
    assert settings.providers.profiles == {}
    assert settings.workspaces.workspaces == {}
    assert settings.mcp.servers == {}
    assert settings.knowledge.knowledge_bases == {}


def test_app_config_roundtrip(aida_home: Path):
    cfg = AppConfig(log_level="DEBUG", default_safety_mode="relaxed")
    path = save_app_config(cfg, aida_home)
    assert path.exists()

    loaded = load_app_config(aida_home)
    assert loaded.log_level == "DEBUG"
    assert loaded.default_safety_mode == "relaxed"
    assert loaded.config_version == 1


def test_app_config_allowed_folders_roundtrip(aida_home: Path):
    cfg = AppConfig(allowed_folders=["/tmp/shared-a", "/tmp/shared-b"])
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.allowed_folders == ["/tmp/shared-a", "/tmp/shared-b"]


def test_app_config_last_workspace_and_profile_roundtrip(aida_home: Path):
    cfg = AppConfig(last_workspace_name="use-pyirena", last_profile_name="local-lmstudio")
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.last_workspace_name == "use-pyirena"
    assert loaded.last_profile_name == "local-lmstudio"


def test_app_config_last_workspace_defaults_to_none(aida_home: Path):
    cfg = AppConfig()
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.last_workspace_name is None
    assert loaded.last_profile_name is None


def test_app_config_max_agent_iterations_defaults_to_ten(aida_home: Path):
    cfg = AppConfig()
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.max_agent_iterations == 10


def test_app_config_max_agent_iterations_roundtrip(aida_home: Path):
    cfg = AppConfig(max_agent_iterations=500)
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.max_agent_iterations == 500


def test_old_config_missing_fields_gets_defaults(aida_home: Path):
    """pyIrena rule: old configs must always load."""
    partial = {"config_version": 1}  # no log_level, no records_dir, ...
    path = aida_home / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(partial, fh)

    loaded = load_app_config(aida_home)
    assert loaded.log_level == "INFO"  # default, not a crash


def test_unknown_future_field_ignored(aida_home: Path):
    """A config written by a *future* AIDA version with an extra field must
    still load under this version rather than raising."""
    data = {"config_version": 1, "log_level": "WARNING", "some_future_field": 42}
    path = aida_home / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(data, fh)

    loaded = load_app_config(aida_home)
    assert loaded.log_level == "WARNING"


def test_providers_config_roundtrip(aida_home: Path):
    from aida.config.settings import ProviderProfile

    cfg = ProvidersConfig(
        profiles={
            "argo-claude": ProviderProfile(
                name="argo-claude",
                kind="anthropic",
                base_url="https://apps.inside.anl.gov/argoapi/",
                model="claude-sonnet",
                secret_ref="argo-claude",
            )
        }
    )
    save_providers_config(cfg, aida_home)
    loaded = load_providers_config(aida_home)
    assert "argo-claude" in loaded.profiles
    assert loaded.profiles["argo-claude"].kind == "anthropic"

    # No secret value anywhere in the file on disk.
    raw = (aida_home / "providers.yaml").read_text()
    assert "secret_ref" in raw
    assert "sk-" not in raw


def test_provider_profile_sampling_and_cost_fields_roundtrip(aida_home: Path):
    """B2: max_tokens/temperature/usd_per_m_input/usd_per_m_output/
    supports_vision round-trip through providers.yaml like every other
    field."""
    from aida.config.settings import ProviderProfile

    cfg = ProvidersConfig(
        profiles={
            "argo-claude": ProviderProfile(
                name="argo-claude",
                kind="anthropic",
                model="claude-sonnet",
                max_tokens=8192,
                temperature=0.3,
                usd_per_m_input=3.0,
                usd_per_m_output=15.0,
                supports_vision=True,
            )
        }
    )
    save_providers_config(cfg, aida_home)
    loaded = load_providers_config(aida_home).profiles["argo-claude"]
    assert loaded.max_tokens == 8192
    assert loaded.temperature == 0.3
    assert loaded.usd_per_m_input == 3.0
    assert loaded.usd_per_m_output == 15.0
    assert loaded.supports_vision is True


def test_provider_profile_sampling_fields_default_to_none():
    """A profile that never set these must behave exactly as before B2 —
    None means "use CompletionSettings' own defaults", not zero."""
    from aida.config.settings import ProviderProfile

    profile = ProviderProfile.from_dict("local-ollama", {"kind": "openai_compat", "model": "qwen"})
    assert profile.max_tokens is None
    assert profile.temperature is None
    assert profile.usd_per_m_input is None
    assert profile.usd_per_m_output is None
    assert profile.supports_vision is False


def test_provider_profile_rejects_a_badly_typed_sampling_field():
    """A hand-quoted max_tokens: "lots" must not crash config loading — it
    should fall back to None (built-in default) with a warning, same as
    every other coercion guard in this module."""
    from aida.config.settings import ProviderProfile

    profile = ProviderProfile.from_dict(
        "argo-claude", {"kind": "anthropic", "model": "claude-sonnet", "max_tokens": "lots", "temperature": {}}
    )
    assert profile.max_tokens is None
    assert profile.temperature is None


def test_provider_profile_context_window_roundtrip(aida_home: Path):
    """PLAN.md §1.3: context_window is the model's TOTAL window, a
    separate field from max_tokens (the output cap) — round-trips through
    providers.yaml like every other optional numeric field."""
    from aida.config.settings import ProviderProfile

    cfg = ProvidersConfig(
        profiles={
            "local-qwen": ProviderProfile(
                name="local-qwen", kind="openai_compat", model="qwen2.5", context_window=128_000
            )
        }
    )
    save_providers_config(cfg, aida_home)
    loaded = load_providers_config(aida_home).profiles["local-qwen"]
    assert loaded.context_window == 128_000


def test_provider_profile_context_window_defaults_to_none():
    """None means "fall back to AppConfig.max_context_tokens" — a profile
    that never sets this must behave exactly as before this field existed."""
    from aida.config.settings import ProviderProfile

    profile = ProviderProfile.from_dict("local-ollama", {"kind": "openai_compat", "model": "qwen"})
    assert profile.context_window is None


def test_provider_profile_rejects_a_badly_typed_context_window():
    """A hand-quoted context_window: "huge" must not crash config
    loading — falls back to None (use AppConfig.max_context_tokens) with a
    warning, same coercion guard every other optional numeric field uses."""
    from aida.config.settings import ProviderProfile

    profile = ProviderProfile.from_dict(
        "argo-claude", {"kind": "anthropic", "model": "claude-sonnet", "context_window": "huge"}
    )
    assert profile.context_window is None


def test_workspaces_config_roundtrip(aida_home: Path):
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={
            "use-pyirena": WorkspaceConfig(
                name="use-pyirena",
                profile="argo-claude",
                source_folders=["/data/USAXS"],
                target_folder="~/Documents/Aida/analysis",
                mcp_group="pyirena-analysis",
                safety="relaxed",
            )
        }
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home)
    ws = loaded.workspaces["use-pyirena"]
    assert ws.profile == "argo-claude"
    assert ws.safety == "relaxed"
    assert ws.source_folders == ["/data/USAXS"]


def test_workspace_config_wraps_a_hand_edited_scalar_list_field():
    """A hand-edited ``source_folders: /some/path`` (scalar, not a YAML
    list) used to be fed straight to ``list(...)``, silently exploding into
    a list of single characters (['/', 's', 'o', ...]) — a nonsense
    allowed-roots list with no warning at all. It should instead be treated
    as a one-item list, same as every other list-typed field guarded this
    way."""
    from aida.config.settings import WorkspaceConfig

    ws = WorkspaceConfig.from_dict(
        "use-pyirena",
        {
            "source_folders": "/data/USAXS",
            "skills": "pyirena-usage",
            "knowledge_bases": "usaxs-docs",
            "command_allowlist": "ls",
        },
    )
    assert ws.source_folders == ["/data/USAXS"]
    assert ws.skills == ["pyirena-usage"]
    assert ws.knowledge_bases == ["usaxs-docs"]
    assert ws.command_allowlist == ["ls"]


def test_workspace_config_falls_back_to_default_for_a_non_list_non_string_field():
    from aida.config.settings import WorkspaceConfig

    ws = WorkspaceConfig.from_dict("use-pyirena", {"source_folders": {"not": "a list"}})
    assert ws.source_folders == []


def test_mcp_server_config_wraps_a_hand_edited_scalar_args():
    from aida.config.settings import McpServerConfig

    server = McpServerConfig.from_dict("pyirena-mcp", {"args": "--flag"})
    assert server.args == ["--flag"]


def test_knowledge_base_config_wraps_a_hand_edited_scalar_source_folders():
    from aida.config.settings import KnowledgeBaseConfig

    kb = KnowledgeBaseConfig.from_dict("usaxs-docs", {"source_folders": "/data/usaxs-instructions"})
    assert kb.source_folders == ["/data/usaxs-instructions"]


def test_mcp_config_roundtrip(aida_home: Path):
    from aida.config.settings import McpServerConfig

    cfg = McpConfig(
        servers={
            "pyirena-mcp": McpServerConfig(
                name="pyirena-mcp",
                command="/opt/conda/envs/pyirena/bin/pyirena-mcp",
                groups=["pyirena-analysis"],
                skills=["pyirena-usage"],
            )
        }
    )
    save_mcp_config(cfg, aida_home)
    loaded = load_mcp_config(aida_home)
    assert "pyirena-mcp" in loaded.servers
    assert loaded.servers["pyirena-mcp"].groups == ["pyirena-analysis"]


def test_existing_claude_desktop_mcp_json_loads_unmodified(aida_home: Path):
    """Phase 3 acceptance criterion: "An existing Claude Desktop mcp.json
    entry for pyirena works unmodified." Claude Desktop's config has no
    ``groups``/``skills``/``config_version`` keys (those are AIDA-only
    extras) and may carry extra keys AIDA doesn't know about (e.g.
    ``disabled``) — none of that should prevent loading, and AIDA's extra
    fields should simply default rather than erroring."""
    raw_claude_desktop_config = {
        "mcpServers": {
            "pyirena": {
                "command": "/opt/conda/envs/pyirena/bin/pyirena-mcp",
                "args": ["--stdio"],
                "env": {"PYIRENA_DATA_ROOT": "/data/USAXS"},
                "disabled": False,
            }
        }
    }
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "mcp.json").write_text(json.dumps(raw_claude_desktop_config), encoding="utf-8")

    loaded = load_mcp_config(aida_home)

    server = loaded.servers["pyirena"]
    assert server.command == "/opt/conda/envs/pyirena/bin/pyirena-mcp"
    assert server.args == ["--stdio"]
    assert server.env == {"PYIRENA_DATA_ROOT": "/data/USAXS"}
    # AIDA-only extras simply default — no error, no data loss.
    assert server.groups == []
    assert server.skills == []


def test_unknown_mcp_server_keys_survive_a_save_and_reload(aida_home: Path):
    """Regression (Phase 7): loading an unknown key like ``disabled`` never
    errored (see the test above), but nothing previously proved it
    *survived a save* — before ``McpServerConfig.extra`` existed, the very
    first GUI/CLI edit that re-saved mcp.json silently deleted every key
    AIDA didn't model. A real Claude-Desktop export carries exactly these:
    ``disabled``, ``autoApprove``, ``type``, ``cwd``."""

    raw = {
        "mcpServers": {
            "pyirena": {
                "command": "/opt/pyirena-mcp",
                "disabled": False,
                "autoApprove": ["plot_saxs"],
                "type": "stdio",
                "cwd": "/data",
            }
        }
    }
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "mcp.json").write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_mcp_config(aida_home)
    assert loaded.servers["pyirena"].extra == {
        "disabled": False,
        "autoApprove": ["plot_saxs"],
        "type": "stdio",
        "cwd": "/data",
    }

    # A round-trip through AIDA (e.g. an edit made from the GUI/CLI that
    # re-saves the whole file) must not lose any of those unknown keys.
    save_mcp_config(loaded, aida_home)
    reloaded = load_mcp_config(aida_home)
    assert reloaded.servers["pyirena"].extra == loaded.servers["pyirena"].extra
    assert reloaded.servers["pyirena"].command == "/opt/pyirena-mcp"


def test_disabled_and_confirm_tools_roundtrip(aida_home: Path):
    from aida.config.settings import McpServerConfig

    cfg = McpConfig(
        servers={
            "bait": McpServerConfig(
                name="bait",
                command="/opt/bait-mcp",
                disabled_tools=["dangerous_tool"],
                confirm_tools=["move_stage"],
            )
        }
    )
    save_mcp_config(cfg, aida_home)
    loaded = load_mcp_config(aida_home)
    assert loaded.servers["bait"].disabled_tools == ["dangerous_tool"]
    assert loaded.servers["bait"].confirm_tools == ["move_stage"]


def test_a_real_edit_to_a_known_field_wins_over_stale_extra_data(aida_home: Path):
    """If ``extra`` ever contained a value for a key AIDA *does* model
    (shouldn't normally happen since ``from_dict`` excludes known keys from
    ``extra`` in the first place, but ``to_dict`` must be robust either
    way), the dataclass's own field always wins on save."""
    from aida.config.settings import McpServerConfig

    server = McpServerConfig(name="x", command="/real/command", extra={"command": "/stale/command"})
    assert server.to_dict()["command"] == "/real/command"


# --- Phase 8: embedding profiles / knowledge bases --------------------------


def test_embedding_profile_roundtrip(aida_home: Path):
    from aida.config.settings import EmbeddingProfile

    cfg = ProvidersConfig(
        embedding_profiles={
            "argo-embed": EmbeddingProfile(
                name="argo-embed",
                base_url="https://apps.inside.anl.gov/argoapi/",
                model="text-embedding-3-small",
                secret_ref="argo-claude",
            )
        }
    )
    save_providers_config(cfg, aida_home)
    loaded = load_providers_config(aida_home)
    profile = loaded.embedding_profiles["argo-embed"]
    assert profile.model == "text-embedding-3-small"
    assert profile.secret_ref == "argo-claude"

    # No secret value anywhere in the file on disk (same guarantee as
    # ProviderProfile).
    raw = (aida_home / "providers.yaml").read_text()
    assert "secret_ref" in raw
    assert "sk-" not in raw


def test_old_providers_yaml_without_embedding_profiles_loads_fine(aida_home: Path):
    """Old configs must always load (pyIrena rule) — a providers.yaml
    written before Phase 8 has no `embedding_profiles` key at all."""
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "providers.yaml").write_text("config_version: 1\nprofiles: {}\n", encoding="utf-8")
    loaded = load_providers_config(aida_home)
    assert loaded.embedding_profiles == {}


def test_knowledge_config_roundtrip(aida_home: Path):
    from aida.config.settings import (
        KnowledgeBaseConfig,
        load_knowledge_config,
        save_knowledge_config,
    )

    cfg = KnowledgeConfig(
        knowledge_bases={
            "usaxs-docs": KnowledgeBaseConfig(
                name="usaxs-docs",
                source_folders=["/data/usaxs-instructions"],
                embedding_profile="argo-embed",
                chunk_size=800,
                chunk_overlap=100,
            )
        }
    )
    save_knowledge_config(cfg, aida_home)
    loaded = load_knowledge_config(aida_home)
    kb = loaded.knowledge_bases["usaxs-docs"]
    assert kb.source_folders == ["/data/usaxs-instructions"]
    assert kb.embedding_profile == "argo-embed"
    assert kb.chunk_size == 800
    assert kb.chunk_overlap == 100


def test_knowledge_config_defaults_when_file_missing(aida_home: Path):
    from aida.config.settings import load_knowledge_config

    loaded = load_knowledge_config(aida_home)
    assert loaded.knowledge_bases == {}


def test_workspace_knowledge_bases_roundtrip(aida_home: Path):
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={
            "beamline-help": WorkspaceConfig(name="beamline-help", knowledge_bases=["usaxs-docs", "pyirena-docs"])
        }
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home)
    assert loaded.workspaces["beamline-help"].knowledge_bases == ["usaxs-docs", "pyirena-docs"]


def test_settings_bundle_includes_knowledge(aida_home: Path):
    settings = load_settings()
    assert settings.knowledge is not None
    assert (aida_home / "knowledge.yaml").exists()


def test_workspace_coding_fields_roundtrip(aida_home: Path):
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={
            "instrument-ops": WorkspaceConfig(
                name="instrument-ops",
                command_allowlist=["git status", "git log *"],
                python_interpreter="/opt/miniconda3/envs/aievaluator/bin/python",
                scripting_enabled=False,
            )
        }
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home).workspaces["instrument-ops"]
    assert loaded.command_allowlist == ["git status", "git log *"]
    assert loaded.python_interpreter == "/opt/miniconda3/envs/aievaluator/bin/python"
    assert loaded.scripting_enabled is False


def test_workspace_coding_fields_default(aida_home: Path):
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(workspaces={"plain": WorkspaceConfig(name="plain")})
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home).workspaces["plain"]
    assert loaded.command_allowlist == []
    assert loaded.python_interpreter is None
    assert loaded.scripting_enabled is True
    assert loaded.templates_dir is None
    assert loaded.saved_scripts_dir is None
    assert loaded.script_timeout_seconds == 30.0


def test_workspace_script_timeout_seconds_roundtrip(aida_home: Path):
    """B5: WorkspaceConfig.script_timeout_seconds — the per-workspace
    ceiling run_python_script/run_command are capped by."""
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={"long-runs": WorkspaceConfig(name="long-runs", script_timeout_seconds=300.0)}
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home).workspaces["long-runs"]
    assert loaded.script_timeout_seconds == 300.0


def test_workspace_templates_and_saved_scripts_dir_roundtrip(aida_home: Path):
    from aida.config.settings import WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={
            "instrument-ops": WorkspaceConfig(
                name="instrument-ops",
                templates_dir="/data/bits-usaxs/templates",
                saved_scripts_dir="/data/scripts",
            )
        }
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home).workspaces["instrument-ops"]
    assert loaded.templates_dir == "/data/bits-usaxs/templates"
    assert loaded.saved_scripts_dir == "/data/scripts"


def test_resolved_saved_scripts_dir_uses_explicit_override():
    from aida.config.settings import WorkspaceConfig

    ws = WorkspaceConfig(name="ws", target_folder="/data/target", saved_scripts_dir="/data/scripts")
    assert ws.resolved_saved_scripts_dir() == "/data/scripts"


def test_resolved_saved_scripts_dir_defaults_under_target_folder():
    from aida.config.settings import WorkspaceConfig

    ws = WorkspaceConfig(name="ws", target_folder="/data/target")
    assert ws.resolved_saved_scripts_dir() == str(Path("/data/target") / "saved_scripts")


def test_resolved_saved_scripts_dir_none_with_no_target_folder():
    from aida.config.settings import WorkspaceConfig

    ws = WorkspaceConfig(name="ws")
    assert ws.resolved_saved_scripts_dir() is None


def test_app_config_command_allowlist_roundtrip(aida_home: Path):
    cfg = AppConfig(command_allowlist=["ls", "git status"])
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.command_allowlist == ["ls", "git status"]


def test_app_config_command_allowlist_defaults_to_empty(aida_home: Path):
    cfg = AppConfig()
    save_app_config(cfg, aida_home)

    loaded = load_app_config(aida_home)
    assert loaded.command_allowlist == []


# --- from_dict type coercion ----------------------------------------------
#
# Review finding: AppConfig.from_dict filtered unknown keys but never
# validated types, so `max_agent_iterations: "20"` from a hand-edited YAML
# loaded fine and only failed much later, at comparison time inside the
# agent loop, with an error naming neither the file nor the field.


def test_app_config_coerces_a_quoted_number():
    config = AppConfig.from_dict({"max_agent_iterations": "20", "font_size": "13"})
    assert config.max_agent_iterations == 20
    assert config.font_size == 13


def test_app_config_falls_back_to_the_default_for_an_uncoercible_value():
    """"Old configs must always load" applies to a *wrong* config too: warn
    and use the default rather than refusing to start."""
    config = AppConfig.from_dict({"max_agent_iterations": "lots", "log_level": "DEBUG"})
    assert config.max_agent_iterations == AppConfig().max_agent_iterations
    assert config.log_level == "DEBUG"  # the rest of the file still applies


def test_app_config_rejects_nonsensical_but_well_typed_values():
    assert AppConfig.from_dict({"max_agent_iterations": 0}).max_agent_iterations >= 1
    assert AppConfig.from_dict({"max_context_tokens": -5}).max_context_tokens >= 0


def test_app_config_rejects_a_scalar_where_a_list_belongs():
    config = AppConfig.from_dict({"allowed_folders": "/tmp/one"})
    assert config.allowed_folders == []


def test_app_config_accepts_null_only_for_optional_fields():
    assert AppConfig.from_dict({"records_dir": None}).records_dir is None
    assert AppConfig.from_dict({"log_level": None}).log_level == AppConfig().log_level


def test_app_config_scratch_dir_defaults_to_none_and_round_trips():
    assert AppConfig().scratch_dir is None
    config = AppConfig.from_dict({"scratch_dir": "/somewhere/scratch"})
    assert config.scratch_dir == "/somewhere/scratch"
    assert config.to_dict()["scratch_dir"] == "/somewhere/scratch"


def test_app_config_still_ignores_unknown_keys():
    """``theme`` (U3) is itself a real example of this: removed as a dead
    setting, so an old config.yaml with ``theme: dark`` in it must load
    exactly as if that key had never existed at all — a still-known field
    (``log_level``) stays at its default, unaffected."""
    config = AppConfig.from_dict({"some_future_field": 1, "theme": "dark"})
    assert config.log_level == AppConfig().log_level
    assert not hasattr(config, "theme")


def test_every_app_config_field_is_loadable_from_disk():
    """A field missing from the coercion map would silently stop being
    readable from config.yaml — keep the two in step."""
    from aida.config.settings import _APP_FIELD_KINDS

    assert set(_APP_FIELD_KINDS) == set(AppConfig.__dataclass_fields__)


# --- assistant_name / user_context (B15 — "should we inject name Aida ...
# and should we somehow also inject user name and some small user info?")
# --------------------------------------------------------------------------


def test_app_config_assistant_name_defaults_to_aida_and_round_trips():
    assert AppConfig().assistant_name == "Aida"
    config = AppConfig.from_dict({"assistant_name": "Beamie"})
    assert config.assistant_name == "Beamie"
    assert config.to_dict()["assistant_name"] == "Beamie"


def test_app_config_blank_assistant_name_falls_back_to_default():
    config = AppConfig.from_dict({"assistant_name": "   "})
    assert config.assistant_name == "Aida"


def test_app_config_user_context_defaults_to_empty_and_round_trips():
    assert AppConfig().user_context == ""
    config = AppConfig.from_dict({"user_context": "Jan, beamline scientist at APS."})
    assert config.user_context == "Jan, beamline scientist at APS."
    assert config.to_dict()["user_context"] == "Jan, beamline scientist at APS."


# --- WorkspaceConfig.quick_tasks (B14 — "some workspaces may have set of
# routine tasks which I would like to add to some kind of quick selection
# methods") -------------------------------------------------------------


def test_workspace_quick_tasks_default_empty():
    from aida.config.settings import WorkspaceConfig

    assert WorkspaceConfig(name="plain").quick_tasks == []


def test_workspace_quick_tasks_roundtrip(aida_home: Path):
    from aida.config.settings import QuickTask, WorkspaceConfig

    cfg = WorkspacesConfig(
        workspaces={
            "usaxs": WorkspaceConfig(
                name="usaxs",
                quick_tasks=[
                    QuickTask(name="Reduce today's data", text="Reduce and plot today's USAXS runs."),
                    QuickTask(name="Fit Guinier", text="Fit a Guinier region to the selected dataset."),
                ],
            )
        }
    )
    save_workspaces_config(cfg, aida_home)
    loaded = load_workspaces_config(aida_home).workspaces["usaxs"]
    assert [t.name for t in loaded.quick_tasks] == ["Reduce today's data", "Fit Guinier"]
    assert loaded.quick_tasks[0].text == "Reduce and plot today's USAXS runs."


def test_workspace_quick_tasks_skips_malformed_entries(aida_home: Path):
    """A hand-edited workspaces.yaml entry missing 'name' or 'text' is
    skipped with a warning, not a crash — same "old/wrong configs must
    still load" rule as every other list field here."""
    from aida.config.settings import WorkspaceConfig

    loaded = WorkspaceConfig.from_dict(
        "usaxs",
        {
            "quick_tasks": [
                {"name": "ok", "text": "Do the thing."},
                {"name": "missing text"},
                "not even a dict",
                {"text": "missing name"},
            ]
        },
    )
    assert [t.name for t in loaded.quick_tasks] == ["ok"]


def test_workspace_quick_tasks_non_list_value_ignored():
    from aida.config.settings import WorkspaceConfig

    loaded = WorkspaceConfig.from_dict("usaxs", {"quick_tasks": "not a list"})
    assert loaded.quick_tasks == []


def test_app_config_round_trips_max_context_tokens(tmp_path: Path, aida_home: Path):
    save_app_config(AppConfig(max_context_tokens=4321))
    assert load_app_config().max_context_tokens == 4321
