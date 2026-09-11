"""Export/import of a portable setup bundle (`aida.portability`).

The three properties that matter most, and that everything else here
supports: **no secret ever reaches a bundle**, **an old bundle always
imports**, and **an import is not destructive**.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from aida.config.settings import (
    AppConfig,
    KnowledgeBaseConfig,
    KnowledgeConfig,
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    ProvidersConfig,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
    save_app_config,
    save_knowledge_config,
    save_mcp_config,
    save_providers_config,
    save_workspaces_config,
)
from aida.portability import (
    BUNDLE_VERSION,
    BundleError,
    export_bundle,
    format_export,
    format_import,
    import_bundle,
    read_manifest,
)
from aida.portability.bundle import looks_secret
from aida.portability.paths_map import PathMapper

SECRET_VALUE = "BSAIF2ybLGn49xmiJcDW6dqwxCD4Bp3"


@pytest.fixture
def populated_home(aida_home: Path, tmp_path: Path) -> Path:
    """A ``~/.aida`` with one of everything, shaped like a real install:
    home-relative folders, a conda-env MCP command, a plaintext API key in a
    server's env, personal context and a private workspace note."""
    home = Path.home()
    aida_home.mkdir(parents=True, exist_ok=True)

    save_app_config(
        AppConfig(
            default_safety_mode="relaxed",
            max_agent_iterations=500,
            allowed_folders=[str(home / "Documents" / "Aida"), str(aida_home / "tmp")],
            scratch_dir=str(home / "Documents" / "Aida" / "temp"),
            user_context="The user is Jan, a beamline scientist at APS.",
            known_users=["USAXS-user"],
            window_x=2563,
            font_size=15,
            last_workspace_name="analysis",
        ),
        aida_home,
    )
    save_providers_config(
        ProvidersConfig(
            profiles={
                "argo": ProviderProfile(
                    name="argo",
                    kind="anthropic",
                    base_url="https://apps.inside.anl.gov/argoapi/",
                    model="claudesonnet5",
                    secret_ref="argo-key",
                )
            }
        ),
        aida_home,
    )
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "analysis": WorkspaceConfig(
                    name="analysis",
                    profile="argo",
                    source_folders=[str(home / "Experiments" / "USAXS")],
                    target_folder=str(home / "Desktop" / "out"),
                    python_interpreter="/opt/miniconda3/envs/pyirena/bin/python3.13",
                    mcp_group="pyirena-analysis",
                    skills=["saxs-basics"],
                    system_prompt="prompts/pyirena.md",
                    notes="private scratch notes",
                )
            }
        ),
        aida_home,
    )
    save_mcp_config(
        McpConfig(
            servers={
                "pyirena-mcp": McpServerConfig(
                    name="pyirena-mcp",
                    command="/opt/miniconda3/envs/pyirena/bin/pyirena-mcp",
                    groups=["pyirena-analysis"],
                ),
                "brave-search": McpServerConfig(
                    name="brave-search",
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-brave-search"],
                    env={"BRAVE_API_KEY": SECRET_VALUE, "EPICS_CA_ADDR_LIST": "10.54.122.63:16661"},
                ),
            }
        ),
        aida_home,
    )
    save_knowledge_config(
        KnowledgeConfig(
            knowledge_bases={
                "notes": KnowledgeBaseConfig(
                    name="notes", source_folders=[str(home / "vault")], embedding_profile="ollama"
                )
            }
        ),
        aida_home,
    )
    (aida_home / "skills").mkdir(exist_ok=True)
    (aida_home / "skills" / "saxs-basics.md").write_text("# SAXS basics\n", encoding="utf-8")
    (aida_home / "prompts").mkdir(exist_ok=True)
    (aida_home / "prompts" / "pyirena.md").write_text("You are a USAXS expert.\n", encoding="utf-8")
    (aida_home / "workflows").mkdir(exist_ok=True)
    (aida_home / "workflows" / "nightly.yaml").write_text("steps: []\n", encoding="utf-8")
    return aida_home


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------


def test_api_key_never_reaches_the_bundle(populated_home: Path, tmp_path: Path):
    """The single most important property. Asserted on the raw bytes of the
    zip, not on parsed config: a leak through any member — README, paths
    inventory, a section this test does not know about — is still a leak."""
    bundle = tmp_path / "setup.zip"
    result = export_bundle(bundle, base_dir=populated_home)

    assert SECRET_VALUE.encode() not in bundle.read_bytes()
    assert ("brave-search", "BRAVE_API_KEY") in result.redacted_env
    assert result.secret_refs == ["argo-key"]


