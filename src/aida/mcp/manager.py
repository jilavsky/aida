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

import time
from dataclasses import dataclass
from typing import Any

from mcp.types import Tool as McpTool

from aida.artifacts.base import Artifact, FileArtifact, ImageArtifact
from aida.artifacts.policy import describe_for_model
from aida.artifacts.store import ArtifactStore
from aida.config.settings import McpServerConfig
from aida.core.confirmation import ConfirmationRequest, ConfirmCallback, deny_all
from aida.core.tools import NativeTool, ToolResult
from aida.mcp.results import convert_result
from aida.mcp.server import McpServerError, McpServerHandle, ToolCallRecord
from aida.providers.base import ToolSchema

NAMESPACE_SEPARATOR = "."


def namespaced_tool_name(server_name: str, tool_name: str) -> str:
    return f"{server_name}{NAMESPACE_SEPARATOR}{tool_name}"


@dataclass
class ConnectionTestResult:
    """Phase 7's "Test connection" button: initialize + list tools, report
    timing — without disturbing a server that's already running."""

    ok: bool
    tool_count: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None


class McpManager:
    """Owns the ``McpServerHandle`` for every enabled server in one session.

    ``confirm_callback`` (Phase 7) gates any tool a server config marks
    ``confirm_tools`` — the same generic human-in-the-loop channel
    ``aida.workspace.safety.SafetyGuard`` already uses for file operations
    (see ``aida.core.confirmation``'s docstring for why they share one
    module), so a confirm-flagged MCP tool call pops the same GUI modal /
    CLI prompt with zero new UI plumbing. Independent of any workspace's
    safety mode — a bait_mcp instrument-write tool marked confirm-required
    still asks even inside a "relaxed" workspace.
    """

    def __init__(
        self,
        servers: list[McpServerConfig],
        *,
        artifact_store: ArtifactStore | None = None,
        call_timeout_seconds: float | None = None,
        confirm_callback: ConfirmCallback = deny_all,
    ) -> None:
        self._configs = {s.name: s for s in servers}
        self._handles: dict[str, McpServerHandle] = {}
        self._store = artifact_store or ArtifactStore()
        self._call_timeout_seconds = call_timeout_seconds
        self._confirm_callback = confirm_callback
        self.start_errors: dict[str, str] = {}

    @property
    def enabled_server_names(self) -> list[str]:
        return list(self._configs.keys())

    @property
    def running_server_names(self) -> list[str]:
        return list(self._handles.keys())

    def tool_names(self, server_name: str) -> list[str]:
        """Unnamespaced tool names discovered on a *running* server, or an
        empty list if it isn't running — the Tools tab in the MCP
        management dialog uses this to show what a live server actually
        exposes, without reaching into ``McpManager``'s own handle dict."""
        handle = self._handles.get(server_name)
        return [t.name for t in handle.list_tools()] if handle is not None else []

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
            handle = McpServerHandle(config, **self._handle_kwargs())
            try:
                mcp_tools = await handle.start()
            except McpServerError as exc:
                self.start_errors[name] = str(exc)
                continue
            self._handles[name] = handle
            tools.update(self._tools_for(name, config, mcp_tools))
        return tools

    def _handle_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._call_timeout_seconds is not None:
            kwargs["call_timeout_seconds"] = self._call_timeout_seconds
        return kwargs

    def _tools_for(
        self, server_name: str, config: McpServerConfig, discovered_tools: list[McpTool]
    ) -> dict[str, NativeTool]:
        """Namespaced tools for one server's discovered tool list, excluding
        anything in ``config.disabled_tools`` — a disabled tool's schema is
        never sent to the model, not merely refused if called (Phase 7:
        "Per-tool disable respected in tool schemas sent to provider")."""
        disabled = set(config.disabled_tools)
        return {
            namespaced_tool_name(server_name, tool.name): self._build_native_tool(server_name, tool)
            for tool in discovered_tools
            if tool.name not in disabled
        }

    # --- live per-server control (Phase 7: start/stop/restart from the GUI
    # without restarting the whole chat session) -------------------------

    async def start_server(self, name: str) -> dict[str, NativeTool]:
        """Start one already-configured server (register it first with
        ``add_server_config`` if it's brand new) and return its namespaced
        tools. Idempotent: a server already running just returns its
        current tools rather than restarting. Raises ``McpServerError`` on
        failure — unlike ``start_all``'s failure isolation across many
        servers, this is one explicit user action; the caller (CLI/GUI)
        decides how to surface the failure."""
        config = self._configs.get(name)
        if config is None:
            raise McpServerError(f"mcp server {name!r} is not configured")
        if name in self._handles:
            return self._tools_for(name, config, self._handles[name].list_tools())

        handle = McpServerHandle(config, **self._handle_kwargs())
        try:
            mcp_tools = await handle.start()
        except McpServerError as exc:
            self.start_errors[name] = str(exc)
            raise
        self._handles[name] = handle
        self.start_errors.pop(name, None)
        return self._tools_for(name, config, mcp_tools)

    async def stop_server(self, name: str) -> None:
        """Stop one running server. A no-op if it isn't running."""
        handle = self._handles.pop(name, None)
        if handle is not None:
            await handle.stop()

    async def restart_server(self, name: str) -> dict[str, NativeTool]:
        """Recovery path after a crashed/wedged server, or just "reload its
        tool list" — stop (if running) then start again."""
        if name not in self._configs:
            raise McpServerError(f"mcp server {name!r} is not configured")
        await self.stop_server(name)
        return await self.start_server(name)

    def add_server_config(self, config: McpServerConfig) -> None:
        """Register a new server config (or replace an existing one by
        name) without starting it — lazy start still applies; call
        ``start_server(config.name)`` to actually launch it. Lets the GUI's
        "Add Server" dialog take effect in the *current* session, not only
        after a restart."""
        self._configs[config.name] = config

    async def remove_server_config(self, name: str) -> None:
        """Stop the server first if it's running, then forget its config
        entirely — used when the GUI's "Remove" action is confirmed."""
        await self.stop_server(name)
        self._configs.pop(name, None)
        self.start_errors.pop(name, None)

    async def test_connection(self, config: McpServerConfig) -> ConnectionTestResult:
        """"Test connection" button: initialize + list tools, report
        timing. Reuses an already-running handle for the same name
        instantly (spinning up a second subprocess against a server that's
        already connected risks confusing a stdio server expecting one
        client); otherwise launches and tears down a temporary handle
        purely to measure reachability, without registering it anywhere."""
        existing = self._handles.get(config.name)
        if existing is not None:
            return ConnectionTestResult(ok=True, tool_count=len(existing.list_tools()), elapsed_seconds=0.0)

        handle = McpServerHandle(config, **self._handle_kwargs())
        start = time.monotonic()
        try:
            tools = await handle.start()
        except McpServerError as exc:
            return ConnectionTestResult(ok=False, elapsed_seconds=time.monotonic() - start, error=str(exc))
        elapsed = time.monotonic() - start
        await handle.stop()
        return ConnectionTestResult(ok=True, tool_count=len(tools), elapsed_seconds=elapsed)

    # --- diagnostics (Phase 7: the log panel + raw result inspector) -----

    def recent_calls(self, limit: int = 200) -> list[tuple[str, ToolCallRecord]]:
        """Every running server's recorded calls, most recent first,
        capped at ``limit``. Sorted by ``ToolCallRecord.recorded_at`` (not
        just concatenated per-server) so calls from different servers
        interleave in the order they actually happened. Diagnostics stay
        session-scoped and in-memory — the same scope
        ``McpServerHandle.calls`` itself already has — rather than
        persisted to SQLite; a restart clears the log, same as restarting
        clears each handle's own ``.calls`` list."""
        merged = [
            (name, record) for name, handle in self._handles.items() for record in handle.calls
        ]
        merged.sort(key=lambda pair: pair[1].recorded_at, reverse=True)
        return merged[:limit]

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

        config = self._configs.get(server_name)
        if config is not None and tool_name in config.confirm_tools:
            namespaced = namespaced_tool_name(server_name, tool_name)
            approved = await self._confirm_callback(
                ConfirmationRequest(
                    action="tool_call",
                    path=namespaced,
                    detail=f'Run {namespaced}({arguments})? This tool is marked "confirm before run".',
                )
            )
            if not approved:
                # A normal error ToolResult, not a raised exception — same
                # contract McpServerError gets a few lines down, so every
                # path through this method returns rather than relying on
                # AgentLoop's outer try/except to paper over an escape.
                return ToolResult(content=f"{namespaced} declined by user (confirm-before-run)", is_error=True)

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


__all__ = ["ConnectionTestResult", "McpManager", "namespaced_tool_name"]
