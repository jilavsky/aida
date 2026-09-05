"""The optional Mistral OCR figure backend (documents_implementation.md D).

Three properties, in order of how much they matter:

1. **It never fails a turn.** Every failure mode — no key, no extra, HTTP
   error, timeout, garbage response, a declined confirmation — falls back
   to the built-in extractor and *says so*, because silently producing
   worse labels than the user expected is how a "Figure 1" answer becomes
   quietly wrong.
2. **Nothing is uploaded without consent**, and never in an unattended run
   unless explicitly pre-approved by name.
3. Reading order is what it buys: a caption on a two-column page pairs
   correctly, where the built-in extractor can only report low confidence.

No network: the HTTP layer is stubbed throughout.
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path

import pytest

from aida.core.confirmation import ConfirmationRequest
from aida.core.headless import build_headless_confirm_callback
from aida.documents.figure_tools import OCR_CONFIRM_NAME, OcrBackend, default_figure_tools
from aida.documents.ocr.mistral import (
    MAX_UPLOAD_BYTES,
    MistralOcrError,
    figures_from_ocr,
    ocr_pdf,
    verify_api_key,
)

pymupdf = pytest.importorskip("pymupdf")
PILImage = pytest.importorskip("PIL.Image")


def _png(color: str = "red", size: int = 240) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf(path: Path, pages: int = 1) -> Path:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), "Body text of the paper. " * 10)
        page.insert_image(pymupdf.Rect(72, 200, 320, 420), stream=_png())
    doc.save(str(path))
    doc.close()
    return path


class _Response:
    def __init__(self, payload=None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload


class _StubClient:
    """Records what it was asked to do, so a test can assert an upload did
    or did not happen — which is the whole point of the consent tests."""

    def __init__(self, *, ocr_payload=None, status_by_call=None):
        self.calls: list[str] = []
        self._ocr_payload = ocr_payload if ocr_payload is not None else _ocr_payload()
        self._status = status_by_call or {}

    def post(self, url, **_kwargs):
        if url.endswith("/v1/files"):
            self.calls.append("upload")
            return _Response({"id": "file-1"}, self._status.get("upload", 200))
        self.calls.append("ocr")
        return _Response(self._ocr_payload, self._status.get("ocr", 200))

    def get(self, url, **_kwargs):
        self.calls.append("signed")
        return _Response({"url": "https://signed.example/doc.pdf"}, self._status.get("signed", 200))

    def close(self):
        self.calls.append("closed")


def _ocr_payload(markdown: str | None = None):
    body = (
        markdown
        if markdown is not None
        else (
            "Some body text in reading order.\n"
            "\n"
            "![img-0.jpeg](img-0.jpeg)\n"
            "\n"
            "Figure 1. SAXS patterns of the annealed film\n"
        )
    )
    return {
        "pages": [
            {
                "index": 0,
                "markdown": body,
                "images": [{"id": "img-0.jpeg", "image_base64": base64.b64encode(_png()).decode()}],
            }
        ]
    }


# --- the transport -------------------------------------------------------


def test_ocr_pdf_makes_the_three_calls_in_order(tmp_path: Path):
    client = _StubClient()
    result = ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=client)

    assert client.calls == ["upload", "signed", "ocr"]
    assert len(result.pages) == 1
    assert result.pages[0].images[0].id == "img-0.jpeg"


def test_a_data_uri_prefixed_image_is_decoded(tmp_path: Path):
    payload = _ocr_payload()
    raw = payload["pages"][0]["images"][0]["image_base64"]
    payload["pages"][0]["images"][0]["image_base64"] = f"data:image/png;base64,{raw}"
    result = ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=_StubClient(ocr_payload=payload))
    assert result.pages[0].images[0].data.startswith(b"\x89PNG")


@pytest.mark.parametrize("failing,status", [("upload", 401), ("signed", 500), ("ocr", 429)])
def test_every_http_failure_becomes_one_error_type(tmp_path: Path, failing: str, status: int):
    client = _StubClient(status_by_call={failing: status})
    with pytest.raises(MistralOcrError):
        ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=client)


def test_an_auth_failure_says_to_check_the_key(tmp_path: Path):
    client = _StubClient(status_by_call={"upload": 401})
    with pytest.raises(MistralOcrError, match="API key"):
        ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=client)


def test_a_response_with_no_pages_is_an_error_not_an_empty_result(tmp_path: Path):
    client = _StubClient(ocr_payload={"pages": []})
    with pytest.raises(MistralOcrError):
        ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=client)


def test_no_api_key_is_refused_before_anything_is_read(tmp_path: Path):
    client = _StubClient()
    with pytest.raises(MistralOcrError, match="no API key"):
        ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="", client=client)
    assert client.calls == []


def test_an_oversized_document_is_refused_locally(tmp_path: Path, monkeypatch):
    """A clear message beats a 413 from the service, and the file never
    leaves the machine."""
    client = _StubClient()
    monkeypatch.setattr("aida.documents.ocr.mistral.MAX_UPLOAD_BYTES", 10)
    with pytest.raises(MistralOcrError, match="limit"):
        ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=client)
    assert client.calls == []
    assert MAX_UPLOAD_BYTES > 0


# --- reading order is the point ------------------------------------------


def test_reading_order_pairs_a_caption_with_high_confidence(tmp_path: Path):
    """What the built-in extractor can only call `low` on a multi-column
    page, OCR resolves — the layout problem was solved upstream."""
    result = ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=_StubClient())
    entries = figures_from_ocr(result, tmp_path / "assets")

    assert len(entries) == 1
    assert entries[0].label == "Figure 1"
    assert "SAXS patterns" in entries[0].caption
    assert entries[0].confidence == "high"
    assert (tmp_path / "assets" / entries[0].file).exists()


def test_an_image_with_no_caption_beside_it_stays_positional(tmp_path: Path):
    """Being sure of the reading order does not conjure a caption that is
    not there."""
    payload = _ocr_payload("![img-0.jpeg](img-0.jpeg)\n\nJust ordinary prose, not a caption.\n")
    result = ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=_StubClient(ocr_payload=payload))
    entries = figures_from_ocr(result, tmp_path / "assets")

    assert entries[0].confidence == "none"
    assert entries[0].label.startswith("image 1")


def test_a_caption_above_the_image_is_found_too(tmp_path: Path):
    payload = _ocr_payload("Table 2. Fitted parameters\n\n![img-0.jpeg](img-0.jpeg)\n")
    result = ocr_pdf(_pdf(tmp_path / "p.pdf"), api_key="k", client=_StubClient(ocr_payload=payload))
    entries = figures_from_ocr(result, tmp_path / "assets")
    assert entries[0].label == "Table 2"


# --- consent -------------------------------------------------------------


def _attached(tmp_path: Path) -> Path:
    from aida.documents.attachments import store_attachment

    attachments = tmp_path / "attachments" / "abcd1234"
    store_attachment(_pdf(tmp_path / "paper.pdf"), attachments)
    return attachments


def _backend(answer: bool, _client: _StubClient, *, calls: list | None = None) -> OcrBackend:
    async def confirm(request: ConfirmationRequest) -> bool:
        if calls is not None:
            calls.append(request)
        return answer

    return OcrBackend(api_key="k", confirm=confirm)


def _run_list(tmp_path: Path, backend, client):
    import aida.documents.figure_tools as ft

    original = ft.ocr_pdf
    ft.ocr_pdf = lambda path, **kwargs: original(path, **{**kwargs, "client": client})
    try:
        tools = default_figure_tools(lambda: _attached(tmp_path), ocr=backend)
        return asyncio.run(tools["list_document_figures"].func({"document": "paper.pdf"}))
    finally:
        ft.ocr_pdf = original


def test_the_user_is_asked_before_a_document_is_uploaded(tmp_path: Path):
    requests: list[ConfirmationRequest] = []
    client = _StubClient()
    result = _run_list(tmp_path, _backend(True, client, calls=requests), client)

    assert len(requests) == 1
    assert "paper.pdf" in requests[0].detail
    assert "third-party" in requests[0].detail
    assert requests[0].path == OCR_CONFIRM_NAME
    assert "Figure 1" in result.content
    assert "Mistral OCR" in result.content


def test_declining_falls_back_and_uploads_nothing(tmp_path: Path):
    client = _StubClient()
    result = _run_list(tmp_path, _backend(False, client), client)

    assert client.calls == [], "a declined confirmation must not send the document"
    assert not result.is_error, "declining is not a failure — the built-in extractor still runs"
    assert "OCR was not used" in result.content


def test_a_service_failure_falls_back_and_says_so(tmp_path: Path):
    client = _StubClient(status_by_call={"ocr": 500})
    result = _run_list(tmp_path, _backend(True, client), client)

    assert not result.is_error
    assert "OCR was not used" in result.content
    assert "Figure 1" in result.content or "image 1" in result.content


def test_a_headless_run_refuses_the_upload_unless_preapproved(tmp_path: Path):
    """The default unattended answer is no. `--preapprove-tool` is the
    deliberate opt-in, and it works because the confirmation reuses the
    `tool_call` action rather than inventing a category headless mode has
    never heard of."""
    refusing = build_headless_confirm_callback(yes_in_allowed=True)
    request = ConfirmationRequest(
        action="tool_call", path=OCR_CONFIRM_NAME, detail="", in_allowed_roots=False
    )
    assert asyncio.run(refusing(request)) is False

    approving = build_headless_confirm_callback(
        yes_in_allowed=False, preapproved_tools={OCR_CONFIRM_NAME}
    )
    assert asyncio.run(approving(request)) is True


def test_the_confirmation_is_rememberable_for_the_chat():
    """Working through a stack of manuals must not be a stack of dialogs —
    the request carries a remember_scope, and its action is one the
    remembering layer actually caches."""
    from aida.core.confirmation import REMEMBERABLE_ACTIONS

    async def capture(request):
        captured.append(request)
        return True

    captured: list[ConfirmationRequest] = []
    asyncio.run(OcrBackend(api_key="k", confirm=capture).approved_for(Path("paper.pdf")))
    assert captured[0].remember_scope == OCR_CONFIRM_NAME
    assert captured[0].action in REMEMBERABLE_ACTIONS


def test_a_cached_index_is_never_re_uploaded(tmp_path: Path):
    """Every upload is a dialog and a document leaving the machine; asking
    a second question about the same paper must do neither."""
    client = _StubClient()
    backend = _backend(True, client)
    _run_list(tmp_path, backend, client)
    first = list(client.calls)
    assert "upload" in first

    client.calls.clear()
    _run_list(tmp_path, backend, client)
    assert client.calls == []


# --- verifying the key without uploading anything ------------------------


class _ModelsClient:
    def __init__(self, payload=None, status_code: int = 200):
        self.calls: list[str] = []
        self._payload = payload if payload is not None else {"data": [{"id": "mistral-ocr-latest"}]}
        self._status = status_code

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return _Response(self._payload, self._status)

    def post(self, *_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("verifying a key must not upload anything")

    def close(self):
        pass


def test_verify_api_key_reports_the_ocr_model_and_uploads_nothing():
    """ "Is my key working?" has to be answerable without sending a
    document — otherwise the only way to test the setup is to perform the
    exact action the user is being careful about."""
    client = _ModelsClient()
    detail = verify_api_key("k", client=client)
    assert "works" in detail.lower()
    assert "mistral-ocr-latest" in detail
    assert client.calls and client.calls[0].endswith("/v1/models")


def test_verify_api_key_flags_a_key_with_no_ocr_model():
    """A valid key whose account cannot see an OCR model is a real and
    confusing state: the upload would succeed and the OCR call fail."""
    detail = verify_api_key("k", client=_ModelsClient({"data": [{"id": "mistral-small"}]}))
    assert "no OCR model" in detail


def test_verify_api_key_rejects_a_bad_key():
    with pytest.raises(MistralOcrError, match="API key"):
        verify_api_key("k", client=_ModelsClient(status_code=401))


def test_verify_api_key_needs_a_key():
    with pytest.raises(MistralOcrError, match="no API key"):
        verify_api_key("", client=_ModelsClient())


# --- the `aida documents figures` diagnostic -----------------------------
#
# The question this answers: "how do I tell whether my document was sent to
# Mistral?" Every other signal is indirect — a dialog that only appears
# when the agent asks about figures, a note in a tool result, a field in a
# JSON file. "Attach a PDF and read the reply carefully" is not a way to
# check a setup.


def _run_figures(tmp_path: Path, argv: list[str], monkeypatch) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    from aida.cli.documents_cmds import main as documents_main

    monkeypatch.setenv("AIDA_HOME", str(tmp_path / ".aida"))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = documents_main(argv)
    return code, buffer.getvalue()


def test_figures_reports_the_builtin_backend_and_why(tmp_path: Path, monkeypatch):
    pdf = _pdf(tmp_path / "paper.pdf")
    code, out = _run_figures(
        tmp_path, ["figures", str(pdf), "--assets-dir", str(tmp_path / "a")], monkeypatch
    )
    assert code == 0
    assert "backend: builtin" in out
    assert "--ocr not passed" in out, "a bare 'not used' is useless — it must say which reason"


def test_figures_names_a_missing_key_rather_than_failing_silently(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aida.cli.documents_cmds.get_secret", lambda _ref: None)
    pdf = _pdf(tmp_path / "paper.pdf")
    code, out = _run_figures(
        tmp_path,
        ["figures", str(pdf), "--ocr", "--yes", "--assets-dir", str(tmp_path / "a")],
        monkeypatch,
    )
    assert code == 0
    assert "no API key" in out


def test_figures_surfaces_an_ocr_failure_instead_of_burying_it_in_a_log(
    tmp_path: Path, monkeypatch
):
    """The whole reason the command exists: this string used to go to a log
    file nobody was watching, and the only symptom was figures quietly a
    bit worse than expected."""
    monkeypatch.setattr("aida.cli.documents_cmds.get_secret", lambda _ref: "k")

    def _boom(*_a, **_k):
        raise MistralOcrError("upload failed with HTTP 401 — check the API key")

    monkeypatch.setattr("aida.cli.documents_cmds.ocr_pdf", _boom)
    pdf = _pdf(tmp_path / "paper.pdf")
    code, out = _run_figures(
        tmp_path,
        ["figures", str(pdf), "--ocr", "--yes", "--assets-dir", str(tmp_path / "a")],
        monkeypatch,
    )
    assert code == 1, "a failed OCR run must be a non-zero exit, not a quiet fallback"
    assert "OCR failed" in out and "401" in out
    assert "backend: builtin" in out, "…and it still falls back, so the figures are there"


def test_figures_json_output_carries_the_backend(tmp_path: Path, monkeypatch):
    import json as _json

    pdf = _pdf(tmp_path / "paper.pdf")
    code, out = _run_figures(
        tmp_path,
        ["figures", str(pdf), "--json", "--assets-dir", str(tmp_path / "a")],
        monkeypatch,
    )
    payload = _json.loads(out)
    assert code == 0
    assert payload["backend"] == "builtin"
    assert payload["figures"] and payload["figures"][0]["label"]


def test_figures_uses_the_workspace_switch(tmp_path: Path, monkeypatch):
    from aida.config.settings import WorkspaceConfig, WorkspacesConfig, load_settings

    monkeypatch.setenv("AIDA_HOME", str(tmp_path / ".aida"))
    settings = load_settings()
    settings.workspaces = WorkspacesConfig(
        workspaces={"manuals": WorkspaceConfig(name="manuals", use_ocr=False)}
    )
    monkeypatch.setattr("aida.cli.documents_cmds.load_settings", lambda: settings)
    pdf = _pdf(tmp_path / "paper.pdf")
    code, out = _run_figures(
        tmp_path,
        ["figures", str(pdf), "--workspace", "manuals", "--assets-dir", str(tmp_path / "a")],
        monkeypatch,
    )
    assert code == 0
    assert "use_ocr disabled" in out


def test_figures_rejects_a_missing_file(tmp_path: Path, monkeypatch):
    code, _out = _run_figures(tmp_path, ["figures", str(tmp_path / "nope.pdf")], monkeypatch)
    assert code == 1