def test_non_secret_env_survives(populated_home: Path, tmp_path: Path):
    """An EPICS address is configuration, not a credential — redacting it
    would quietly break instrument access on the target machine."""
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    with zipfile.ZipFile(bundle) as archive:
        mcp = json.loads(archive.read("config/mcp.json"))
    env = mcp["mcpServers"]["brave-search"]["env"]
    assert env["EPICS_CA_ADDR_LIST"] == "10.54.122.63:16661"
    assert env["BRAVE_API_KEY"] == ""


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("BRAVE_API_KEY", "short", True),  # name says credential
        ("SOME_TOKEN", "x", True),
        ("PASSWORD", "hunter2", True),
        ("OPAQUE", SECRET_VALUE, True),  # value shape says credential
        ("EPICS_CA_ADDR_LIST", "10.54.122.63:16661", False),
        ("EPICS_CA_AUTO_ADDR_LIST", "NO", False),
        ("DATA_DIR", "/Users/someone/Experiments/2026", False),
        ("ANYTHING", "", False),
    ],
)
def test_looks_secret(key: str, value: str, expected: bool):
    assert looks_secret(key, value) is expected


# --------------------------------------------------------------------------
# what is and is not carried
# --------------------------------------------------------------------------


def test_personal_and_machine_fields_excluded_by_default(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    with zipfile.ZipFile(bundle) as archive:
        app = yaml.safe_load(archive.read("config/app.yaml"))
        workspaces = yaml.safe_load(archive.read("config/workspaces.yaml"))

    # Personal.
    assert "user_context" not in app
    assert "known_users" not in app
    assert workspaces["workspaces"]["analysis"]["notes"] == ""
    # Per-screen. A window position and font size belong to one monitor.
    for machine_field in ("window_x", "font_size", "last_workspace_name", "splitter_sizes"):
        assert machine_field not in app
    # Genuinely portable settings still travel.
    assert app["default_safety_mode"] == "relaxed"
    assert app["max_agent_iterations"] == 500


def test_include_personal_carries_context_and_notes(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home, include_personal=True)
    with zipfile.ZipFile(bundle) as archive:
        app = yaml.safe_load(archive.read("config/app.yaml"))
        workspaces = yaml.safe_load(archive.read("config/workspaces.yaml"))
    assert app["user_context"].startswith("The user is Jan")
    assert workspaces["workspaces"]["analysis"]["notes"] == "private scratch notes"
    # Still never the screen geometry.
    assert "window_x" not in app


def test_skills_prompts_and_workflows_are_carried(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    result = export_bundle(bundle, base_dir=populated_home)
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
    assert "skills/saxs-basics.md" in names
    assert "workflows/nightly.yaml" in names
    # The workspace's system_prompt names this file relatively; without it
    # the imported workspace's prompt silently becomes the literal string
    # "prompts/pyirena.md".
    assert "prompts/pyirena.md" in names
    assert result.counts["skills"] == 1


def test_app_config_fields_are_all_classified():
    """Every ``AppConfig`` field must be portable, personal, machine-specific
    or ``config_version`` — a new field that is none of these would silently
    stop being exported, the same failure ``_APP_FIELD_KINDS`` guards
    against on the loading side."""
    from aida.portability.bundle import (
        _APP_MACHINE_FIELDS,
        _APP_PERSONAL_FIELDS,
        _APP_PORTABLE_FIELDS,
    )

    classified = set(_APP_PORTABLE_FIELDS) | set(_APP_PERSONAL_FIELDS) | set(_APP_MACHINE_FIELDS)
    classified.add("config_version")
    assert set(AppConfig().to_dict()) == classified


# --------------------------------------------------------------------------
# path tokenizing
# --------------------------------------------------------------------------


def test_home_and_conda_paths_are_tokenized(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    with zipfile.ZipFile(bundle) as archive:
        workspaces = yaml.safe_load(archive.read("config/workspaces.yaml"))
        mcp = json.loads(archive.read("config/mcp.json"))
        app = yaml.safe_load(archive.read("config/app.yaml"))
    entry = workspaces["workspaces"]["analysis"]
    assert entry["source_folders"] == ["${HOME}/Experiments/USAXS"]
    assert entry["target_folder"] == "${HOME}/Desktop/out"
    assert entry["python_interpreter"] == "${CONDA_ENV:pyirena}/bin/python3.13"
    assert mcp["mcpServers"]["pyirena-mcp"]["command"] == "${CONDA_ENV:pyirena}/bin/pyirena-mcp"
    # AIDA_HOME wins over HOME for a path inside ~/.aida, and a command that
    # is a bare name (npx) is already portable and must be left alone.
    assert "${AIDA_HOME}/tmp" in app["allowed_folders"]
    assert mcp["mcpServers"]["brave-search"]["command"] == "npx"


def test_relative_and_foreign_paths_are_left_alone(tmp_path: Path):
    mapper = PathMapper(home=tmp_path / "home", aida_home=tmp_path / "home" / ".aida")
    assert mapper.tokenize("prompts/pyirena.md") == "prompts/pyirena.md"
    assert mapper.tokenize("figures") == "figures"
    # A system path shared by every machine of that platform.
    assert mapper.tokenize("/etc/bait/instrument.yaml") == "/etc/bait/instrument.yaml"


def test_user_placeholder_survives_a_round_trip(tmp_path: Path):
    """``{user}`` is expanded at session time by ``aida.config.users``; the
    tokenizer must not eat it or a per-user folder layout breaks on import."""
    home = tmp_path / "home"
    mapper = PathMapper(home=home, aida_home=home / ".aida")
    original = str(home / "scripts" / "{user}" / "out")
    tokenized = mapper.tokenize(original)
    assert tokenized == "${HOME}/scripts/{user}/out"
    assert Path(mapper.expand(tokenized)) == Path(original)


def test_expand_rehomes_onto_this_machine(tmp_path: Path):
    mapper = PathMapper(home=tmp_path / "elsewhere", aida_home=tmp_path / "elsewhere" / ".aida")
    assert Path(mapper.expand("${HOME}/Experiments")) == tmp_path / "elsewhere" / "Experiments"
    assert Path(mapper.expand("${AIDA_HOME}/tmp")) == tmp_path / "elsewhere" / ".aida" / "tmp"


def test_unresolvable_conda_token_is_kept_verbatim_and_reported(tmp_path: Path):
    """Better an obviously-broken token the user can grep for than a
    plausible-looking path that does not exist."""
    mapper = PathMapper(home=tmp_path, aida_home=tmp_path / ".aida")
    value, problem = mapper.expand_executable("${CONDA_ENV:definitely-not-here}/bin/nope-mcp")
    assert value == "${CONDA_ENV:definitely-not-here}/bin/nope-mcp"
    assert problem and "definitely-not-here" in problem


def test_conda_executable_resolves_across_platform_layouts(tmp_path: Path):
    """A bundle exported from macOS (``bin/x``) must resolve against a
    Windows-style env (``Scripts/x.exe``)."""
    root = tmp_path / "miniconda3"
    scripts = root / "envs" / "pyirena" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pyirena-mcp.exe").write_text("", encoding="utf-8")
    mapper = PathMapper(home=tmp_path, aida_home=tmp_path / ".aida")
    mapper._conda_prefixes["pyirena"] = root / "envs" / "pyirena"
    value, problem = mapper.expand_executable("${CONDA_ENV:pyirena}/bin/pyirena-mcp")
    assert problem is None
    assert Path(value) == scripts / "pyirena-mcp.exe"


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def _fresh_home(tmp_path: Path, name: str = "target") -> Path:
    target = tmp_path / name / ".aida"
    target.mkdir(parents=True)
    return target


def test_round_trip_into_an_empty_install(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)

    report = import_bundle(bundle, base_dir=target)

    settings = load_settings(target)
    assert "analysis" in settings.workspaces.workspaces
    assert "argo" in settings.providers.profiles
    assert set(settings.mcp.servers) == {"pyirena-mcp", "brave-search"}
    assert "notes" in settings.knowledge.knowledge_bases
    assert (target / "skills" / "saxs-basics.md").is_file()
    assert (target / "prompts" / "pyirena.md").is_file()
    assert (target / "workflows" / "nightly.yaml").is_file()
    assert "workspace" in report.added
    # Paths came back as real paths on this machine, not as tokens.
    workspace = settings.workspaces.workspaces["analysis"]
    assert "${HOME}" not in (workspace.target_folder or "")
    assert workspace.target_folder == str(Path.home() / "Desktop" / "out")


def test_app_settings_are_not_applied_unless_asked(populated_home: Path, tmp_path: Path):
    """Importing someone's workspaces must not silently switch your install
    to their safety mode."""
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)

    report = import_bundle(bundle, base_dir=target)
    assert load_settings(target).app.default_safety_mode == "confirm"
    assert not report.app_settings_applied
    assert any("--app-settings" in warning for warning in report.warnings)

    report = import_bundle(bundle, base_dir=target, apply_app_settings=True)
    settings = load_settings(target)
    assert settings.app.default_safety_mode == "relaxed"
    assert settings.app.max_agent_iterations == 500
    assert report.app_settings_applied
    # Even when applied, per-screen settings keep this machine's values.
    assert settings.app.window_x is None
    assert settings.app.font_size == AppConfig().font_size


def test_import_skips_existing_by_default(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)
    import_bundle(bundle, base_dir=target)

    # Diverge locally, then import again: the local edit must survive.
    settings = load_settings(target)
    settings.workspaces.workspaces["analysis"].target_folder = "/local/edit"
    save_workspaces_config(settings.workspaces, target)

    report = import_bundle(bundle, base_dir=target)
    assert load_settings(target).workspaces.workspaces["analysis"].target_folder == "/local/edit"
    assert "analysis" in report.skipped["workspace"]
    assert report.total_added == 0


def test_import_overwrite_replaces(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)
    import_bundle(bundle, base_dir=target)
    settings = load_settings(target)
    settings.workspaces.workspaces["analysis"].target_folder = "/local/edit"
    save_workspaces_config(settings.workspaces, target)

    report = import_bundle(bundle, base_dir=target, conflict="overwrite")
    assert load_settings(target).workspaces.workspaces["analysis"].target_folder != "/local/edit"
    assert "analysis" in report.overwritten["workspace"]
    assert report.backups  # the pre-existing file was backed up first


def test_import_rename_keeps_both(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)
    import_bundle(bundle, base_dir=target)

    import_bundle(bundle, base_dir=target, conflict="rename")
    settings = load_settings(target)
    assert "analysis" in settings.workspaces.workspaces
    assert "analysis (imported)" in settings.workspaces.workspaces
    # The dataclass's own `name` has to follow the new key, or the next save
    # writes a mapping whose key and name disagree.
    assert settings.workspaces.workspaces["analysis (imported)"].name == "analysis (imported)"


def test_a_fresh_install_is_not_littered_with_backups(populated_home: Path, tmp_path: Path):
    """Setting up a new machine is the main use case; backing up the empty
    default files `load_settings` just wrote would fill its report with
    meaningless `.bak-` entries."""
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)
    report = import_bundle(bundle, base_dir=target)
    assert report.backups == []
    assert not list(target.glob("*.bak-*"))


def test_unresolved_command_is_reported_not_hidden(populated_home: Path, tmp_path: Path):
    settings = load_settings(populated_home)
    settings.mcp.servers["ghost"] = McpServerConfig(
        name="ghost", command="/opt/miniconda3/envs/no-such-env-here/bin/ghost-mcp"
    )
    save_mcp_config(settings.mcp, populated_home)
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)

    report = import_bundle(bundle, base_dir=_fresh_home(tmp_path))
    assert any("ghost" in who for who, _ in report.unresolved_commands)
    assert "COULD NOT LOCATE" in format_import(report)


