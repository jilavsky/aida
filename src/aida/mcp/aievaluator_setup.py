"""Find aievaluator's MCP server on this machine and configure it in one
step — the ``aievaluator`` counterpart to ``aida.mcp.pyirena_setup``.

aievaluator is a separately-maintained USAXS/12-ID instrument-status package
(EPICS beam/energy/flux checks, tune-scan QC, fitness reports); AIDA talks to
it over stdio via its own ``aievaluator-mcp`` console script, exactly the way
it talks to ``pyirena-mcp`` — never by importing aievaluator's code, so
pyepics and the Channel Access environment stay in *that* conda env, not
AIDA's (``planning/PLAN_INSTRUMENT_INTEGRATION.md`` §2.1, option C).

Detection reuses ``aida.mcp.env_discovery`` rather than a second copy of the
sibling-conda-env search: the beamline layout (``~/.conda/envs/aievaluator``,
i.e. ``/home/beams/USAXS/.conda/envs/aievaluator`` on ``usaxscontrol``) is
already covered by that search, so this module only adds what's specific to
aievaluator — its env vars and its skill files.

Deliberately an offer, never automatic, same as pyIrena's setup: this only
*finds* things and *builds* a config object; writing it to ``mcp.json`` is
the caller's (``aida mcp add-aievaluator``) action.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from aida.config.settings import McpServerConfig
from aida.mcp.env_discovery import (
    ScriptCandidate,
    epics_ca_env,
    find_console_script,
    interpreter_for,
    resolve_editable_source_root,
)

#: Default server name written into ``mcp.json``.
DEFAULT_SERVER_NAME = "aievaluator-mcp"

#: aievaluator's tools are read-only regardless of who's asking, so unlike
#: pyIrena (one group) it belongs in both a plain "what's the beam doing"
#: group and the staff group.
DEFAULT_GROUPS = ("instrument-status", "instrument-staff")

#: The one tool that writes anything (the Obsidian fitness-report record) —
#: gated behind AIDA's own confirm-before-run, independent of the workspace's
#: safety mode, since this isn't a filesystem write.
DEFAULT_CONFIRM_TOOLS = ("fitness_report",)

#: Skill names shipped in aievaluator's own repo (``skills/*.md``), not
#: bundled with AIDA — see ``find_aievaluator_skills_dir``.
DEFAULT_SKILLS = ("aievaluator", "usaxs-instrument")

_SCRIPT_NAME = "aievaluator-mcp"
_MODULE_FALLBACK = ["-m", "aievaluator.mcp_server"]

#: Alias kept for symmetry with ``pyirena_setup.PyirenaMcpCandidate`` — same
#: shape (``command``/``args``/``source``), just aievaluator's name for it.
AievaluatorMcpCandidate = ScriptCandidate


def _aievaluator_importable() -> bool:
    try:
        return importlib.util.find_spec("aievaluator.mcp_server") is not None
    except (ImportError, ValueError):
        return False


def find_aievaluator_mcp() -> list[ScriptCandidate]:
    """Every way to launch ``aievaluator-mcp`` found on this machine, best
    first. Never raises; an empty list means "not found"."""
    return find_console_script(
        _SCRIPT_NAME, importable=_aievaluator_importable, module_fallback=_MODULE_FALLBACK
    )


def find_aievaluator_skills_dir(candidate: ScriptCandidate) -> Path | None:
    """Where aievaluator's ``skills/*.md`` live for this candidate, or
    ``None`` if it can't be determined.

    They ship in the repo checkout, not as installed package data
    (aievaluator's own ``pyproject.toml`` only force-includes
    ``config/*.yaml`` and ``py.typed``), and the checkout path differs by
    machine (``~/GitHub/aievaluator`` here, ``~/Apps/aievaluator`` at the
    beamline) — so this asks the candidate's own interpreter rather than
    guessing a convention (see ``resolve_editable_source_root``).
    """
    python = interpreter_for(candidate)
    if python is None:
        return None
    root = resolve_editable_source_root(python, "aievaluator")
    if root is None:
        return None
    skills = root / "skills"
    return skills if skills.is_dir() else None


def aievaluator_server_config(
    candidate: ScriptCandidate,
    *,
    name: str = DEFAULT_SERVER_NAME,
    ca_addr_list: str | None = None,
    ca_auto_addr_list: str | None = "NO",
    groups: tuple[str, ...] | list[str] = DEFAULT_GROUPS,
    skills: tuple[str, ...] | list[str] = DEFAULT_SKILLS,
    confirm_tools: tuple[str, ...] | list[str] = DEFAULT_CONFIRM_TOOLS,
) -> McpServerConfig:
    """Build the ``McpServerConfig`` for one found candidate."""
    env = epics_ca_env(ca_addr_list, ca_auto_addr_list)
    return McpServerConfig(
        name=name,
        command=candidate.command,
        args=list(candidate.args),
        env=env,
        groups=list(groups),
        skills=list(skills),
        confirm_tools=list(confirm_tools),
    )


__all__ = [
    "AievaluatorMcpCandidate",
    "DEFAULT_CONFIRM_TOOLS",
    "DEFAULT_GROUPS",
    "DEFAULT_SERVER_NAME",
    "DEFAULT_SKILLS",
    "aievaluator_server_config",
    "find_aievaluator_mcp",
    "find_aievaluator_skills_dir",
]
