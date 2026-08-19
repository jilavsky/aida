"""MCP manager: stdio servers, groups, typed result artifacts, skills linkage.
Never imports Qt.

See ``aida.mcp.server`` (one stdio server handle), ``aida.mcp.results`` (MCP
content -> typed artifact conversion), ``aida.mcp.groups`` (which servers are
active for a session), and ``aida.mcp.manager`` (wires the above into
agent-loop-compatible ``NativeTool``s).
"""

from aida.mcp.groups import resolve_explicit, resolve_group
from aida.mcp.manager import McpManager
from aida.mcp.results import convert_result
from aida.mcp.server import McpServerError, McpServerHandle

__all__ = [
    "McpManager",
    "McpServerError",
    "McpServerHandle",
    "convert_result",
    "resolve_explicit",
    "resolve_group",
]