def test_secret_refs_reach_the_import_report(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    report = import_bundle(bundle, base_dir=_fresh_home(tmp_path))
    assert "argo-key" in report.secret_refs
    assert ("brave-search", "BRAVE_API_KEY") in report.redacted_env
    text = format_import(report)
    assert "aida config secret set argo-key" in text
    assert "BRAVE_API_KEY" in text


# --------------------------------------------------------------------------
# format robustness
# --------------------------------------------------------------------------


def test_manifest_records_the_format_version(populated_home: Path, tmp_path: Path):
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    manifest = read_manifest(bundle)
    assert manifest["bundle_version"] == BUNDLE_VERSION
    assert manifest["scope"] == "config"


def test_a_newer_bundle_is_refused_with_a_useful_message(tmp_path: Path):
    """Silently importing a format we do not understand would produce a
    setup that is quietly incomplete — worse than refusing."""
    bundle = tmp_path / "future.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"bundle_version": BUNDLE_VERSION + 99}))
    with pytest.raises(BundleError, match="newer AIDA"):
        read_manifest(bundle)


def test_a_bundle_missing_optional_sections_still_imports(tmp_path: Path):
    """Forward compatibility's other half: a bundle with only some sections
    (an older writer, or a hand-trimmed one) must import what it has."""
    bundle = tmp_path / "partial.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"bundle_version": 1}))
        archive.writestr("skills/only-this.md", "# only this\n")
    target = _fresh_home(tmp_path)
    report = import_bundle(bundle, base_dir=target)
    assert (target / "skills" / "only-this.md").is_file()
    assert "only-this" in report.added["skill"]


def test_not_a_bundle_is_a_clear_error(tmp_path: Path):
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    with pytest.raises(BundleError, match="not a readable AIDA bundle"):
        read_manifest(plain)

    zip_without_manifest = tmp_path / "other.zip"
    with zipfile.ZipFile(zip_without_manifest, "w") as archive:
        archive.writestr("a.txt", "x")
    with pytest.raises(BundleError, match="not an AIDA bundle"):
        read_manifest(zip_without_manifest)


def test_zip_slip_entries_are_refused(tmp_path: Path):
    """A bundle is untrusted input; ``../`` in a member name must not write
    outside the target folder."""
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"bundle_version": 1}))
        archive.writestr("skills/../../escaped.md", "pwned\n")
    target = _fresh_home(tmp_path)

    report = import_bundle(bundle, base_dir=target)
    assert not (target.parent.parent / "escaped.md").exists()
    assert any("unsafe entry" in warning for warning in report.warnings)


