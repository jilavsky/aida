"""Mistral OCR as a figure-extraction backend.

**Why this exists.** The built-in `pymupdf` extractor
(``aida.documents.figures``) pairs an image with the caption text nearest
to it on the page, which is reliable on single-column documents and a guess
on the two-column layouts most journals use — "the text below this image"
is regularly the neighbouring column. Mistral OCR returns *ordered
interleaved text and images*: per-page Markdown with inline
``![img-0.jpeg](img-0.jpeg)`` placeholders in **reading order**. Pairing an
image with its caption then becomes "take the adjacent line", and the hard
layout problem was solved before the text reached us. That, not nicer text,
is the argument for this backend — it is what makes "show me Figure 1"
trustworthy on a real paper.

**No SDK.** Three REST calls do not justify the ``mistralai`` package and
the pydantic/httpx pins it would drag in alongside the ``openai`` and
``anthropic`` SDKs that actually matter. ``httpx`` is already present
transitively via those two; the ``ocr`` extra declares it, because relying
on a transitive dependency is exactly what breaks on someone else's
resolver.

**This uploads the user's document to a third party.** Everything that
follows from that — off by default, per workspace, a confirmation naming
the file, never in an unattended run without explicit pre-approval, and a
fallback that always says what happened — lives in
``aida.documents.figure_tools``, not here. This module is the transport.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

from aida.config.logging_setup import get_logger
from aida.documents.figures import FigureEntry

logger = get_logger(__name__)

#: Keyring/env ref for the API key, resolved through ``aida.config.secrets``
#: exactly like a provider key — so ``AIDA_SECRET_MISTRAL_OCR`` works for
#: headless runs with no extra machinery.
SECRET_REF = "mistral-ocr"

DEFAULT_MODEL = "mistral-ocr-latest"
DEFAULT_BASE_URL = "https://api.mistral.ai"

#: Generous, because OCR of a long paper is genuinely slow — but bounded,
#: because a hung upload must not hold a turn open forever.
DEFAULT_TIMEOUT_SECONDS = 180.0

#: Refuse locally rather than uploading something the free tier will reject
#: anyway. The service's own limit is larger; this is a courtesy check that
#: produces a clear message instead of a 413.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_IMAGE_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_CAPTION_RE = re.compile(
    r"^\s*(?P<kind>Fig(?:ure)?|Table|Scheme|Chart|Plate)\s*\.?\s*(?P<number>[0-9]+[a-z]?)",
    re.IGNORECASE,
)


class MistralOcrError(Exception):
    """Any reason this backend could not produce a result. Always caught by
    the caller and turned into a fallback plus an explanation — never
    surfaced as a failed turn."""


@dataclass
class OcrImage:
    id: str
    data: bytes


@dataclass
class OcrPage:
    index: int
    markdown: str
    images: list[OcrImage]


@dataclass
class OcrResult:
    pages: list[OcrPage]


def _decode_image(payload: str) -> bytes | None:
    """Base64 from the API, with or without a ``data:`` prefix."""
    if not payload:
        return None
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        return base64.b64decode(payload)
    except (ValueError, TypeError):
        return None


def verify_api_key(
    api_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
    client: object | None = None,
) -> str:
    """Check a key against the service and return a short human-readable
    result. Raises ``MistralOcrError`` if the key is not usable.

    Uploads nothing: it lists models, which is the cheapest authenticated
    call the API has. That distinction matters — "is my key right?" must be
    answerable without sending a document anywhere, or the only way to test
    the configuration would be to perform the exact action the user is
    being careful about.
    """
    if not api_key:
        raise MistralOcrError("no API key given")
    owns_client = client is None
    if client is None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - the extra being absent
            raise MistralOcrError(
                "the 'ocr' extra is not installed (pip install 'aida-workbench[ocr]')"
            ) from exc
        client = httpx.Client(timeout=timeout)
    try:
        response = client.get(f"{base_url}/v1/models", headers={"Authorization": f"Bearer {api_key}"})
        _raise_for_status(response, "key check")
        names = [m.get("id", "") for m in (response.json().get("data") or [])]
        ocr_models = [name for name in names if "ocr" in name.lower()]
        if ocr_models:
            return f"Key works. OCR model available: {', '.join(sorted(ocr_models)[:3])}."
        # A valid key whose account cannot see an OCR model is a real and
        # confusing state — the upload would succeed and the OCR call fail.
        return "Key works, but no OCR model is visible on this account."
    except MistralOcrError:
        raise
    except Exception as exc:  # noqa: BLE001 - one error type for the caller
        raise MistralOcrError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if owns_client:
            close = getattr(client, "close", None)
            if callable(close):
                close()


def ocr_pdf(
    path: Path,
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: object | None = None,
) -> OcrResult:
    """Upload ``path``, run OCR on it, and return the pages with images.

    Three calls: ``POST /v1/files`` (purpose=ocr), ``GET
    /v1/files/{id}/url`` for a signed URL, then ``POST /v1/ocr``.

    ``client`` is an injection point for tests — anything with httpx's
    ``post``/``get`` shape. Production passes nothing and gets a real
    ``httpx.Client``.

    Raises ``MistralOcrError`` for every failure mode (missing extra,
    oversized file, auth, network, malformed response) so the caller has
    one thing to catch.
    """
    if not api_key:
        raise MistralOcrError("no API key configured")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise MistralOcrError(f"cannot read {path.name}: {exc}") from exc
    if size > MAX_UPLOAD_BYTES:
        raise MistralOcrError(
            f"{path.name} is {size // (1024 * 1024)} MB, over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
        )

    owns_client = client is None
    if client is None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised by the extra being absent
            raise MistralOcrError(
                "the 'ocr' extra is not installed (pip install 'aida-workbench[ocr]')"
            ) from exc
        client = httpx.Client(timeout=timeout)

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with path.open("rb") as handle:
            upload = client.post(
                f"{base_url}/v1/files",
                headers=headers,
                files={"file": (path.name, handle, "application/pdf")},
                data={"purpose": "ocr"},
            )
        _raise_for_status(upload, "upload")
        file_id = upload.json().get("id")
        if not file_id:
            raise MistralOcrError("upload succeeded but returned no file id")

        signed = client.get(f"{base_url}/v1/files/{file_id}/url", headers=headers)
        _raise_for_status(signed, "signed URL")
        document_url = signed.json().get("url")
        if not document_url:
            raise MistralOcrError("no signed URL returned for the uploaded file")

        response = client.post(
            f"{base_url}/v1/ocr",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model": model,
                "document": {"type": "document_url", "document_url": document_url},
                "include_image_base64": True,
            },
        )
        _raise_for_status(response, "OCR")
        return _parse(response.json())
    except MistralOcrError:
        raise
    except Exception as exc:  # noqa: BLE001 - one error type for the caller
        raise MistralOcrError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if owns_client:
            close = getattr(client, "close", None)
            if callable(close):
                close()


def _raise_for_status(response, what: str) -> None:
    status = getattr(response, "status_code", 200)
    if status >= 400:
        # 401 and 429 are the two a user can actually act on, so name them.
        hint = {401: " — check the API key", 403: " — check the API key", 429: " — rate limited"}.get(
            status, ""
        )
        raise MistralOcrError(f"{what} failed with HTTP {status}{hint}")


def _parse(payload: dict) -> OcrResult:
    pages: list[OcrPage] = []
    for raw in payload.get("pages") or []:
        images = []
        for image in raw.get("images") or []:
            data = _decode_image(image.get("image_base64") or "")
            if data:
                images.append(OcrImage(id=str(image.get("id") or f"img-{len(images)}"), data=data))
        pages.append(
            OcrPage(index=int(raw.get("index", len(pages))), markdown=raw.get("markdown") or "", images=images)
        )
    if not pages:
        raise MistralOcrError("the OCR response contained no pages")
    return OcrResult(pages=pages)


def _caption_near(lines: list[str], position: int) -> str:
    """The caption for an image placeholder at ``lines[position]``.

    Because the Markdown is in **reading order**, "the next non-empty line"
    is the caption on a two-column page just as reliably as on a
    single-column one — which is the entire reason this backend exists.
    Looks forward first (the figure convention), then back (tables).
    """
    for step in (1, -1):
        index = position + step
        while 0 <= index < len(lines):
            candidate = lines[index].strip()
            if candidate:
                if _CAPTION_RE.match(candidate):
                    return candidate
                break  # a non-caption neighbour: do not keep hunting
            index += step
    return ""


def _label_from_caption(caption: str) -> str:
    match = _CAPTION_RE.match(caption)
    if not match:
        return ""
    kind = match.group("kind").lower()
    kind = "Figure" if kind.startswith("fig") else kind.capitalize()
    return f"{kind} {match.group('number')}"


def figures_from_ocr(result: OcrResult, assets_dir: Path) -> list[FigureEntry]:
    """Write each returned image out and pair it with its caption.

    Entries carry ``confidence="high"`` when a caption was matched: reading
    order removed the layout guesswork that forces the built-in extractor
    to report ``low`` on a multi-column page. An image with no caption
    beside it still gets ``none`` and a positional label — being sure about
    the reading order does not conjure a caption that is not there.
    """
    assets_dir.mkdir(parents=True, exist_ok=True)
    entries: list[FigureEntry] = []
    for page in result.pages:
        by_id = {image.id: image for image in page.images}
        lines = page.markdown.splitlines()
        for position, line in enumerate(lines):
            for target in _IMAGE_PLACEHOLDER_RE.findall(line):
                image = by_id.get(target) or by_id.get(Path(target).name)
                if image is None:
                    continue
                suffix = Path(image.id).suffix or ".png"
                filename = f"fig-{len(entries) + 1:02d}{suffix}"
                (assets_dir / filename).write_bytes(image.data)
                caption = _caption_near(lines, position)
                label = _label_from_caption(caption)
                entries.append(
                    FigureEntry(
                        label=label or f"image {len(entries) + 1} (page {page.index + 1})",
                        caption=caption,
                        file=filename,
                        page=page.index + 1,
                        confidence="high" if label else "none",
                    )
                )
    return entries


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_UPLOAD_BYTES",
    "SECRET_REF",
    "MistralOcrError",
    "OcrImage",
    "OcrPage",
    "OcrResult",
    "figures_from_ocr",
    "ocr_pdf",
    "verify_api_key",
]
