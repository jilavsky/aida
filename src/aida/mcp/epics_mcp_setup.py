"""Find epics-mcp on this machine and configure it in one step — the
``epics-mcp`` counterpart to ``aida.mcp.pyirena_setup``.

epics-mcp is a separately-maintained, policy-gated generic EPICS Channel
Access server: one PV catalog and one YAML *policy file* is the hard safety
boundary (enforced inside the server process, not by anything AIDA sends
it — see ``epics_mcp.policy``), and a policy's ``mode: read-only`` means the
write tool (``epics_pv_put``) is never even registered, regardless of
``mcp.json``.

Because read-only and write-capable are two different policy files, one
found ``epics-mcp`` install produces up to *two* ``McpServerConfig``
entries — ``epics-mcp-user`` (always, read-only) and ``epics-mcp-staff``
(only when explicitly requested) — not one, unlike pyIrena/aievaluator.
``epics_mcp_server_configs`` returns both; the caller decides which to keep.

Read ``planning/PLAN_INSTRUMENT_INTEGRATION.md`` §4 before deploying the
staff policy anywhere: its write rules are marked ``TODO(verify)`` against
the live instrument.
"""

from __future__ import annotations

import contextlib
import importlib.util
import shutil
from pathlib import Path

from aida.config.settings import McpServerConfig
from aida.mcp.env_discovery import (
    ScriptCandidate,
    epics_ca_env,
    find_console_script,
    interpreter_for,
    resolve_editable_source_root,
)

#: Server names written into ``mcp.json``.
USER_SERVER_NAME = "epics-mcp-user"
STAFF_SERVER_NAME = "epics-mcp-staff"

#: Policy names as epics-mcp's own CLI resolves them
#: (``~/.epics-mcp/policies/<name>.yaml`` — ``epics_mcp.cli.resolve_policy_path``).
USER_POLICY_NAME = "usaxs-user"
STAFF_POLICY_NAME = "usaxs-staff"

#: The one tool a write-capable policy can expose. Listing it in
#: ``confirm_tools`` on the staff server is a second, client-side layer on
#: top of the confirm-token protocol ``epics_pv_put`` already has for any
#: write rule marked ``confirm: true`` in the policy file itself.
STAFF_CONFIRM_TOOLS = ("epics_pv_put",)

#: Example policy + catalog files shipped in epics-mcp's own repo
#: (``examples/``), copied into ``~/.epics-mcp/policies/`` under these names.
_POLICY_INSTALL_MAP = {
    USER_POLICY_NAME: "policy_usaxs_readonly.yaml",
    STAFF_POLICY_NAME: "policy_usaxs_staff.yaml",
}
_CATALOG_FILENAME = "pv_catalog_usaxs.txt"

_SCRIPT_NAME = "epics-mcp"
_MODULE_FALLBACK = ["-m", "epics_mcp.cli"]


def _epics_mcp_importable() -> bool:
    try:
        return importlib.util.find_spec("epics_mcp.cli") is not None
    except (ImportError, ValueError):
        return False


def find_epics_mcp() -> list[ScriptCandidate]:
    """Every way to launch ``epics-mcp`` found on this machine, best first.
    Never raises; an empty list means "not found"."""
    return find_console_script(
        _SCRIPT_NAME, importable=_epics_mcp_importable, module_fallback=_MODULE_FALLBACK
    )


def find_epics_mcp_examples_dir(candidate: ScriptCandidate) -> Path | None:
    """Where epics-mcp's ``examples/`` (policy YAML + PV catalog) live for
    this candidate, or ``None`` if it can't be determined — same
    editable-install resolution as ``aievaluator_setup.find_aievaluator_skills_dir``,
    since epics-mcp ships these at the repo root too, not as package data."""
    python = interpreter_for(candidate)
    if python is None:
        return None
    root = resolve_editable_source_root(python, "epics_mcp")
    if root is None:
        return None
    examples = root / "examples"
    return examples if examples.is_dir() else None