def test_bad_conflict_policy_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="conflict must be one of"):
        import_bundle(tmp_path / "whatever.zip", conflict="clobber")


def test_export_report_names_what_was_withheld(populated_home: Path, tmp_path: Path):
    result = export_bundle(tmp_path / "setup.zip", base_dir=populated_home)
    text = format_export(result)
    assert "BRAVE_API_KEY" in text
    assert "aida config secret set argo-key" in text
    assert "--include-personal" in text


# ==========================================================================
# Tier 2 — selective import, dependency closure, path remapping
# ==========================================================================


@pytest.fixture
def linked_home(aida_home: Path) -> Path:
    """A ``~/.aida`` whose items actually reference each other, so a closure
    has something to close over: two workspaces sharing a profile, an MCP
    group with two servers in it, a knowledge base with an embedding
    profile, and a workflow and schedule on top."""
    from aida.config.settings import (
        EmbeddingProfile,
        ScheduleEntry,
        SchedulesConfig,
        WorkflowConfig,
        WorkflowStep,
        save_schedules_config,
        save_workflow,
    )

    aida_home.mkdir(parents=True, exist_ok=True)
    save_providers_config(
        ProvidersConfig(
            profiles={
                "argo": ProviderProfile(name="argo", model="claudesonnet5", secret_ref="argo-key"),
                "local": ProviderProfile(name="local", model="gemma", secret_ref="local-key"),
            },
            embedding_profiles={"embed": EmbeddingProfile(name="embed", model="nomic")},
        ),
        aida_home,
    )
    save_knowledge_config(
        KnowledgeConfig(
            knowledge_bases={
                "vault": KnowledgeBaseConfig(
                    name="vault", source_folders=["${HOME}/vault"], embedding_profile="embed"
                )
            }
        ),
        aida_home,
    )
    save_mcp_config(
        McpConfig(
            servers={
                "pyirena-mcp": McpServerConfig(
                    name="pyirena-mcp",
                    command="npx",
                    groups=["analysis"],
                    skills=["pyirena-usage"],
                ),
                "epics-mcp": McpServerConfig(
                    name="epics-mcp", command="npx", groups=["analysis", "instrument"]
                ),
                "unrelated": McpServerConfig(name="unrelated", command="npx", groups=["other"]),
            }
        ),
        aida_home,
    )
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "analysis": WorkspaceConfig(
                    name="analysis",
                    profile="argo",
                    mcp_group="analysis",
                    skills=["saxs-basics"],
                    knowledge_bases=["vault"],
                ),
                "other": WorkspaceConfig(name="other", profile="local", mcp_group="other"),
            }
        ),
        aida_home,
    )
    save_schedules_config(
        SchedulesConfig(
            schedules={"nightly": ScheduleEntry(name="nightly", workflow="reduce", at="02:00")}
        ),
        aida_home,
    )
    save_workflow(
        WorkflowConfig(
            name="reduce",
            workspace="analysis",
            steps=[WorkflowStep(prompt="reduce everything")],
        ),
        aida_home / "workflows",
    )
    skills = aida_home / "skills"
    skills.mkdir(exist_ok=True)
    for name in ("saxs-basics", "pyirena-usage", "unused"):
        (skills / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    return aida_home


@pytest.fixture
def linked_bundle(linked_home: Path, tmp_path: Path) -> Path:
    bundle = tmp_path / "linked.zip"
    export_bundle(bundle, base_dir=linked_home)
    return bundle


# --- reading contents -----------------------------------------------------


def test_contents_lists_every_selectable_item(linked_bundle: Path):
    from aida.portability import read_contents

    contents = read_contents(linked_bundle)
    keys = contents.all_keys()
    assert ("workspace", "analysis") in keys
    assert ("profile", "argo") in keys
    assert ("embedding profile", "embed") in keys
    assert ("MCP server", "epics-mcp") in keys
    assert ("knowledge base", "vault") in keys
    assert ("workflow", "reduce") in keys
    assert ("schedule", "nightly") in keys
    assert ("skill", "unused") in keys
    # Details are what make a picker usable rather than a list of bare names.
    detail = {i.name: i.detail for i in contents.items() if i.kind == "MCP server"}
    assert "analysis" in detail["pyirena-mcp"]


def test_contents_understands_a_folder_skill(aida_home: Path, tmp_path: Path):
    """``<name>/SKILL.md`` plus siblings is one *item*, not three."""
    skills = aida_home / "skills" / "big-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# big\n", encoding="utf-8")
    (skills / "reference.md").write_text("more\n", encoding="utf-8")
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=aida_home)

    from aida.portability import read_contents

    contents = read_contents(bundle)
    assert set(contents.skill_members) == {"big-skill"}
    assert len(contents.skill_members["big-skill"]) == 2

    target = _fresh_home(tmp_path)
    report = import_bundle(bundle, base_dir=target)
    assert (target / "skills" / "big-skill" / "SKILL.md").is_file()
    assert (target / "skills" / "big-skill" / "reference.md").is_file()
    assert report.added["skill"] == ["big-skill"]  # reported once, not per file


