"""The version lives in two files, and nothing derives one from the other.

`pyproject.toml`'s `version` is what PyPI publishes; `aida.__version__` is
what the running program reports — `aida --version`, `aida doctor`, the
About box, and the `aida_version` field of a workflow run manifest. A
release that ships with the two disagreeing cannot be corrected after the
fact, because PyPI will not let a version be re-uploaded. The publish
workflow checks this too (and checks the git tag against it, which no test
can); this is the copy that fails in a second on every local run, long
before a tag exists.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import aida

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_pyproject_and_dunder_version_agree():
    assert aida.__version__ == _pyproject_version()


def test_version_is_pep440_release_or_prerelease():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[ab]\d+|rc\d+)?", aida.__version__), aida.__version__


def test_readme_status_line_names_the_current_version():
    """README's status banner is the first thing a visitor reads; a stale
    one claims a release that is not what `pip install` gives them."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    banner = next(line for line in readme.splitlines() if line.startswith("> **Status:"))
    assert aida.__version__ in banner, banner
