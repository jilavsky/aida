"""Tests for aida.cli.config_cmds — bare ``aida config`` (Phase 1, unchanged)
and ``aida config secret set/get/delete`` (new: the previously-missing way
to actually get a secret into the OS keychain — see the module docstring
for why this was a real gap, found while reviewing a user's real
providers.yaml, which had a raw-looking API key pasted directly into
secret_ref because there was no supported command to do it properly)."""

from __future__ import annotations

from pathlib import Path

import keyring
from keyring.backend import KeyringBackend

from aida.cli.config_cmds import main
from aida.config import secrets


class _InMemoryKeyring(KeyringBackend):
    """Same in-process fake as test_secrets.py — never touch a real OS
    keychain in tests, reproducible in headless CI."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):  # noqa: D102
        return self._store.get((service, username))

    def set_password(self, service, username, password):  # noqa: D102
        self._store[(service, username)] = password

    def delete_password(self, service, username):  # noqa: D102
        self._store.pop((service, username), None)


def _use_memory_backend(monkeypatch):
    backend = _InMemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_bare_config_prints_directories(aida_home, records_home, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "AIDA config directory:" in out
    assert "AIDA records directory:" in out


def test_secret_set_stores_in_keychain_not_printed(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    exit_code = main(["secret", "set", "argo-claude", "super-secret-value"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "super-secret-value" not in out  # never echoed back
    assert secrets.get_secret("argo-claude") == "super-secret-value"


def test_secret_set_reports_a_locked_keyring_instead_of_crashing(monkeypatch, capsys):
    """A KeyringLocked backend (headless Linux with no unlocked keyring
    daemon) must produce a clean error and exit code, not a traceback."""
    from keyring.errors import KeyringLocked

    def _raise(*a, **k):
        raise KeyringLocked("Failed to unlock the collection!")

    monkeypatch.setattr("aida.config.secrets.set_secret", _raise)

    exit_code = main(["secret", "set", "argo-claude", "super-secret-value"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "AIDA_SECRET_" in err
    assert "super-secret-value" not in err


def test_secret_get_reports_set_without_printing_value(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "super-secret-value")

    exit_code = main(["secret", "get", "argo-claude"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "set" in out
    assert "super-secret-value" not in out


def test_secret_get_reports_not_set_for_unknown_profile(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    exit_code = main(["secret", "get", "does-not-exist"])
    assert exit_code == 0
    assert "not set" in capsys.readouterr().out


def test_secret_delete_removes_it(monkeypatch):
    _use_memory_backend(monkeypatch)
    secrets.set_secret("argo-claude", "value")

    exit_code = main(["secret", "delete", "argo-claude"])
    assert exit_code == 0
    assert secrets.get_secret("argo-claude") is None


def test_secret_delete_of_unset_profile_is_a_noop(monkeypatch):
    _use_memory_backend(monkeypatch)
    assert main(["secret", "delete", "does-not-exist"]) == 0


def test_secret_with_no_action_prints_usage(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    exit_code = main(["secret"])
    assert exit_code == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_secret_value_with_spaces_survives_as_a_single_argv_token(monkeypatch):
    # Mirrors what shell quoting (`aida config secret set p "a value"`)
    # produces once the shell has already split argv — argparse just sees
    # one token containing a space, same as any other value.
    _use_memory_backend(monkeypatch)
    main(["secret", "set", "argo-claude", "a value with spaces"])
    assert secrets.get_secret("argo-claude") == "a value with spaces"


# --- bare `aida config` must honor config.yaml's records_dir --------------
#
# Review finding: it printed — and, via ensure_records_dir(), *created* —
# the default ~/Documents/Aida regardless of the user's config, reporting a
# directory their sessions never touch and leaving an empty one behind.
# `aida doctor` was explicitly fixed for this (_effective_records_dir);
# this command was missed.


def test_bare_config_reports_the_configured_records_dir(aida_home, records_home, tmp_path, capsys):
    from aida.config.settings import AppConfig, save_app_config

    configured = tmp_path / "beamline-records"
    save_app_config(AppConfig(records_dir=str(configured)))

    assert main([]) == 0

    out = capsys.readouterr().out
    assert str(configured) in out
    assert configured.is_dir()
    assert not (records_home / "Documents" / "Aida").exists()


def test_bare_config_still_works_when_the_config_is_unreadable(
    aida_home, records_home, monkeypatch, capsys
):
    """Printing where things live must not be the thing that crashes."""
    import aida.config.settings as settings_module

    def _boom():
        raise ValueError("config.yaml is not valid YAML")

    monkeypatch.setattr(settings_module, "load_settings", _boom)

    assert main([]) == 0
    assert "AIDA records directory:" in capsys.readouterr().out


# --- `aida config secret set` must not require the secret on argv ---------
#
# Review finding: taking the secret as an argv value puts it in shell
# history and makes it visible in `ps` — on a shared beamline machine, both
# matter.


def test_secret_set_prompts_without_echo_when_the_value_is_omitted(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    prompts: list[str] = []

    def _fake_getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return "typed-secret"

    monkeypatch.setattr("getpass.getpass", _fake_getpass)

    assert main(["secret", "set", "argo-claude"]) == 0
    assert prompts  # really prompted rather than erroring on the missing arg
    assert secrets.get_secret("argo-claude") == "typed-secret"
    assert "typed-secret" not in capsys.readouterr().out


def test_secret_set_stores_nothing_when_the_prompt_is_left_empty(monkeypatch, capsys):
    _use_memory_backend(monkeypatch)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")

    assert main(["secret", "set", "argo-claude"]) == 1
    assert secrets.get_secret("argo-claude") is None
    assert "nothing stored" in capsys.readouterr().out.lower()


# --- `aida config export` / `aida config import` --------------------------
#
# The bundle format itself is covered in tests/test_portability.py; these
# check the command wiring, and the one behaviour that only exists at the
# CLI layer: an import that leaves an unusable MCP command behind exits
# non-zero so a scripted machine setup notices.


def test_export_then_import_round_trips_through_the_cli(aida_home, tmp_path, capsys, monkeypatch):
    from aida.config.settings import ProviderProfile, ProvidersConfig, save_providers_config

    save_providers_config(
        ProvidersConfig(profiles={"argo": ProviderProfile(name="argo", model="claudesonnet5")}),
        aida_home,
    )
    bundle = tmp_path / "setup.zip"

    assert main(["export", str(bundle)]) == 0
    assert bundle.is_file()
    assert "Wrote" in capsys.readouterr().out

    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))
    assert main(["import", str(bundle)]) == 0

    from aida.config.settings import load_settings

    assert "argo" in load_settings(target).providers.profiles
    assert "Imported" in capsys.readouterr().out


def test_export_refuses_a_directory_destination(aida_home, tmp_path, capsys):
    assert main(["export", str(tmp_path)]) == 1
    assert "give the bundle a file name" in capsys.readouterr().err


def test_import_of_a_non_bundle_fails_cleanly(aida_home, tmp_path, capsys):
    plain = tmp_path / "notes.txt"
    plain.write_text("not a bundle", encoding="utf-8")
    assert main(["import", str(plain)]) == 1
    assert "not a readable AIDA bundle" in capsys.readouterr().err


def test_import_exits_nonzero_when_a_command_could_not_be_located(
    aida_home, tmp_path, capsys, monkeypatch
):
    from aida.config.settings import McpConfig, McpServerConfig, save_mcp_config

    save_mcp_config(
        McpConfig(
            servers={
                "ghost": McpServerConfig(
                    name="ghost", command="/opt/miniconda3/envs/no-such-env-here/bin/ghost-mcp"
                )
            }
        ),
        aida_home,
    )
    bundle = tmp_path / "setup.zip"
    assert main(["export", str(bundle)]) == 0
    capsys.readouterr()

    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))
    assert main(["import", str(bundle)]) == 2
    assert "COULD NOT LOCATE" in capsys.readouterr().out


# --- Tier 2: --list / --only / --check / --map -----------------------------


def _linked_bundle(aida_home, tmp_path) -> Path:
    """A bundle whose workspace actually references a profile, a skill and
    an MCP group, so the closure has something to do."""
    from aida.config.settings import (
        McpConfig,
        McpServerConfig,
        ProviderProfile,
        ProvidersConfig,
        WorkspaceConfig,
        WorkspacesConfig,
        save_mcp_config,
        save_providers_config,
        save_workspaces_config,
    )
    from aida.portability import export_bundle

    save_providers_config(
        ProvidersConfig(
            profiles={
                "argo": ProviderProfile(name="argo", model="sonnet", secret_ref="argo-key"),
                "spare": ProviderProfile(name="spare", model="gemma"),
            }
        ),
        aida_home,
    )
    save_mcp_config(
        McpConfig(
            servers={
                "pyirena-mcp": McpServerConfig(
                    name="pyirena-mcp", command="npx", groups=["analysis"]
                )
            }
        ),
        aida_home,
    )
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "analysis": WorkspaceConfig(
                    name="analysis", profile="argo", mcp_group="analysis", skills=["saxs"]
                ),
                "spare-ws": WorkspaceConfig(name="spare-ws", profile="spare"),
            }
        ),
        aida_home,
    )
    (aida_home / "skills").mkdir(exist_ok=True)
    (aida_home / "skills" / "saxs.md").write_text("# saxs\n", encoding="utf-8")
    bundle = tmp_path / "linked.zip"
    export_bundle(bundle, base_dir=aida_home)
    return bundle


def test_list_shows_contents_and_imports_nothing(aida_home, tmp_path, capsys, monkeypatch):
    bundle = _linked_bundle(aida_home, tmp_path)
    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    assert main(["import", str(bundle), "--list"]) == 0
    out = capsys.readouterr().out
    assert "analysis" in out and "pyirena-mcp" in out
    # Every listed line doubles as a --only selector, which is the point.
    assert "--only workspace:NAME" in out
    assert not list(target.glob("*.yaml"))


def test_only_imports_the_closure(aida_home, tmp_path, capsys, monkeypatch):
    from aida.config.settings import load_settings

    bundle = _linked_bundle(aida_home, tmp_path)
    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    assert main(["import", str(bundle), "--only", "workspace:analysis"]) == 0
    settings = load_settings(target)
    assert set(settings.workspaces.workspaces) == {"analysis"}
    assert set(settings.providers.profiles) == {"argo"}
    assert (target / "skills" / "saxs.md").is_file()
    assert "Pulled in as dependencies" in capsys.readouterr().out


def test_only_accepts_comma_separated_and_repeated_forms(aida_home, tmp_path, monkeypatch):
    from aida.config.settings import load_settings

    bundle = _linked_bundle(aida_home, tmp_path)
    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    assert main(["import", str(bundle), "--only", "profile:spare,profile:argo"]) == 0
    assert set(load_settings(target).providers.profiles) == {"argo", "spare"}


def test_an_unknown_selector_fails_before_writing_anything(
    aida_home, tmp_path, capsys, monkeypatch
):
    bundle = _linked_bundle(aida_home, tmp_path)
    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    assert main(["import", str(bundle), "--only", "workspace:typo"]) == 1
    err = capsys.readouterr().err
    assert "no workspace named 'typo'" in err
    assert "--list" in err
    assert not (target / "workspaces.yaml").exists()


def test_check_previews_without_writing(aida_home, tmp_path, capsys, monkeypatch):
    bundle = _linked_bundle(aida_home, tmp_path)
    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    assert main(["import", str(bundle), "--check"]) == 0
    out = capsys.readouterr().out
    assert "PREVIEW" in out
    assert "Would import" in out
    assert not (target / "workspaces.yaml").exists()
    assert not (target / "skills").exists()


def test_map_resolves_a_command_that_would_otherwise_be_unresolved(
    aida_home, tmp_path, capsys, monkeypatch
):
    from aida.config.settings import McpConfig, McpServerConfig, load_settings, save_mcp_config
    from aida.portability import export_bundle

    save_mcp_config(
        McpConfig(
            servers={
                "ghost": McpServerConfig(
                    name="ghost", command="/opt/miniconda3/envs/no-such-env/bin/ghost-mcp"
                )
            }
        ),
        aida_home,
    )
    bundle = tmp_path / "ghost.zip"
    export_bundle(bundle, base_dir=aida_home)
    replacement = tmp_path / "ghost-mcp"
    replacement.write_text("", encoding="utf-8")

    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    # Without a mapping: imported, but flagged, and exit 2.
    assert main(["import", str(bundle)]) == 2
    capsys.readouterr()

    # With one: resolved, and exit 0.
    target2 = tmp_path / "target2" / ".aida"
    target2.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target2))
    assert (
        main(
            [
                "import",
                str(bundle),
                "--map",
                f"${{CONDA_ENV:no-such-env}}/bin/ghost-mcp={replacement}",
            ]
        )
        == 0
    )
    assert load_settings(target2).mcp.servers["ghost"].command == str(replacement)


def test_a_malformed_map_is_rejected(aida_home, tmp_path, capsys, monkeypatch):
    bundle = _linked_bundle(aida_home, tmp_path)
    target = tmp_path / "target" / ".aida"
    target.mkdir(parents=True)
    monkeypatch.setenv("AIDA_HOME", str(target))

    assert main(["import", str(bundle), "--map", "no-equals-sign"]) == 1
    assert "expected FROM=TO" in capsys.readouterr().err