# --- closure --------------------------------------------------------------


def test_workspace_pulls_its_whole_closure(linked_bundle: Path):
    from aida.portability import expand_selection, read_contents

    contents = read_contents(linked_bundle)
    chosen = expand_selection(contents, {("workspace", "analysis")})

    assert ("profile", "argo") in chosen.keys  # its profile
    assert ("skill", "saxs-basics") in chosen.keys  # its skills
    assert ("knowledge base", "vault") in chosen.keys  # its knowledge bases
    assert ("embedding profile", "embed") in chosen.keys  # ...and *their* embedding profile
    assert ("MCP server", "pyirena-mcp") in chosen.keys  # every server in its group
    assert ("MCP server", "epics-mcp") in chosen.keys
    assert ("skill", "pyirena-usage") in chosen.keys  # ...and the skills those attach
    # Nothing it does not need.
    assert ("MCP server", "unrelated") not in chosen.keys
    assert ("profile", "local") not in chosen.keys
    assert ("workspace", "other") not in chosen.keys
    assert ("skill", "unused") not in chosen.keys


def test_closure_is_transitive_through_schedule_and_workflow(linked_bundle: Path):
    """schedule -> workflow -> workspace -> profile/skills/servers, in one
    pass of the fixpoint rather than one level."""
    from aida.portability import expand_selection, read_contents

    contents = read_contents(linked_bundle)
    chosen = expand_selection(contents, {("schedule", "nightly")})
    assert ("workflow", "reduce") in chosen.keys
    assert ("workspace", "analysis") in chosen.keys
    assert ("profile", "argo") in chosen.keys
    assert ("MCP server", "pyirena-mcp") in chosen.keys