def default_policy_dir() -> Path:
    """``~/.epics-mcp/policies`` — epics-mcp's own default
    ``EPICS_MCP_POLICY_DIR``, computed lazily (not a module-level constant)
    so it reflects ``$HOME`` at call time rather than at import time."""
    return Path.home() / ".epics-mcp" / "policies"


def install_epics_mcp_policies(
    examples_dir: Path,
    *,
    include_staff: bool = False,
    policy_dir: Path | None = None,
) -> list[str]:
    """Copy the example policy YAML(s) + PV catalog into
    ``~/.epics-mcp/policies/``, under the names epics-mcp's own CLI expects
    (``usaxs-user.yaml``, ``usaxs-staff.yaml``) — epics-mcp ships no
    installer of its own (its only subcommand besides serving is
    ``doctor``, which never writes files).

    Never overwrites: a policy already on disk may be a staff member's own
    tuned rules, and silently replacing it on a later AIDA setup run would
    be the worst kind of data loss. The PV catalog must be copied alongside
    *every* installed policy, not once — ``policy.py`` resolves a policy's
    ``catalog:`` entry relative to the copied file's own directory, not the
    repo's ``examples/``.
    """
    names_to_install = [USER_POLICY_NAME]
    if include_staff:
        names_to_install.append(STAFF_POLICY_NAME)

    policy_dir = policy_dir or default_policy_dir()
    policy_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    catalog_source = examples_dir / _CATALOG_FILENAME

    for name in names_to_install:
        source = examples_dir / _POLICY_INSTALL_MAP[name]
        destination = policy_dir / f"{name}.yaml"
        if not destination.exists() and source.is_file():
            try:
                shutil.copyfile(source, destination)
                installed.append(name)
            except OSError:
                continue  # a read-only or full home directory must not break setup

        catalog_destination = policy_dir / _CATALOG_FILENAME
        if not catalog_destination.exists() and catalog_source.is_file():
            with contextlib.suppress(OSError):
                shutil.copyfile(catalog_source, catalog_destination)

    return installed


def epics_mcp_server_configs(
    candidate: ScriptCandidate,
    *,
    ca_addr_list: str | None = None,
    ca_auto_addr_list: str | None = "NO",
    include_staff: bool = False,
) -> dict[str, McpServerConfig]:
    """Build the ``McpServerConfig``(s) for one found candidate.

    Always includes ``epics-mcp-user`` (read-only, group
    ``instrument-status``). Includes ``epics-mcp-staff`` (write-capable per
    its policy file, group ``instrument-staff``, ``confirm_tools:
    [epics_pv_put]``) only when ``include_staff=True`` — read-only is the
    default and the shipped example; a deployment opts into writes
    explicitly, both here and in the policy file itself.
    """
    env = epics_ca_env(ca_addr_list, ca_auto_addr_list)
    configs = {
        USER_SERVER_NAME: McpServerConfig(
            name=USER_SERVER_NAME,
            command=candidate.command,
            args=[*candidate.args, "--policy", USER_POLICY_NAME],
            env=dict(env),
            groups=["instrument-status"],
        )
    }
    if include_staff:
        configs[STAFF_SERVER_NAME] = McpServerConfig(
            name=STAFF_SERVER_NAME,
            command=candidate.command,
            args=[*candidate.args, "--policy", STAFF_POLICY_NAME],
            env=dict(env),
            groups=["instrument-staff"],
            confirm_tools=list(STAFF_CONFIRM_TOOLS),
        )
    return configs


__all__ = [
    "STAFF_CONFIRM_TOOLS",
    "STAFF_POLICY_NAME",
    "STAFF_SERVER_NAME",
    "USER_POLICY_NAME",
    "USER_SERVER_NAME",
    "default_policy_dir",
    "epics_mcp_server_configs",
    "find_epics_mcp",
    "find_epics_mcp_examples_dir",
    "install_epics_mcp_policies",
]
