"""Group a server's tool names into categories for the Tools tab.

planning/mcp_tool_scaling.md (Tier 1): pyIrena-mcp alone can expose ~110
tools, and curating `McpServerConfig.disabled_tools` one checkbox per tool
in `McpManagementDialog`'s Tools tab doesn't scale past a handful.

This is a *generic* longest-common-prefix-then-bucket algorithm over each
name's `_`-separated tokens, not a `pyirena_ctrl_` regex — it produces
sensible categories for any MCP server that names its tools
`<namespace>_<category>_<verb>`-style, and quietly degrades to "no
grouping" for a small server or one with no shared token structure (see
the module-level constants below for the thresholds that decide which).
"""

from __future__ import annotations

from dataclasses import dataclass

#: At or below this many tools, grouping buys nothing over the existing
#: flat list — return a single ungrouped section instead.
MIN_TOOLS_TO_GROUP = 12

#: A bucket smaller than this merges into that level's "Other" catch-all
#: rather than becoming its own one- or two-tool "category".
MIN_GROUP_SIZE = 2

#: How many token-levels deep bucketing will recurse into an oversized
#: bucket (e.g. pyIrena's ~74 `pyirena_ctrl_*` tools split once more into
#: `ctrl/waxs`, `ctrl/sizes`, ...). Capped so the Tools tab never grows a
#: category tree deeper than "category / sub-category".
MAX_GROUP_DEPTH = 2

#: Name given to the leftover bucket at any level once small groups are
#: merged together.
OTHER_GROUP_NAME = "Other"


@dataclass
class ToolGroup:
    """One section of the Tools tab: a category name (``None`` only for
    the "no grouping happened" case, rendered with no header at all) and
    the tool names it contains, sorted."""

    name: str | None
    tools: list[str]


def _tokens(name: str) -> list[str]:
    return name.split("_")


def _common_prefix_len(token_lists: list[list[str]]) -> int:
    """How many leading tokens every name in ``token_lists`` shares."""
    if not token_lists:
        return 0
    shortest = min(len(tokens) for tokens in token_lists)
    length = 0
    while length < shortest and len({tokens[length] for tokens in token_lists}) == 1:
        length += 1
    return length


def _bucket_by_next_token(names: list[str], prefix_len: int) -> dict[str, list[str]]:
    """Group ``names`` by the token immediately after their shared
    ``prefix_len``-token prefix. A name with nothing left after the prefix
    (the prefix *is* the whole name) keys under its own full name, so it
    never silently vanishes."""
    buckets: dict[str, list[str]] = {}
    for name in names:
        tokens = _tokens(name)
        key = tokens[prefix_len] if len(tokens) > prefix_len else name
        buckets.setdefault(key, []).append(name)
    return buckets


def _merge_small_buckets(buckets: dict[str, list[str]]) -> dict[str, list[str]]:
    """Buckets under ``MIN_GROUP_SIZE`` collapse into one trailing
    ``OTHER_GROUP_NAME`` bucket, so a long tail of one-off tools doesn't
    produce a wall of one-item categories."""
    kept: dict[str, list[str]] = {}
    leftover: list[str] = []
    for key, tools in buckets.items():
        if len(tools) < MIN_GROUP_SIZE:
            leftover.extend(tools)
        else:
            kept[key] = tools
    if leftover:
        kept[OTHER_GROUP_NAME] = sorted(kept.get(OTHER_GROUP_NAME, []) + leftover)
    return kept


def _group(names: list[str], *, label_prefix: str, depth: int) -> list[ToolGroup]:
    prefix_len = _common_prefix_len([_tokens(n) for n in names])
    buckets = _merge_small_buckets(_bucket_by_next_token(names, prefix_len))

    groups: list[ToolGroup] = []
    for key, tools in buckets.items():
        label = f"{label_prefix}/{key}" if label_prefix else key
        if key != OTHER_GROUP_NAME and depth < MAX_GROUP_DEPTH and len(tools) > MIN_TOOLS_TO_GROUP:
            groups.extend(_group(tools, label_prefix=label, depth=depth + 1))
        else:
            groups.append(ToolGroup(name=label, tools=sorted(tools)))
    groups.sort(key=lambda g: g.name or "")
    return groups


def group_tool_names(names: list[str]) -> list[ToolGroup]:
    """Split ``names`` into categories for the Tools tab.

    Returns a single ``ToolGroup(name=None, ...)`` — rendered with no
    header, identical to today's flat list — whenever there are too few
    tools for grouping to be worth the extra chrome, or whenever the names
    have no shared token structure to bucket by (e.g. `tool_0`..`tool_149`,
    where every name is its own leaf once the shared `tool` prefix is
    stripped, collapses back to a single group here).
    """
    if len(names) <= MIN_TOOLS_TO_GROUP:
        return [ToolGroup(name=None, tools=sorted(names))]

    groups = _group(names, label_prefix="", depth=1)
    if len(groups) <= 1:
        return [ToolGroup(name=None, tools=sorted(names))]
    return groups


__all__ = [
    "MAX_GROUP_DEPTH",
    "MIN_GROUP_SIZE",
    "MIN_TOOLS_TO_GROUP",
    "OTHER_GROUP_NAME",
    "ToolGroup",
    "group_tool_names",
]