def test_closure_reports_what_the_bundle_cannot_satisfy(aida_home: Path, tmp_path: Path):
    """A dangling reference is reported, not fatal: one stale name in a
    colleague's config must not block everything else in it."""
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "broken": WorkspaceConfig(
                    name="broken", profile="gone", mcp_group="nobody", skills=["missing"]
                )
            }
        ),
        aida_home,
    )
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=aida_home)

    from aida.portability import expand_selection, read_contents

    contents = read_contents(bundle)
    chosen = expand_selection(contents, {("workspace", "broken")})
    joined = " | ".join(chosen.missing)
    assert "profile 'gone'" in joined
    assert "skill 'missing'" in joined
    assert "mcp_group 'nobody'" in joined
    assert ("workspace", "broken") in chosen.keys  # imported anyway


def test_no_deps_imports_exactly_what_was_named(linked_bundle: Path):
    from aida.portability import expand_selection, read_contents

    contents = read_contents(linked_bundle)
    chosen = expand_selection(contents, {("workspace", "analysis")}, follow_dependencies=False)
    assert chosen.keys == {("workspace", "analysis")}
    assert not chosen.added


def test_selection_defaults_to_everything(linked_bundle: Path):
    from aida.portability import expand_selection, read_contents

    contents = read_contents(linked_bundle)
    assert expand_selection(contents, None).keys == contents.all_keys()


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("workspace:analysis", ("workspace", "analysis")),
        ("workspaces:analysis", ("workspace", "analysis")),
        ("server:epics-mcp", ("MCP server", "epics-mcp")),
        ("mcp-server:epics-mcp", ("MCP server", "epics-mcp")),
        ("MCP server:epics-mcp", ("MCP server", "epics-mcp")),
        ("kb:vault", ("knowledge base", "vault")),
        ("embedding_profile:embed", ("embedding profile", "embed")),
    ],
)
def test_selector_kinds_are_matched_leniently(linked_bundle: Path, selector, expected):
    """The canonical kinds contain spaces (``MCP server``); nobody should
    have to quote a shell argument to name one."""
    from aida.portability import parse_selection, read_contents

    keys, problems = parse_selection([selector], read_contents(linked_bundle))
    assert not problems
    assert keys == {expected}


def test_bad_selectors_are_reported_individually(linked_bundle: Path):
    from aida.portability import parse_selection, read_contents

    keys, problems = parse_selection(
        ["nonsense", "sandwich:ham", "workspace:nope", "workspace:analysis"],
        read_contents(linked_bundle),
    )
    assert keys == {("workspace", "analysis")}
    assert len(problems) == 3
    assert any("expected KIND:NAME" in p for p in problems)
    assert any("unknown kind" in p for p in problems)
    assert any("no workspace named" in p for p in problems)


# --- selective import -----------------------------------------------------


def test_selective_import_writes_only_the_closure(linked_bundle: Path, tmp_path: Path):
    target = _fresh_home(tmp_path)
    report = import_bundle(linked_bundle, base_dir=target, select={("workspace", "analysis")})

    settings = load_settings(target)
    assert set(settings.workspaces.workspaces) == {"analysis"}
    assert set(settings.providers.profiles) == {"argo"}
    assert set(settings.mcp.servers) == {"pyirena-mcp", "epics-mcp"}
    assert set(settings.knowledge.knowledge_bases) == {"vault"}
    assert not settings.schedules.schedules
    assert (target / "skills" / "saxs-basics.md").is_file()
    assert not (target / "skills" / "unused.md").exists()
    assert not (target / "workflows" / "reduce.yaml").exists()
    # The report says what came along and why.
    assert ("profile", "argo") in report.pulled_in
    assert report.selected


