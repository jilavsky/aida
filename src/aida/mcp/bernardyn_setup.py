"""Find Bernardyn's MCP server on this machine and configure it in one step
— the ``bernardyn`` counterpart to ``aida.mcp.pyirena_setup``.

[Bernardyn](https://github.com/jilavsky/Bernardyn) is the publication-figure
workbench for the same scattering data pyIrena fits: its ``bernardyn-mcp``
console script exposes a deliberately small five-tool surface (inspect a
data file, list plot recipes, create a plot package, describe a package,
re-export one graph) rather than one tool per view. Anyone who has Bernardyn
installed also has pyIrena — Bernardyn depends on ``pyirena[qtplot]`` — so
this preset exists for exactly the same reason the pyIrena one does: the
wiring is entirely mechanical, and getting it wrong by hand is the hardest
part of setting AIDA up.

Detection reuses ``aida.mcp.env_discovery`` rather than a second copy of the
sibling-conda-env search, so Bernardyn in its own environment
(``~/miniconda3/envs/bernardyn``) is found the same way pyIrena is. AIDA
never imports Bernardyn: PySide6, pyqtgraph and PyOpenGL stay in *that*
environment, which matters here more than for any other preset — Bernardyn
pins PySide6 differently from AIDA (see ``pyproject.toml``'s comment on the
``!=6.7.*,!=6.10.*`` exclusions), and the two Qt builds must not meet.

Deliberately an **offer, never automatic**, same as every other preset: this
module only *finds* things and *builds* a config object. Writing it to
``mcp.json`` is the caller's — and therefore the user's — action
(``aida mcp add-bernardyn``, the MCP dialog's "Add Bernardyn…" button, or
the onboarding screen's combined button).
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

from aida.config.settings import McpServerConfig
from aida.mcp.env_discovery import (
    ScriptCandidate,
    find_console_script,
    interpreter_for,
    launch_path,
)

#: Default server name written into ``mcp.json``. Matches what Bernardyn's
#: own ``docs/mcp.md`` uses in its example config, so a user comparing the
#: two sees the same name in both places.
DEFAULT_SERVER_NAME = "bernardyn"

#: Groups the server is put in, both of them by default.
#:
#: ``pyirena-analysis`` is deliberate rather than tidy-minded: Bernardyn is
#: what you reach for *after* a fit, it requires pyIrena to be installed at
#: all, and its whole tool surface is five tools — roughly 700 tokens of
#: schema against pyirena-mcp's ~10,200 — so adding it to the group an
#: analysis workspace already enables costs almost nothing and saves the
#: user a second configuration step they would otherwise have to discover.
#: ``bernardyn-plots`` exists for the other case: a figure-only workspace
#: that wants plotting without pyIrena's ~110 tools in context.
DEFAULT_GROUPS = ("pyirena-analysis", "bernardyn-plots")

#: No skills are attached. Bernardyn ships none of its own (unlike
#: aievaluator), AIDA bundles none for it, and the server's own MCP
#: ``instructions`` string already tells the model the intended order
#: (inspect → list recipes → create). An empty list here is a real
#: decision, not an oversight — revisit it if a ``bernardyn-usage`` skill
#: is ever written for ``skills/``.
DEFAULT_SKILLS: tuple[str, ...] = ()

_SCRIPT_NAME = "bernardyn-mcp"
_MODULE_FALLBACK = ["-m", "bernardyn.mcp.server"]

#: Alias kept for symmetry with ``pyirena_setup.PyirenaMcpCandidate`` — same
#: shape (``command``/``args``/``source``), just Bernardyn's name for it.
BernardynMcpCandidate = ScriptCandidate


def _bernardyn_importable() -> bool:
    try:
        return importlib.util.find_spec("bernardyn.mcp.server") is not None
    except (ImportError, ValueError):
        return False


def find_bernardyn_mcp() -> list[ScriptCandidate]:
    """Every way to launch ``bernardyn-mcp`` found on this machine, best
    first. Never raises; an empty list means "not found", which is the
    normal state for anyone who doesn't use Bernardyn, not an error."""
    return find_console_script(
        _SCRIPT_NAME, importable=_bernardyn_importable, module_fallback=_MODULE_FALLBACK
    )


def bernardyn_version(candidate: ScriptCandidate) -> str | None:
    """The Bernardyn version behind a candidate, or ``None`` if it can't be
    determined.

    Runs the candidate's *interpreter*, never the MCP server itself —
    starting the server would open a stdio session this function has no
    business owning. Best-effort and short-timeout: a wedged or broken
    install reports ``None`` rather than hanging a setup run.
    """
    python = interpreter_for(candidate)
    if python is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - a python interpreter path we resolved ourselves
            [
                python,
                "-c",
                "from importlib.metadata import version; print(version('bernardyn'))",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def bernardyn_server_config(
    candidate: ScriptCandidate,
    *,
    name: str = DEFAULT_SERVER_NAME,
    data_root: str | None = None,
    output_root: str | None = None,
    groups: tuple[str, ...] | list[str] = DEFAULT_GROUPS,
    skills: tuple[str, ...] | list[str] = DEFAULT_SKILLS,
) -> McpServerConfig:
    """Build the ``McpServerConfig`` for one found candidate.

    ``data_root`` sets ``BERNARDYN_DATA_ROOT``, which restricts every file
    the server will *read* to that subtree (plus the output root, so a
    package it just created can be inspected again). Left unset when
    ``None``, matching Bernardyn's own default of "any absolute path
    accepted" — the same shape as ``PYIRENA_DATA_ROOT`` in
    ``pyirena_setup.pyirena_server_config``, and a workspace's source
    folder is the right value for both.

    ``output_root`` sets ``BERNARDYN_OUTPUT_ROOT``, where created packages
    and preview images land. Left unset, Bernardyn writes them into a
    temporary ``bernardyn-mcp`` cache, which is fine for a one-off but
    means the figures the model just made are somewhere the user will not
    think to look — so the callers pass a real folder (the workspace's
    target folder) whenever there is one.

    ``PATH`` is set from the candidate's own environment prefix when one
    can be identified; see ``env_discovery.launch_path`` for why a
    cross-environment stdio server otherwise fails to start on Windows,
    which Bernardyn is especially exposed to (numpy, h5py, PySide6,
    PyOpenGL — all extension modules loading conda DLLs).
    """
    env: dict[str, str] = {}
    path_value = launch_path(candidate)
    if path_value:
        env["PATH"] = path_value
    if data_root:
        env["BERNARDYN_DATA_ROOT"] = str(Path(data_root).expanduser())
    if output_root:
        env["BERNARDYN_OUTPUT_ROOT"] = str(Path(output_root).expanduser())
    return McpServerConfig(
        name=name,
        command=candidate.command,
        args=list(candidate.args),
        env=env,
        groups=list(groups),
        skills=list(skills),
    )


__all__ = [
    "DEFAULT_GROUPS",
    "DEFAULT_SERVER_NAME",
    "DEFAULT_SKILLS",
    "BernardynMcpCandidate",
    "bernardyn_server_config",
    "bernardyn_version",
    "find_bernardyn_mcp",
]
