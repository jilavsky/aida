"""``fetch_url`` native tool (Phase 9) — a small, stdlib-only network tool.

``web_search`` itself is deliberately **not** a built-in tool here: AIDA
already has full MCP client infrastructure (Phase 3/7, ``aida mcp add``) —
pointing a workspace at an existing community search MCP server (Brave,
Tavily, etc., the user's own choice of vendor/API key) is the "modular...
via a search MCP server if a good one exists" option PLAN.md's own escape
hatch describes, with zero new AIDA dependency or secret-handling code.
``McpServerConfig.disabled_tools``/``confirm_tools`` already give per-tool
visibility/confirmation, and a workspace's ``mcp_group`` already gives
"enable per workspace" — no new config surface needed for that half of the
phase file's "web search" task. See ``planning/phase09_coding_scripting.md``.

``fetch_url`` always requires confirmation — no folder concept applies to a
URL, and PLAN.md §5's safety model already names "anything that sends local
content to a network destination other than the configured LLM provider" as
an always-confirm case. No separate per-workspace on/off flag: the always-
confirm gate on every single call already *is* the "visible indicator" the
phase file asks for — declining once blocks that fetch, same as declining
any other confirmation.
"""

from __future__ import annotations

import asyncio
import re
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.error import URLError

from aida.core.confirmation import ConfirmationRequest
from aida.core.tools import NativeTool, ToolResult, wrap_tool_errors
from aida.providers.base import ToolSchema
from aida.workspace.safety import ConfirmationDenied, SafetyGuard

DEFAULT_FETCH_TIMEOUT_SECONDS = 15.0
#: Same "large enough that no real page hits the ceiling, not unlimited"
#: reasoning as knowledge/rag/ingest.py's own size caps.
DEFAULT_FETCH_MAX_CHARS = 20_000

_tool = wrap_tool_errors(ConfirmationDenied, URLError, OSError, ValueError)

_CHARSET_RE = re.compile(r"charset=([\w-]+)")


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text: strips tags, skips ``<script>``/``<style>``
    content — not a real readability extractor, just enough to make a
    fetched page's prose usable as tool-result text."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.text_parts)


def _fetch_sync(url: str) -> str:
    with urllib.request.urlopen(url, timeout=DEFAULT_FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310 - http(s)-only, checked by the caller
        raw = response.read(DEFAULT_FETCH_MAX_CHARS * 4)
        content_type = response.headers.get("Content-Type", "")
    match = _CHARSET_RE.search(content_type)
    charset = match.group(1) if match else "utf-8"
    text = raw.decode(charset, errors="replace")
    if "html" in content_type:
        text = _html_to_text(text)
    return text[:DEFAULT_FETCH_MAX_CHARS]


def default_web_tools(guard: SafetyGuard) -> dict[str, NativeTool]:
    @_tool
    async def fetch_url(arguments: dict[str, Any]) -> ToolResult:
        url = arguments["url"]
        if not url.startswith(("http://", "https://")):
            return ToolResult(content=f"Not an http(s) URL: {url}", is_error=True)

        approved = await guard.confirm_callback(
            ConfirmationRequest(action="fetch_url", path=url, detail=f"Fetch {url}?", in_allowed_roots=False)
        )
        if not approved:
            raise ConfirmationDenied(f"fetch_url declined: {url}")

        text = await asyncio.to_thread(_fetch_sync, url)
        return ToolResult(content=text)

    return {
        "fetch_url": NativeTool(
            schema=ToolSchema(
                name="fetch_url",
                description="Fetch a web page or document and return its readable text, size-capped.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "The http(s) URL to fetch."}},
                    "required": ["url"],
                },
            ),
            func=fetch_url,
        )
    }


__all__ = ["DEFAULT_FETCH_MAX_CHARS", "DEFAULT_FETCH_TIMEOUT_SECONDS", "default_web_tools"]