def test_selective_import_only_asks_for_the_secrets_it_needs(linked_bundle: Path, tmp_path: Path):
    """Importing one workspace should not hand back every key in the
    bundle."""
    report = import_bundle(
        linked_bundle, base_dir=_fresh_home(tmp_path), select={("workspace", "analysis")}
    )
    assert report.secret_refs == ["argo-key"]
    assert "local-key" not in report.secret_refs


def test_imported_selection_validates_clean(linked_bundle: Path, tmp_path: Path):
    """The whole point of the closure: what lands must actually work. An
    unknown-profile failure here would mean the closure missed something."""
    target = _fresh_home(tmp_path)
    report = import_bundle(linked_bundle, base_dir=target, select={("workspace", "analysis")})
    assert not any("unknown profile" in w for w in report.warnings)
    assert not any("skill file(s) not found" in w for w in report.warnings)
    assert not any("will have no MCP tools" in w for w in report.warnings)


def test_import_without_closure_produces_the_broken_thing_it_warns_about(
    linked_bundle: Path, tmp_path: Path
):
    """`--no-deps` is honoured literally, including the consequences —
    which is why it is not the default."""
    target = _fresh_home(tmp_path)
    report = import_bundle(
        linked_bundle,
        base_dir=target,
        select={("workspace", "analysis")},
        follow_dependencies=False,
    )
    assert set(load_settings(target).providers.profiles) == set()
    assert any("unknown profile" in w for w in report.warnings)


# --- dry run --------------------------------------------------------------


def test_dry_run_writes_absolutely_nothing(linked_bundle: Path, tmp_path: Path):
    target = _fresh_home(tmp_path)
    before = {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}

    report = import_bundle(linked_bundle, base_dir=target, dry_run=True)

    after = {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}
    assert after == before
    assert not (target / "skills").exists()
    assert report.dry_run
    assert "PREVIEW" in format_import(report)


def test_dry_run_predicts_what_a_real_import_does(linked_bundle: Path, tmp_path: Path):
    preview = import_bundle(linked_bundle, base_dir=_fresh_home(tmp_path, "a"), dry_run=True)
    real = import_bundle(linked_bundle, base_dir=_fresh_home(tmp_path, "b"))
    assert preview.added == real.added
    assert preview.secret_refs == real.secret_refs
    assert preview.unresolved_commands == real.unresolved_commands


def test_dry_run_does_not_warn_about_skills_it_would_have_written(
    linked_bundle: Path, tmp_path: Path
):
    """The validator looks at what is on disk *now*; in a preview the skills
    this import would write are not there yet, and complaining about them
    would be a phantom problem."""
    report = import_bundle(linked_bundle, base_dir=_fresh_home(tmp_path), dry_run=True)
    assert not any("skill file(s) not found" in w for w in report.warnings)


# --- path overrides -------------------------------------------------------


def test_override_redirects_a_whole_tree_by_prefix(tmp_path: Path):
    mapper = PathMapper(
        home=tmp_path / "home",
        aida_home=tmp_path / "home" / ".aida",
        overrides={"${HOME}/Experiments": "/data/usaxs"},
    )
    assert mapper.expand("${HOME}/Experiments") == "/data/usaxs"
    assert mapper.expand("${HOME}/Experiments/2026/scan1") == "/data/usaxs/2026/scan1"
    # Untouched paths still resolve the normal way.
    assert Path(mapper.expand("${HOME}/Desktop")) == tmp_path / "home" / "Desktop"


def test_longest_matching_override_wins(tmp_path: Path):
    """A specific rule must be able to sit alongside a broad one."""
    mapper = PathMapper(
        home=tmp_path,
        aida_home=tmp_path / ".aida",
        overrides={"${HOME}": "/data", "${HOME}/Experiments/2026": "/archive/2026"},
    )
    assert mapper.expand("${HOME}/Experiments/2026/s1") == "/archive/2026/s1"
    assert mapper.expand("${HOME}/Experiments/2025/s1") == "/data/Experiments/2025/s1"


def test_override_fixes_an_unresolvable_command(linked_bundle: Path, tmp_path: Path):
    """The path table's whole purpose: turn a reported problem into a
    working config without hand-editing mcp.json afterwards."""
    fake = tmp_path / "bin" / "ghost-mcp"
    fake.parent.mkdir(parents=True)
    fake.write_text("", encoding="utf-8")

    settings = load_settings(tmp_path / "src-home")
    settings.mcp.servers["ghost"] = McpServerConfig(
        name="ghost", command="/opt/miniconda3/envs/no-such-env/bin/ghost-mcp"
    )
    save_mcp_config(settings.mcp, tmp_path / "src-home")
    bundle = tmp_path / "ghost.zip"
    export_bundle(bundle, base_dir=tmp_path / "src-home")

    target = _fresh_home(tmp_path)
    report = import_bundle(
        bundle,
        base_dir=target,
        path_overrides={"${CONDA_ENV:no-such-env}/bin/ghost-mcp": str(fake)},
    )
    assert not report.unresolved_commands
    assert load_settings(target).mcp.servers["ghost"].command == str(fake)


