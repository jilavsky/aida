"""Generic "find a console script in a sibling conda/mamba environment" —
extracted from ``aida.mcp.pyirena_setup`` so a second and third MCP server
preset (aievaluator, epics-mcp) reuse the same search instead of copying it.

``pyirena_setup.py`` itself is left untouched: it predates this module, is
fully tested, and nothing here changes its behavior. New presets import
``candidate_env_dirs`` / ``find_console_script`` / ``resolve_editable_source_root``
from here instead.

Search order (most likely to be the one the user meant, first):

1. The *calling* Python's own environment (``sys.executable``'s directory).
2. ``PATH`` — the active conda env in a terminal-launched AIDA.
3. ``python -m <module>`` using the calling interpreter, when the module
   imports but no console script is on disk (an editable install whose
   scripts were never linked).
4. Sibling conda/mamba environments — the common beamline layout, because a
   heavy dependency set (pyepics, Tiled, ...) usually lives in its own env.
   ``~/.conda/envs`` is in the search list, which is exactly
   ``/home/beams/USAXS/.conda/envs`` on ``usaxscontrol`` when ``$HOME`` is
   ``/home/beams/USAXS`` — the same check that already makes
   ``add-pyirena`` work unmodified on that machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from aida.config.logging_setup import get_logger

logger = get_logger("mcp-env-discovery")

_BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


def script_filename(name: str) -> str:
    """``name`` with the platform's console-script extension, e.g.
    ``pyirena-mcp`` -> ``pyirena-mcp.exe`` on Windows."""
    return f"{name}.exe" if os.name == "nt" else name


@dataclass(frozen=True)
class ScriptCandidate:
    """One way to launch a console script that was actually found on disk.

    ``command`` + ``args`` go straight into ``McpServerConfig``. ``source``
    is a short human-readable phrase ("AIDA's own environment", "PATH",
    "conda env 'aievaluator'") for a CLI line or dialog row.
    """

    command: str
    args: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def display(self) -> str:
        joined = " ".join([self.command, *self.args])
        return f"{joined}  ({self.source})" if self.source else joined


def candidate_env_dirs() -> list[Path]:
    """Directories that plausibly hold sibling conda/mamba environments."""
    home = Path.home()
    roots = [
        home / "miniconda3" / "envs",
        home / "anaconda3" / "envs",
        home / "miniforge3" / "envs",
        home / "mambaforge" / "envs",
        home / ".conda" / "envs",
        Path("/opt/homebrew/Caskroom/miniconda/base/envs"),
        Path("/opt/miniconda3/envs"),
        Path("/opt/anaconda3/envs"),
    ]
    # CONDA_PREFIX points at the *active* env; its parent is the envs dir
    # for any non-base env, which covers installs in unusual locations.
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        roots.append(Path(conda_prefix).parent)
    return roots


def find_console_script(
    script_name: str,
    *,
    importable: object = None,
    module_fallback: list[str] | None = None,
    own_executable: str | None = None,
) -> list[ScriptCandidate]:
    """Every way to launch ``script_name`` found on this machine, best first.

    ``importable`` is a zero-arg callable returning whether the fallback
    module is importable from the *calling* interpreter (used only to decide
    whether to offer the ``python -m`` fallback in AIDA's own environment).
    ``module_fallback`` is the ``["-m", "pkg.module"]`` args for that
    fallback. Both are optional — omit them for a package with no such
    module entry point.

    Never raises and never launches anything — a missing home directory, an
    unreadable envs folder, or a stale symlink is skipped. An empty list
    means "not found", a normal state, not an error.
    """
    file_name = script_filename(script_name)
    candidates: list[ScriptCandidate] = []
    seen: set[str] = set()

    def add(command: str | Path, source: str, args: list[str] | None = None) -> None:
        key = f"{command}|{' '.join(args or [])}"
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            ScriptCandidate(command=str(command), args=list(args or []), source=source)
        )

    executable = own_executable or sys.executable
    own_bin = Path(executable).parent / file_name
    own_script_found = own_bin.is_file()
    if own_script_found:
        add(own_bin, "AIDA's own environment")

    on_path = shutil.which(script_name)
    if on_path:
        add(Path(on_path).resolve(), "PATH")

    # Only a *fallback*: when the console script from AIDA's own environment
    # exists, `python -m` there launches exactly the same server from the
    # same interpreter, so offering both would make every ordinary
    # same-environment install look like an ambiguous two-way choice.
    if not own_script_found and module_fallback and importable is not None and importable():
        add(executable, "AIDA's own environment (python -m)", module_fallback)

    for envs_dir in candidate_env_dirs():
        try:
            if not envs_dir.is_dir():
                continue
            entries = sorted(envs_dir.iterdir())
        except OSError:  # unreadable, or a path that vanished mid-scan
            continue
        for env in entries:
            script = env / _BIN_DIRNAME / file_name
            try:
                if script.is_file():
                    add(script, f"conda env {env.name!r}")
            except OSError:
                continue

    logger.debug("%s candidates: %s", script_name, [c.display for c in candidates])
    return candidates


def epics_ca_env(ca_addr_list: str | None, ca_auto_addr_list: str | None = "NO") -> dict[str, str]:
    """The two env vars an EPICS Channel Access client subprocess needs.

    Shared by ``aievaluator_setup`` and ``epics_mcp_setup``: AIDA does not
    pass the launching shell's environment through to MCP subprocesses
    (``aida.mcp.server`` builds an explicit ``env`` dict per server), so
    ``EPICS_CA_ADDR_LIST`` must be set here or every PV read will silently
    fail to connect even though the same command run by hand in a terminal
    works fine. Returns an empty dict (not a placeholder value) when
    ``ca_addr_list`` is unset — never guess a gateway address.
    """
    env: dict[str, str] = {}
    if ca_addr_list:
        env["EPICS_CA_ADDR_LIST"] = ca_addr_list
    if ca_auto_addr_list:
        env["EPICS_CA_AUTO_ADDR_LIST"] = ca_auto_addr_list
    return env


def resolve_ca_addr_list(explicit: str | None) -> str | None:
    """``--epics-addr`` wins; otherwise fall back to ``$EPICS_CA_ADDR_LIST``
    already exported in the shell this command runs in — never guessed."""
    return explicit or os.environ.get("EPICS_CA_ADDR_LIST") or None


def interpreter_for(candidate: ScriptCandidate) -> str | None:
    """The Python interpreter that would run ``candidate``, or ``None`` if it
    can't be found. For a ``python -m pkg.module`` candidate that's simply
    ``candidate.command``; for a console script, it's the ``python`` sitting
    next to it in the same ``bin``/``Scripts`` directory."""
    if candidate.args[:1] == ["-m"]:
        return candidate.command
    bin_dir = Path(candidate.command).parent
    python = str(bin_dir / ("python.exe" if os.name == "nt" else "python"))
    return python if Path(python).exists() else None


def resolve_editable_source_root(python_executable: str, module_name: str) -> Path | None:
    """Best-effort: find the repo checkout backing an editable install of
    ``module_name``, by asking ``python_executable`` where the module lives
    and walking up to the nearest ``pyproject.toml``.

    Several sibling packages (aievaluator, epics-mcp) ship skill files or
    example policies at the repo root, outside the installed package, at a
    path that differs by machine (``~/GitHub/aievaluator`` on a Mac,
    ``~/Apps/aievaluator`` at the beamline). An editable install
    (``pip install -e .``, the norm for these lab packages) still points
    ``module.__file__`` at the real checkout, so asking the interpreter is
    machine-independent — no path convention to hardcode. Never raises;
    returns ``None`` on any failure (not installed, not editable, no
    ``pyproject.toml`` found within a few parent directories).
    """
    try:
        proc = subprocess.run(  # noqa: S603 - a python interpreter path we resolved ourselves
            [python_executable, "-c", f"import {module_name}; print({module_name}.__file__)"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    current = Path(proc.stdout.strip()).resolve().parent
    for _ in range(5):
        if (current / "pyproject.toml").is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


__all__ = [
    "ScriptCandidate",
    "candidate_env_dirs",
    "epics_ca_env",
    "find_console_script",
    "interpreter_for",
    "resolve_ca_addr_list",
    "resolve_editable_source_root",
    "script_filename",
]
