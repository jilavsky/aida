"""Turn a PySide6 ``ImportError`` into actionable guidance, shared by
``aida doctor`` (``aida.cli.doctor._check_gui``) and ``aida-gui``
(``aida.cli.__main__.main_gui``) so both give identical advice.

Pure stdlib, no Qt import — safe to import at module level from
``aida.cli.__main__``, which must otherwise stay importable without
PySide6 installed (see that module's docstring).

Two failure modes both surface as ``ImportError`` from the same
``from aida.ui.qt.app import main`` line but need opposite fixes: a missing
system Qt library (install a distro package) versus the installed PySide6
wheel needing a newer glibc than the OS has (a distro package cannot supply
a missing glibc symbol version — the fix is pinning PySide6 down to a
release built against an older glibc baseline). Telling the glibc case to
"install libGL" sends the user nowhere, so the two are distinguished by
sniffing the exception text for glibc's characteristic
``GLIBC_X.Y' not found`` message.
"""

from __future__ import annotations

import re

_GLIBC_RE = re.compile(r"GLIBC_(\d+\.\d+)")

_INSTALLATION_DOC = "docs/installation.md#gui-fails-to-import-on-headless-linux"


def diagnose_pyside6_import_error(exc: BaseException) -> str:
    message = str(exc)
    glibc_match = _GLIBC_RE.search(message)
    if glibc_match:
        return (
            f"PySide6 installed but failed to import ({message}) — this PySide6 "
            f"release needs glibc {glibc_match.group(1)}+, newer than this "
            "system's glibc (check with `ldd --version`). No system package "
            "fixes a missing glibc symbol — pin PySide6 down to an older "
            "release built against this system's glibc instead (if pyIrena's "
            "GUI already runs here, `pip show PySide6` in its environment "
            "names a version known to work; otherwise try "
            '`pip install "PySide6==<version>"` one release back at a time). '
            f"See {_INSTALLATION_DOC}."
        )
    return (
        f"PySide6 installed but failed to import ({message}) — on Linux this "
        "usually means a system Qt library is missing (libGL, libxkbcommon, "
        f"xcb, ...). See {_INSTALLATION_DOC}."
    )