# --- path inspection ------------------------------------------------------


def test_inspect_paths_finds_both_kinds_of_problem(populated_home: Path, tmp_path: Path):
    from aida.portability import ROLE_EXECUTABLE, ROLE_FOLDER, inspect_paths, read_contents

    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    contents = read_contents(bundle)

    issues = inspect_paths(contents, PathMapper())
    roles = {issue.role for issue in issues}
    assert ROLE_FOLDER in roles  # ~/Experiments/USAXS does not exist
    assert all(issue.where for issue in issues)
    # Executables sort first: a missing command breaks a server outright,
    # a missing folder is often a mount that is not up yet.
    if ROLE_EXECUTABLE in roles:
        assert issues[0].role == ROLE_EXECUTABLE


def test_inspect_paths_shrinks_as_overrides_are_supplied(populated_home: Path, tmp_path: Path):
    """What makes the dialog's table feel responsive — filling a row in has
    to visibly remove it."""
    from aida.portability import inspect_paths, read_contents

    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    contents = read_contents(bundle)

    before = inspect_paths(contents, PathMapper())
    assert before
    existing = tmp_path / "somewhere-real"
    existing.mkdir()
    mapper = PathMapper(overrides={before[0].value: str(existing)})
    after = inspect_paths(contents, mapper)
    assert len(after) == len(before) - 1


def test_inspect_paths_deduplicates_a_shared_folder(aida_home: Path, tmp_path: Path):
    """Two workspaces pointing at the same missing folder is one row to
    fix, not two."""
    from aida.portability import inspect_paths, read_contents

    shared = str(Path.home() / "definitely-not-here-42")
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "a": WorkspaceConfig(name="a", source_folders=[shared]),
                "b": WorkspaceConfig(name="b", target_folder=shared),
            }
        ),
        aida_home,
    )
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=aida_home)

    issues = inspect_paths(read_contents(bundle), PathMapper())
    matching = [i for i in issues if i.value.endswith("definitely-not-here-42")]
    assert len(matching) == 1
    assert len(matching[0].where) == 2


def test_inspect_paths_respects_the_selection(linked_bundle: Path, tmp_path: Path):
    """Paths belonging to items you did not select are not your problem."""
    from aida.portability import expand_selection, inspect_paths, read_contents

    contents = read_contents(linked_bundle)
    everything = inspect_paths(contents, PathMapper())
    just_other = inspect_paths(
        contents, PathMapper(), expand_selection(contents, {("workspace", "other")})
    )
    assert len(just_other) < len(everything)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["${HOME}/a=/data/a"], {"${HOME}/a": "/data/a"}),
        (["a=b=c"], {"a": "b=c"}),  # split on the first `=` only
        (["  x = y  "], {"x": "y"}),
    ],
)
def test_parse_overrides(raw, expected):
    from aida.portability import parse_overrides

    overrides, problems = parse_overrides(raw)
    assert overrides == expected
    assert not problems


def test_parse_overrides_rejects_a_malformed_mapping():
    from aida.portability import parse_overrides

    overrides, problems = parse_overrides(["no-equals-sign", "=missing-source"])
    assert not overrides
    assert len(problems) == 2


# --- prompt files outside prompts/ ----------------------------------------


def test_a_prompt_file_anywhere_under_aida_home_round_trips(aida_home: Path, tmp_path: Path):
    """A workspace may name its prompt file at any relative path — it is
    resolved against ``~/.aida`` — so the bundle has to carry it at that
    same path, not force it into ``prompts/``. Otherwise the imported
    workspace's system prompt silently becomes the literal path string."""
    custom = aida_home / "team-prompts"
    custom.mkdir(parents=True)
    (custom / "reviewer.md").write_text("You are a reviewer.\n", encoding="utf-8")
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "review": WorkspaceConfig(name="review", system_prompt="team-prompts/reviewer.md")
            }
        ),
        aida_home,
    )
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=aida_home)

    target = _fresh_home(tmp_path)
    import_bundle(bundle, base_dir=target)
    assert (
        (target / "team-prompts" / "reviewer.md")
        .read_text(encoding="utf-8")
        .startswith("You are a reviewer.")
    )


def test_a_selected_workspace_brings_its_prompt_file(populated_home: Path, tmp_path: Path):
    """Prompt files are never selected directly — they arrive with the
    workspace that names them, or that workspace is quietly broken."""
    bundle = tmp_path / "setup.zip"
    export_bundle(bundle, base_dir=populated_home)
    target = _fresh_home(tmp_path)
    import_bundle(bundle, base_dir=target, select={("workspace", "analysis")})
    assert (target / "prompts" / "pyirena.md").is_file()
