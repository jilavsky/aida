"""Find pyIrena's MCP server on this machine and configure it in one step.

Why this module exists: pyIrena is AIDA's primary target MCP server — the
beamline audience installs both — and "configure an MCP server" is the
single hardest thing a new user faces. Doing it by hand means knowing that
`pyirena-mcp` is a console script, finding its *absolute* path (a GUI app
on macOS/Windows does not inherit a shell `PATH`, so a bare name fails),
knowing that `PYIRENA_DATA_ROOT` is worth setting, and knowing which group
and skills to attach. None of that is discoverable. All of it is
mechanical, so AIDA does it.

Deliberately an **offer, never automatic**: an MCP server is code AIDA
launches as a subprocess on the user's machine, and every other trust
decision in AIDA (secrets, confirm-before-run, allowed folders) is an
explicit opt-in. `find_pyirena_mcp` only looks; `pyirena_server_config`
only builds a config object. Writing it to `mcp.json` is the caller's
(and therefore the user's) action — `aida mcp add-pyirena`, the MCP
dialog's "Add pyIrena…" button, or the onboarding dialog.

Search order matters, and is "most likely to be the one you meant" first:

1. **AIDA's own environment** (`sys.executable`'s directory). A user who
   ran `pip install aida-workbench pyirena[mcp]` into one environment gets
   this, and it is the only candidate guaranteed to stay in step with the
   AIDA install.
2. **`PATH`** — the active conda env in a terminal-launched AIDA.
3. **`python -m pyirena.mcp.server`** using AIDA's own interpreter, when
   `pyirena` imports but no console script is on disk (an editable install
   whose scripts were never linked, a `--no-scripts` install).
4. **Sibling conda/mamba environments** — the common beamline layout is
   pyIrena in its own env (`~/miniconda3/envs/pyirena`) precisely because
   its dependency set is heavy. AIDA talks to it over stdio, so the two
   never have to share an interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from aida.config.logging_setup import get_logger
from aida.config.settings import McpServerConfig

logger = get_logger("pyirena-setup")

#: Default server name written into ``mcp.json``. Matches what pyIrena's own
#: docs use for every other client, so a user comparing configs sees the
#: same name everywhere.
DEFAULT_SERVER_NAME = "pyirena"

#: Group the server is put in. A group (rather than "always on") is the
#: point: pyirena-mcp exposes ~70 tools, which overloads a small local
#: model when the task has nothing to do with scattering data.
DEFAULT_GROUP = "pyirena-analysis"

#: Skills attached to the server, auto-included whenever it is enabled.
#: Both ship with AIDA (``aida/resources/skills``) and are installed into
#: ``~/.aida/skills/`` by ``aida.config.paths.install_bundled_skills``.
DEFAULT_SKILLS = ("saxs-basics", "pyirena-usage")

_SCRIPT_NAME = "pyirena-mcp.exe" if os.name == "nt" else "pyirena-mcp"
_BIN_DIRNAME = "Scripts" if os.name == "nt" else "bin"


@dataclass(frozen=True)
class PyirenaMcpCandidate:
    """One way to launch pyirena-mcp that was actually found on disk.

    ``command`` + ``args`` go straight into ``McpServerConfig``. ``source``
    is a short human-readable phrase for a CLI line or a dialog row
    ("AIDA's own environment", "PATH", "conda env 'pyirena'"), so a user
    choosing between two candidates can tell which is which without
    parsing paths.
    """

    command: str
    args: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def display(self) -> str:
        joined = " ".join([self.command, *self.args])
        return f"{joined}  ({self.source})" if self.source else joined


def _candidate_env_dirs() -> list[Path]:
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
    # The Windows installers for Miniforge/Miniconda/Anaconda default to
    # %LOCALAPPDATA% (and %PROGRAMDATA% for an all-users install), *not* the
    # home directory the POSIX installers use. Omitting these meant the
    # scan below found nothing at all on a stock Windows beamline machine —
    # the exact layout this function exists to cover — and `add-pyirena`
    # silently fell back to whatever happened to be in AIDA's own env.
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMDATA")):
        if base:
            roots += [
                Path(base) / name / "envs"
                for name in ("miniforge3", "miniconda3", "anaconda3", "mambaforge")
            ]
    # CONDA_PREFIX points at the *active* env; its parent is the envs dir
    # for any non-base env, which covers installs in unusual locations.
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        roots.append(Path(conda_prefix).parent)
    return roots


def _pyirena_importable() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("pyirena.mcp.server") is not None
    except (ImportError, ValueError):
        return False


def find_pyirena_mcp() -> list[PyirenaMcpCandidate]:
    """Every way to launch pyirena-mcp found on this machine, best first.

    Never raises and never launches anything — a missing home directory, an
    unreadable envs folder, or a stale symlink is skipped. An empty list
    means "not found", which is a perfectly normal state (pyIrena not
    installed) and not an error.
    """
    candidates: list[PyirenaMcpCandidate] = []
    seen: set[str] = set()

    def add(command: str | Path, source: str, args: list[str] | None = None) -> None:
        key = f"{command}|{' '.join(args or [])}"
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            PyirenaMcpCandidate(command=str(command), args=list(args or []), source=source)
        )

    own_bin = Path(sys.executable).parent / _SCRIPT_NAME
    own_script_found = own_bin.is_file()
    if own_script_found:
        add(own_bin, "AIDA's own environment")

    on_path = shutil.which("pyirena-mcp")
    if on_path:
        add(Path(on_path).resolve(), "PATH")

    # Only a *fallback*: when the console script from AIDA's own environment
    # exists, `python -m` there launches exactly the same server from the
    # same interpreter, so offering both would make every ordinary
    # same-environment install look like an ambiguous two-way choice the
    # user has to resolve. This form earns its place only when the script
    # is missing — an editable install whose entry points were never
    # linked, or a `--no-scripts` install.
    if not own_script_found and _pyirena_importable():
        add(sys.executable, "AIDA's own environment (python -m)", ["-m", "pyirena.mcp.server"])

    for envs_dir in _candidate_env_dirs():
        try:
            if not envs_dir.is_dir():
                continue
            entries = sorted(envs_dir.iterdir())
        except OSError:  # unreadable, or a path that vanished mid-scan
            continue
        for env in entries:
            script = env / _BIN_DIRNAME / _SCRIPT_NAME
            try:
                if script.is_file():
                    add(script, f"conda env {env.name!r}")
            except OSError:
                continue

    logger.debug("pyirena-mcp candidates: %s", [c.display for c in candidates])
    return candidates


def _interpreter_for(command: str, args: list[str]) -> str | None:
    """The Python that will run this launch form, or ``None`` if not on disk.

    A console script lives next to the interpreter that will run it — on
    POSIX. On Windows only a *venv* puts ``python.exe`` in ``Scripts\\``; a
    conda prefix keeps it one level up, at the root beside ``Scripts\\``.
    Looking only in the script's own directory therefore found nothing for
    every conda install, which made ``pyirena_version`` silently answer
    ``None`` (the version was simply omitted from the doctor row) and would
    have made ``import_failure`` skip the check it exists to perform.
    """
    if args[:1] == ["-m"]:
        return command
    bin_dir = Path(command).parent
    name = "python.exe" if os.name == "nt" else "python"
    search = [bin_dir, bin_dir.parent] if os.name == "nt" else [bin_dir]
    for directory in search:
        python = directory / name
        if python.exists():
            return str(python)
    return None


def pyirena_version(candidate: PyirenaMcpCandidate) -> str | None:
    """The pyIrena version behind a candidate, or ``None`` if it can't be
    determined. Runs the candidate's *interpreter*, never the MCP server
    itself — starting the server would open a stdio session this function
    has no business owning. Best-effort and short-timeout: a wedged or
    broken install reports ``None`` rather than hanging a doctor run."""
    python = _interpreter_for(candidate.command, candidate.args)
    if python is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - a python interpreter path we resolved ourselves
            [python, "-c", "import pyirena; print(pyirena.__version__)"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def import_failure(config: McpServerConfig) -> str | None:
    """Why a *configured* pyIrena server would fail to start, or ``None``.

    Checking that ``mcp.json`` names a file that exists says almost
    nothing: the failure this catches is a server whose command is
    perfectly present and still cannot run, because it lives in a
    different conda environment than AIDA and its ``PATH`` does not reach
    that environment's shared libraries (see ``_launch_path``). On Windows
    that kills CPython at import with exit 127 and an empty stderr, so
    there is no diagnostic to surface from a failed launch — the only way
    to know is to try the import under the environment the server is
    actually configured with, which is what this does.

    Runs the interpreter, not the server, for the same reason
    ``pyirena_version`` does. Returns a one-line reason suitable for a
    doctor row, or ``None`` when the import succeeds or when there is
    nothing meaningful to test (no interpreter found next to the command —
    a wrapper script or a non-Python server, neither of which this check
    has any business grading).
    """
    python = _interpreter_for(config.command, list(config.args))
    if python is None:
        return None
    env = {
        **os.environ,
        **{k: v for k, v in config.env.items() if not v.startswith(("keyring:", "secret:"))},
    }
    try:
        proc = subprocess.run(  # noqa: S603 - a python interpreter path we resolved ourselves
            [python, "-c", "import pyirena.mcp.server"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "importing pyirena.mcp.server timed out after 30s"
    except OSError as exc:
        return f"could not run {python}: {exc}"
    if proc.returncode == 0:
        return None
    detail = (proc.stderr or "").strip().splitlines()
    if detail:
        return f"{detail[-1]} (exit {proc.returncode})"
    # The silent-DLL-failure case: no traceback at all to report, so say
    # what it almost always means instead of printing an empty string.
    return (
        f"exited {proc.returncode} with no error output — usually a shared library that "
        "cannot be found, meaning the server's PATH does not reach its own environment"
    )


def _env_root(candidate: PyirenaMcpCandidate) -> Path | None:
    """The environment prefix a candidate launches from, or ``None``.

    A console script lives in ``<prefix>/bin`` (``<prefix>\\Scripts`` on
    Windows); the ``python -m`` form's command *is* ``<prefix>/bin/python``
    on POSIX and ``<prefix>\\python.exe`` on Windows.
    """
    command = Path(candidate.command)
    parent = command.parent
    if os.name == "nt" and parent.name.lower() != _BIN_DIRNAME.lower():
        # `<prefix>\python.exe` — the interpreter sits at the prefix root.
        return parent if (parent / _BIN_DIRNAME).is_dir() else None
    return parent.parent if parent.name == _BIN_DIRNAME else None


def _launch_path(candidate: PyirenaMcpCandidate) -> str | None:
    """A ``PATH`` that lets ``candidate`` actually load, or ``None``.

    Why this is needed at all: AIDA and pyIrena usually live in *separate*
    conda environments (the whole point of talking over stdio — see this
    module's docstring, search order 4). The MCP SDK builds the server
    subprocess's environment from ``get_default_environment()``, which
    inherits **AIDA's** ``PATH``, so pyIrena's env never appears on it. On
    Windows that is fatal and nearly undebuggable: conda keeps its shared
    libraries in ``<prefix>\\Library\\bin``, so an extension module's DLL
    fails to load, CPython dies at import with exit 127 and *no traceback
    on stderr*, and the client sees only "unhandled errors in a TaskGroup
    (1 sub-exception)" wrapping a bare "Connection closed".

    The value is a snapshot taken now, at configure time, rather than a
    reference resolved at launch: it goes into ``mcp.json`` as a literal a
    user can read and edit, which matters because this is the setting they
    will have to adjust by hand if they ever move the environment. The
    prefix directories go *first* so pyIrena's env wins over AIDA's own.
    """
    prefix = _env_root(candidate)
    if prefix is None:
        return None
    if os.name == "nt":
        # Mirrors what `conda activate` prepends on Windows.
        parts = [
            prefix,
            prefix / "Library" / "mingw-w64" / "bin",
            prefix / "Library" / "usr" / "bin",
            prefix / "Library" / "bin",
            prefix / "Scripts",
            prefix / "bin",
        ]
    else:
        parts = [prefix / "bin"]
    inherited = os.environ.get("PATH", "")
    joined = os.pathsep.join(str(p) for p in parts)
    return f"{joined}{os.pathsep}{inherited}" if inherited else joined


def pyirena_server_config(
    candidate: PyirenaMcpCandidate,
    *,
    name: str = DEFAULT_SERVER_NAME,
    data_root: str | None = None,
    group: str = DEFAULT_GROUP,
    skills: tuple[str, ...] | list[str] = DEFAULT_SKILLS,
    max_array_points: int | None = 500,
) -> McpServerConfig:
    """Build the ``McpServerConfig`` for one found candidate.

    ``data_root`` sets ``PYIRENA_DATA_ROOT``, which restricts every file
    pyirena-mcp will touch to that subtree — pyIrena's own docs call it
    "strongly recommended when exposing the server to an AI agent", and a
    workspace's source folder is exactly the right value. Left unset when
    ``None``, matching pyIrena's default of "any absolute path accepted".

    ``max_array_points`` sets ``PYIRENA_MAX_ARRAY_POINTS``, the decimation
    cap on arrays returned in tool responses. The default of 500 is
    pyIrena's own; it is set explicitly rather than left to the default
    because it is the one knob that controls how much context a single
    tool result can eat, and a user tuning it should find it already in
    their ``mcp.json`` instead of having to learn it exists.

    ``PATH`` is set from the candidate's own environment prefix when one
    can be identified — see ``_launch_path`` for why a cross-environment
    stdio server does not otherwise start on Windows.
    """
    env: dict[str, str] = {}
    launch_path = _launch_path(candidate)
    if launch_path:
        env["PATH"] = launch_path
    if data_root:
        env["PYIRENA_DATA_ROOT"] = str(Path(data_root).expanduser())
    if max_array_points is not None:
        env["PYIRENA_MAX_ARRAY_POINTS"] = str(max_array_points)
    return McpServerConfig(
        name=name,
        command=candidate.command,
        args=list(candidate.args),
        env=env,
        groups=[group] if group else [],
        skills=list(skills),
    )


__all__ = [
    "DEFAULT_GROUP",
    "DEFAULT_SERVER_NAME",
    "DEFAULT_SKILLS",
    "PyirenaMcpCandidate",
    "find_pyirena_mcp",
    "import_failure",
    "pyirena_server_config",
    "pyirena_version",
]
