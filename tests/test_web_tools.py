"""Tests for aida.workspace.web.default_web_tools — a real local HTTP
server (no monkeypatched urlopen) so the actual network code path is
exercised, same "call tool.func(...) directly" convention as
test_workspace_files.py."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aida.workspace.command_allowlist import CommandAllowlist
from aida.workspace.safety import ConfirmationRequest, SafetyGuard
from aida.workspace.web import DEFAULT_FETCH_MAX_CHARS, default_web_tools


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # silence test output
        pass

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming convention
        if self.path == "/plain":
            body = b"hello from a plain text page"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        elif self.path == "/html":
            body = b"<html><head><style>body{color:red}</style></head><body><p>Hello</p><script>evil()</script></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/big":
            body = (b"x" * (DEFAULT_FETCH_MAX_CHARS * 10))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        else:
            self.send_response(404)
            body = b""
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


async def _approve(_request: ConfirmationRequest) -> bool:
    return True


async def _deny(_request: ConfirmationRequest) -> bool:
    return False


def _guard(*, confirm=_approve) -> SafetyGuard:
    return SafetyGuard(allowed_roots=[], confirm_callback=confirm, command_allowlist=CommandAllowlist.empty())


async def _call(tools, name: str, **arguments):
    return await tools[name].func(arguments)


@pytest.mark.asyncio
async def test_fetch_url_plain_text(http_server: str):
    tools = default_web_tools(_guard())
    result = await _call(tools, "fetch_url", url=f"{http_server}/plain")
    assert not result.is_error
    assert "hello from a plain text page" in result.content


@pytest.mark.asyncio
async def test_fetch_url_strips_html_tags_and_script_content(http_server: str):
    tools = default_web_tools(_guard())
    result = await _call(tools, "fetch_url", url=f"{http_server}/html")
    assert "Hello" in result.content
    assert "evil()" not in result.content
    assert "<p>" not in result.content


@pytest.mark.asyncio
async def test_fetch_url_caps_size(http_server: str):
    tools = default_web_tools(_guard())
    result = await _call(tools, "fetch_url", url=f"{http_server}/big")
    assert len(result.content) <= DEFAULT_FETCH_MAX_CHARS


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme():
    tools = default_web_tools(_guard())
    result = await _call(tools, "fetch_url", url="file:///etc/passwd")
    assert result.is_error


@pytest.mark.asyncio
async def test_fetch_url_declined_is_an_error_result(http_server: str):
    tools = default_web_tools(_guard(confirm=_deny))
    result = await _call(tools, "fetch_url", url=f"{http_server}/plain")
    assert result.is_error


@pytest.mark.asyncio
async def test_fetch_url_always_confirms_even_with_no_allowed_folders(http_server: str):
    """fetch_url has no folder concept — it must go through
    confirm_callback on every call regardless of SafetyGuard's mode."""
    seen = []

    async def _track(request: ConfirmationRequest) -> bool:
        seen.append(request.action)
        return True

    tools = default_web_tools(_guard(confirm=_track))
    await _call(tools, "fetch_url", url=f"{http_server}/plain")
    assert seen == ["fetch_url"]


@pytest.mark.asyncio
async def test_fetch_url_404_is_not_a_crash(http_server: str):
    tools = default_web_tools(_guard())
    result = await _call(tools, "fetch_url", url=f"{http_server}/does-not-exist")
    assert result.is_error
