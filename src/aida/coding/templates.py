"""Code templates (Phase 9): "template-based generation of small instrument
functions" — a per-workspace folder of plain ``.py`` files with docstrings
(the BeamlineAdvisor pattern), surfaced to the model as a compact list of
name + docstring (not full source, keeping the context addition small, per
the phase file's own "surfaced... small sets") via ``ChatSession``'s
existing ``extra_context_texts`` mechanism — the same slot
``build_workspace_context_block``'s output already goes into
(``aida.cli.chat.start_session``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Template:
    name: str
    path: Path
    docstring: str | None
    source: str


def load_templates(templates_dir: Path) -> list[Template]:
    """Every ``.py`` file directly under ``templates_dir`` (non-recursive —
    a flat folder of templates, matching the phase file's "plain .py files
    with docstrings", not a package tree). A file that fails to parse (a
    syntax error, an encoding issue) is skipped, not raised — one bad
    template must not break every other template or the whole session
    start, the same "one bad X must not take down the whole Y" reasoning
    already applied elsewhere (a bad MCP server, a bad ingest file)."""
    if not templates_dir.is_dir():
        return []
    templates: list[Template] = []
    for path in sorted(templates_dir.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            docstring = ast.get_docstring(ast.parse(source))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        templates.append(Template(name=path.stem, path=path, docstring=docstring, source=source))
    return templates


def templates_context_text(templates: list[Template]) -> str:
    """Formats the compact "available templates" block injected into the
    system message — empty string (no block at all) for no templates,
    matching how ``build_workspace_context_block`` already omits itself
    when a workspace has nothing to report."""
    if not templates:
        return ""
    lines = ["# Available code templates", "", "Follow these templates when generating instrument functions:"]
    for template in templates:
        docstring = template.docstring or "(no docstring)"
        lines.append(f"- **{template.name}**: {docstring}")
    return "\n".join(lines)


__all__ = ["Template", "load_templates", "templates_context_text"]
