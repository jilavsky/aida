"""Links from the application to its online documentation.

Copied from jilavsky-docs/templates/package-docs/pkg/_docs.py; place it at
``<package>/_docs.py``. No Qt imports here: the GUI calls
``QDesktopServices.openUrl(QUrl(docs_url("unified_fit")))`` itself.

The page names in HELP_TARGETS are a contract with released copies of the
program. Never rename a page they point to; add a redirect instead.
tests/test_help_targets.py checks that every target exists.
"""

from importlib.metadata import PackageNotFoundError, version

DIST = "aida-workbench"  # PyPI distribution name
SUBPROJECT = "aida"  # RTD subproject alias under jilavsky-docs
_BASE = f"https://jilavsky-docs.readthedocs.io/projects/{SUBPROJECT}/en/{{ver}}/"

# GUI id -> page path relative to docs/, without suffix ("" = home page).
HELP_TARGETS: dict[str, str] = {
    "home": "",
    "providers": "reference/providers-and-secrets",
}

# Explicit MyST labels, "(label)=", that the code links to with an anchor.
# GUI id -> (page path, label)
HELP_ANCHORS: dict[str, tuple[str, str]] = {
    "secrets": ("reference/providers-and-secrets", "secrets"),
    "gui_import_headless": (
        "getting-started/installation",
        "gui-fails-to-import-on-headless-linux",
    ),
}


def docs_version() -> str:
    """Return the RTD version to open: "stable" for releases, "latest" otherwise.

    Versions containing "dev", "+", "a", "b" or "rc" open "latest", which is
    built from the main branch. An editable checkout of main reports the last
    release number and therefore opens "stable" (accepted 2026-11-13, PLAN.md §6).
    """
    try:
        v = version(DIST)
    except PackageNotFoundError:
        return "latest"
    is_release = v.replace(".", "").isdigit()
    return "stable" if is_release else "latest"


def docs_url(target: str = "home", anchor: str = "") -> str:
    """Return the documentation URL for a GUI component.

    Args:
        target: Key in HELP_TARGETS or HELP_ANCHORS.
        anchor: Optional explicit label on the page (ignored for HELP_ANCHORS keys).

    Returns:
        Absolute URL on Read the Docs.
    """
    if target in HELP_ANCHORS:
        page, anchor = HELP_ANCHORS[target]
    else:
        page = HELP_TARGETS[target]
    url = _BASE.format(ver=docs_version())
    if page:
        url += page + ".html"
    return url + (f"#{anchor}" if anchor else "")
