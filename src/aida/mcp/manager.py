"""Coordinates a set of MCP servers for one AIDA session.

``McpManager`` takes an already-resolved list of server configs (see
``aida.mcp.groups``), launches only those (lazy start — a server not in the
active group/list is never spawned), namespaces every discovered tool as
``server.tool`` so name collisions across servers can't happen, and wraps
each as an agent-loop-compatible ``NativeTool``. Every call result is run
through ``aida.mcp.results.convert_result`` so images/files reach the agent
loop as typed artifacts, never flattened text (PLAN.md hard rule 3) — this
is the piece that actually plugs the MCP layer into ``aida.core.agent``.
"""

from __future__ import annotations

from typing import Any

from mcp.types import Tool as McpTool

from aida.artifacts.base import Artifact, FileArtifact, ImageArtifact
from aida.artifacts.policy import describe_for_model
from aida.artifacts.store import ArtifactStore
from aida.config.settings import McpServerConfig
from aida.core.tools import NativeTool, ToolResult
from aida.mcp.results import convert_result
from aida.mcp.server import McpServerError, McpServerHandle
from aida.providers.base import ToolSchema

NAMESPACE_SEPARATOR = "."


def namespaced_tool_name(server_name: str, tool_name: str) -> str:
    return f"{server_name}{NAMESPACE_SEPARATOR}{tool_name}"


class McpManager:
    """Owns the ``McpServerHandle`` for every enabled server in one session."""

    def __init__(
        self,
        servers: list[McpServerConfig],
        *,
        artifact_store: ArtifactStore | None = None,
        call_timeout_seconds: float | None = None,
    ) -> None:
        self._configs = {s.name: s for s in servers}
        self._handles: dict[str, McpServerHandle] = {}
        self._store = artifact_store or ArtifactStore()
        self._call_timeout_seconds = call_timeout_seconds
        self.start_errors: dict[str, str] = {}

    @property
    def enabled_server_names(self) -> list[str]:
        return list(self._configs.keys())

    @property
    def running_server_names(self) -> list[str]:
        return list(self._handles.keys())

    def skills(self) -> list[str]:
        """Union of every enabled server's ``skills:`` list, config order,
        de-duplicated — "enabling a server auto-includes those skills files
        in the system context" (planning/phase03_mcp.md)."""
        seen: list[str] = []
        for config in self._configs.values():
            for skill in config.skills:
                if skill not in seen:
                    seen.append(skill)
        return seen

    async def start_all(self) -> dict[str, NativeTool]:
        """Launch every enabled server and return namespaced ``NativeTool``s
        ready to merge into an ``AgentLoop``'s tool set.

        Failure isolation applies at startup too: a server that fails to
        launch is recorded in ``start_errors`` and simply contributes no
        tools, rather than aborting the whole chat session over one bad MCP
        server config.
        """
        tools: dict[str, NativeTool] = {}
        for name, config in self._configs.items():
            handle_kwargs: dict[str, Any] = {}
            if self._call_timeout_seconds is not None:
                handle_kwargs["call_timeout_seconds"] = self._call_timeout_seconds
            handle = McpServerHandle(config, **handle_kwargs)
            try:
                mcp_tools = await handle.start()
            except McpServerError as exc:
                self.start_errors[name] = str(exc)
                continue
            self._handles[name] = handle
            for tool in mcp_tools:
                tools[namespaced_tool_name(name, tool.name)] = self._build_native_tool(name, tool)
        return tools

    def _build_native_tool(self, server_name: str, tool: McpTool) -> NativeTool:
        schema = ToolSchema(
            name=namespaced_tool_name(server_name, tool.name),
            description=tool.description or "",
            parameters=tool.inputSchema or {"type": "object", "properties": {}},
        )
        tool_name = tool.name

        async def _call(arguments: dict[str, Any]) -> ToolResult:
            return await self._call_tool(server_name, tool_name, arguments)

        return NativeTool(schema=schema, func=_call)

    async def _call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        handle = self._handles.get(server_name)
        if handle is None:
            return ToolResult(content=f"mcp server {server_name!r} is not running", is_error=True)

        try:
            result = await handle.call_tool(tool_name, arguments)
        except McpServerError as exc:
            return ToolResult(content=str(exc), is_error=True)

        artifacts = [self._persist(a) for a in convert_result(result)]
        text = "\n".join(describe_for_model(a) for a in artifacts) if artifacts else ""
        return ToolResult(content=text, is_error=bool(result.isError), artifacts=artifacts)

    def _persist(self, artifact: Artifact) -> Artifact:
        """Save binary artifacts to the artifact store immediately so a
        ``path`` is available for ``ImageArtifactCreated``/
        ``FileArtifactCreated`` events. Artifacts with no bytes to write
        (e.g. a bare ``ResourceLink``) pass through unchanged."""
        if isinstance(artifact, ImageArtifact):
            return self._store.save_image(artifact)
        if isinstance(artifact, FileArtifact) and artifact.data is not None:
            return self._store.save_file(artifact)
        return artifact

    async def aclose(self) -> None:
        for handle in self._handles.values():
            await handle.stop()
        self._handles = {}


__all__ = ["McpManager", "namespaced_tool_name"]
