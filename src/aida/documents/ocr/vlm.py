"""PDF page conversion with a vision model.

Each page that needs it is rendered with PyMuPDF and sent, as an image, to a
model with vision through the ordinary provider layer, so any profile AIDA
can chat with (Argo, Anthropic, a local OpenAI-compatible server) can also
read a scanned page. The model returns the page as Markdown in reading
order, with image placeholders followed by their captions; see
``aida.documents.ocr.common`` for how those pair captions with figures.

**Which model** (``resolve_ocr_routes``), in order: a dedicated OCR profile
when one is configured (``AppConfig.ocr_profile``), then the chat profile
when it has ``supports_vision``. If neither applies, nothing is converted
and the caller is given a message that says why and what to change. A
dedicated profile that fails is skipped in favour of the chat model, with a
note.

**Privacy.** Pages go where the chat already goes, so the chat model needs
no extra consent. A dedicated profile on a loopback address needs none
either. Only a dedicated profile on a *different remote host* than the chat
asks first, once per document, through the same confirmation channel as
other tool actions (unattended runs refuse unless pre-approved).

**Cache.** Converted pages are kept under ``~/.aida/cache/ocr``, keyed by the
file's content hash, the model and the render size, so a document is
converted once.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import ipaddress
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from aida.config.logging_setup import get_logger
from aida.core.confirmation import ConfirmationRequest, ConfirmCallback
from aida.core.events import AgentError, MessageFinished, TextFinished
from aida.providers.base import CompletionSettings, ImageRef, LLMProvider, Message
from aida.providers.retry import SHORT_RETRY_POLICY, complete_with_retries

logger = get_logger(__name__)

#: Bumped when the prompt or the output handling changes, so cached pages
#: from an older version are not reused.
CONVERSION_VERSION = 1

#: Pages are rendered so their longest side is this many pixels: enough for
#: small print and subscripts, and what LightOnOCR-2 expects.
RENDER_LONGEST_SIDE_PX = 1540

#: Above this, a rendered page is re-encoded as JPEG (provider image limits
#: are around 5 MB, and base64 adds a third).
MAX_PAGE_IMAGE_BYTES = 3_500_000

PAGE_TIMEOUT_SECONDS = 180.0

#: Pages in flight at once: a local server works one request at a time, a
#: cloud endpoint handles a few in parallel.
LOCAL_CONCURRENCY = 1
REMOTE_CONCURRENCY = 3

OCR_PROMPT = (
    "Transcribe this page to Markdown in reading order. Write equations as LaTeX ($...$ "
    "inline, $$...$$ for display) and tables as Markdown tables. For each figure, plot or "
    "photograph, write ![fig-N](fig-N) where it appears, numbering the figures on this page "
    "from 1, and put its caption on the next line. Do not describe images and add no "
    "commentary: output only the page content."
)

OCR_CONFIRM_NAME = "ocr_page_upload"

_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(?P<body>.*?)\n?```\s*$", re.DOTALL)


def is_loopback_url(url: str | None) -> bool:
    """Whether ``url`` points at this machine (``localhost``, ``127.x``,
    ``::1``)."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").lower() if url else ""


@dataclass
class OcrRoute:
    """One model that can convert pages, and how to call it."""

    provider: LLMProvider
    settings: CompletionSettings
    #: For messages: "the OCR profile 'x'" / "the chat model (profile 'y')".
    label: str
    concurrency: int
    #: Ask before sending a document's pages (a dedicated profile on a
    #: different remote host than the chat).
    needs_confirmation: bool = False
    #: Whether this route's provider was built for it and must be closed.
    owns_provider: bool = False
    prompt: str = OCR_PROMPT
    #: Set once this route's model has put a page into its reasoning and
    #: left the reply empty (a thinking model such as LightOnOCR-3); later
    #: pages are then requested with reasoning off from the start.
    reasoning_off: bool = False


@dataclass
class RouteDecision:
    """``routes`` to try in order; ``unavailable`` says why there are none."""

    routes: list[OcrRoute] = field(default_factory=list)
    unavailable: str = ""


def describe_route(
    *,
    mode: str,
    ocr_profile_name: str | None,
    ocr_profile_exists: bool,
    chat_profile_name: str | None,
    chat_supports_vision: bool,
) -> str:
    """One line for ``aida doctor`` and Settings: which model would convert
    a scanned page, or why none would. No network access."""
    if mode == "off":
        return "off (ocr_backend: off)"
    parts = []
    if ocr_profile_name:
        if ocr_profile_exists:
            parts.append(f"the OCR profile {ocr_profile_name!r}")
        else:
            parts.append(f"(OCR profile {ocr_profile_name!r} does not exist)")
    if chat_profile_name and chat_supports_vision:
        parts.append(f"the chat model (profile {chat_profile_name!r})")
    usable = [p for p in parts if not p.startswith("(")]
    if not usable:
        why = parts[0] + " and " if parts else ""
        return (
            f"unavailable: {why}profile {chat_profile_name!r} has supports_vision off — "
            "scanned pages cannot be converted"
        )
    route = usable[0] + (f", falling back to {usable[1]}" if len(usable) > 1 else "")
    note = f" {parts[0]}" if parts[0].startswith("(") else ""
    return route + note


def unavailable_message(chat_profile_name: str | None) -> str:
    """The "no model can convert this" explanation, naming the fix."""
    return (
        "Converting it needs a model with vision, and the current profile "
        f"{chat_profile_name!r} has supports_vision off. Enable vision on this profile if the "
        "model supports images, or set an OCR profile (Settings, Documents; or ocr_profile in "
        "config.yaml)."
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:20]


def _cache_name(model: str, page_index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)[:60] or "model"
    return f"{slug}-v{CONVERSION_VERSION}-{RENDER_LONGEST_SIDE_PX}px-p{page_index + 1:04d}.md"


def render_page(pdf: Path, page_index: int, out_dir: Path) -> Path:
    """Render one page (0-based) to an image file in ``out_dir``."""
    import pymupdf

    with pymupdf.open(pdf) as doc:
        page = doc[page_index]
        longest = max(page.rect.width, page.rect.height) or 1.0
        zoom = RENDER_LONGEST_SIDE_PX / longest
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        data = pixmap.tobytes("png")
        suffix = ".png"
        if len(data) > MAX_PAGE_IMAGE_BYTES:
            data = pixmap.tobytes("jpg", jpg_quality=85)
            suffix = ".jpg"
    target = out_dir / f"page-{page_index + 1:04d}{suffix}"
    target.write_bytes(data)
    return target


def clean_page_markdown(text: str) -> str:
    """The model's reply without a wrapping code fence."""
    match = _FENCE_RE.match(text)
    return (match.group("body") if match else text).strip()


class PageConversionError(Exception):
    """One page could not be converted (provider error, timeout, empty reply)."""


#: Request parameter that switches a thinking model's reasoning off on
#: OpenAI-compatible servers (Ollama, vLLM, llama.cpp honour it).
REASONING_OFF = {"reasoning_effort": "none"}


async def _request_page(
    route: OcrRoute, request: list[Message], settings: CompletionSettings
) -> tuple[str, int]:
    """One request: ``(reply text, reasoning characters)``."""
    text = ""
    reasoning_chars = 0
    error: str | None = None

    async def _collect() -> None:
        nonlocal text, reasoning_chars, error
        stream = complete_with_retries(
            route.provider, request, [], settings, policy=SHORT_RETRY_POLICY
        )
        # aclosing: the HTTP stream is closed now, not by the garbage
        # collector after the event loop has gone.
        async with contextlib.aclosing(stream) as events:
            async for event in events:
                if isinstance(event, TextFinished):
                    text = event.text
                elif isinstance(event, MessageFinished):
                    reasoning_chars = event.reasoning_chars
                elif isinstance(event, AgentError):
                    error = f"{event.message}" + (f" ({event.detail})" if event.detail else "")

    try:
        await asyncio.wait_for(_collect(), timeout=PAGE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise PageConversionError(f"no reply within {PAGE_TIMEOUT_SECONDS:.0f} s") from exc
    if error is not None:
        raise PageConversionError(error)
    return text, reasoning_chars


async def convert_page(route: OcrRoute, image: Path) -> str:
    """Send one rendered page to ``route`` and return its Markdown.

    A thinking model may write the whole page into its reasoning and send an
    empty reply (seen with LightOnOCR-3 on Ollama). That page is asked for
    again with reasoning off (``REASONING_OFF``), and the route remembers to
    do so for its later pages.
    """
    mime = "image/jpeg" if image.suffix == ".jpg" else "image/png"
    request = [
        Message(
            role="user",
            content=route.prompt,
            images=[ImageRef(path=str(image), mime_type=mime, max_edge_px=RENDER_LONGEST_SIDE_PX)],
        )
    ]
    off = dataclasses.replace(route.settings, extra={**route.settings.extra, **REASONING_OFF})
    text, reasoning_chars = await _request_page(
        route, request, off if route.reasoning_off else route.settings
    )
    if not clean_page_markdown(text) and reasoning_chars and not route.reasoning_off:
        logger.info(
            "%s answered in its reasoning only (%d chars); asking again with reasoning off",
            route.label,
            reasoning_chars,
        )
        route.reasoning_off = True
        text, reasoning_chars = await _request_page(route, request, off)
    markdown = clean_page_markdown(text)
    if not markdown:
        if reasoning_chars:
            raise PageConversionError(
                "the model wrote its answer into its reasoning and returned an empty page, "
                "even with reasoning off"
            )
        raise PageConversionError("the model returned an empty page")
    return markdown


@dataclass
class ConversionResult:
    """Converted pages (0-based index -> Markdown), the ones that failed, and
    what to tell the reader about how it went."""

    pages: dict[int, str] = field(default_factory=dict)
    failed: list[int] = field(default_factory=list)
    route_label: str = ""
    #: Set when nothing could be converted at all (no route, declined).
    unavailable: str = ""
    notes: list[str] = field(default_factory=list)


RouteResolver = Callable[[], RouteDecision]


class PdfPageConverter:
    """Converts chosen pages of a PDF, with caching and route fallback.

    ``resolver`` is called on every conversion, so a profile switch in the
    running chat is picked up. ``confirm`` asks before pages go to a
    dedicated remote OCR profile.
    """

    def __init__(
        self,
        resolver: RouteResolver | None = None,
        *,
        confirm: ConfirmCallback | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.resolver = resolver
        self.confirm = confirm
        self.cache_dir = cache_dir

    async def __call__(self, pdf: Path, pages: list[int]) -> ConversionResult:
        return await self.convert(pdf, pages)

    async def convert(self, pdf: Path, pages: list[int]) -> ConversionResult:
        result = ConversionResult()
        if not pages:
            return result
        decision = self.resolver() if self.resolver is not None else RouteDecision()
        if not decision.routes:
            result.unavailable = decision.unavailable or "No model is set up to convert pages."
            return result
        try:
            digest = await asyncio.to_thread(file_digest, pdf)
            missing = sorted(set(pages))
            for route in decision.routes:
                if not missing:
                    break
                cached = self._read_cache(digest, route.settings.model, missing)
                result.pages.update(cached)
                missing = [p for p in missing if p not in cached]
                if not missing:
                    result.route_label = result.route_label or route.label
                    break
                if route.needs_confirmation and not await self._approved(pdf, route):
                    result.notes.append(f"You declined sending {pdf.name!r} to {route.label}.")
                    continue
                converted, errors = await self._convert_with(route, pdf, missing)
                for index, markdown in converted.items():
                    result.pages[index] = markdown
                    self._write_cache(digest, route.settings.model, index, markdown)
                if converted:
                    result.route_label = route.label
                    missing = [p for p in missing if p not in converted]
                    if missing:
                        sample = next(iter(errors.values()), "")
                        result.notes.append(
                            f"{len(missing)} page(s) could not be converted ({sample})."
                        )
                    break
                # Nothing at all came back: the route is unusable right now.
                sample = next(iter(errors.values()), "unknown error")
                result.notes.append(f"{route.label} could not convert pages: {sample}.")
                logger.warning("OCR route %s failed for %s: %s", route.label, pdf.name, sample)
        finally:
            for route in decision.routes:
                if route.owns_provider:
                    try:
                        await route.provider.aclose()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        logger.debug("closing OCR provider failed", exc_info=True)
        result.failed = [p for p in sorted(set(pages)) if p not in result.pages]
        if not result.pages and not result.unavailable:
            result.unavailable = " ".join(result.notes) or "No page could be converted."
        return result

    async def _approved(self, pdf: Path, route: OcrRoute) -> bool:
        if self.confirm is None:
            return False
        try:
            return await self.confirm(
                ConfirmationRequest(
                    action="tool_call",
                    path=OCR_CONFIRM_NAME,
                    detail=(
                        f"Send the pages of {pdf.name!r} that need conversion to {route.label}? "
                        "That model runs on a different server than this chat."
                    ),
                    in_allowed_roots=False,
                    remember_scope=OCR_CONFIRM_NAME,
                )
            )
        except Exception:  # noqa: BLE001 - a confirmation failure is a decline
            logger.warning("OCR confirmation failed for %s", pdf.name, exc_info=True)
            return False

    async def _convert_with(
        self, route: OcrRoute, pdf: Path, pages: list[int]
    ) -> tuple[dict[int, str], dict[int, str]]:
        semaphore = asyncio.Semaphore(max(1, route.concurrency))
        converted: dict[int, str] = {}
        errors: dict[int, str] = {}
        with tempfile.TemporaryDirectory(prefix="aida-ocr-") as tmp:
            out_dir = Path(tmp)

            async def _one(index: int) -> None:
                async with semaphore:
                    try:
                        image = await asyncio.to_thread(render_page, pdf, index, out_dir)
                        converted[index] = await convert_page(route, image)
                    except Exception as exc:  # noqa: BLE001 - a failed page is a gap, not a failure
                        errors[index] = str(exc) or type(exc).__name__

            await asyncio.gather(*(_one(index) for index in pages))
        logger.info(
            "converted %d of %d page(s) of %s with %s",
            len(converted),
            len(pages),
            pdf.name,
            route.label,
        )
        return converted, errors

    def _page_cache_dir(self, digest: str) -> Path | None:
        return self.cache_dir / digest if self.cache_dir is not None else None

    def _read_cache(self, digest: str, model: str, pages: list[int]) -> dict[int, str]:
        folder = self._page_cache_dir(digest)
        if folder is None or not folder.is_dir():
            return {}
        found: dict[int, str] = {}
        for index in pages:
            path = folder / _cache_name(model, index)
            try:
                found[index] = path.read_text(encoding="utf-8")
            except OSError:
                continue
        return found

    def _write_cache(self, digest: str, model: str, index: int, markdown: str) -> None:
        folder = self._page_cache_dir(digest)
        if folder is None:
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / _cache_name(model, index)).write_text(markdown, encoding="utf-8")
        except OSError:
            logger.debug("could not write the OCR cache", exc_info=True)


def build_routes(
    *,
    mode: str,
    ocr_profile: object | None,
    chat_profile: object | None,
    chat_provider: LLMProvider | None,
    chat_settings: CompletionSettings | None,
    build_provider: Callable[[object], LLMProvider],
    settings_for: Callable[[object], CompletionSettings],
    ocr_profile_name: str | None = None,
) -> RouteDecision:
    """The routes for the current configuration, in the order to try them.

    ``ocr_profile``/``chat_profile`` are ``ProviderProfile`` objects (typed
    loosely to keep this module free of the config layer). The chat route
    reuses the running chat provider; a dedicated profile gets its own.
    """
    chat_name = getattr(chat_profile, "name", None)
    if mode == "off":
        return RouteDecision(
            unavailable="Converting scanned pages is turned off (ocr_backend: off in config.yaml)."
        )
    routes: list[OcrRoute] = []
    notes = ""
    if ocr_profile_name and ocr_profile is None:
        notes = f"The OCR profile {ocr_profile_name!r} does not exist. "
    if ocr_profile is not None:
        try:
            provider = build_provider(ocr_profile)
        except Exception as exc:  # noqa: BLE001 - a broken OCR profile falls through
            logger.warning("OCR profile %r could not be built: %s", ocr_profile_name, exc)
            notes = f"The OCR profile {ocr_profile_name!r} could not be used ({exc}). "
        else:
            base_url = getattr(ocr_profile, "base_url", None)
            chat_url = getattr(chat_profile, "base_url", None)
            local = is_loopback_url(base_url)
            routes.append(
                OcrRoute(
                    provider=provider,
                    settings=_ocr_settings(settings_for(ocr_profile)),
                    label=f"the OCR profile {getattr(ocr_profile, 'name', ocr_profile_name)!r}",
                    concurrency=LOCAL_CONCURRENCY if local else REMOTE_CONCURRENCY,
                    needs_confirmation=not local and _host(base_url) != _host(chat_url),
                    owns_provider=True,
                )
            )
    if (
        chat_profile is not None
        and getattr(chat_profile, "supports_vision", False)
        and chat_provider is not None
        and chat_settings is not None
    ):
        local = is_loopback_url(getattr(chat_profile, "base_url", None))
        routes.append(
            OcrRoute(
                provider=chat_provider,
                settings=_ocr_settings(chat_settings),
                label=f"the chat model (profile {chat_name!r})",
                concurrency=LOCAL_CONCURRENCY if local else REMOTE_CONCURRENCY,
            )
        )
    if routes:
        return RouteDecision(routes=routes)
    return RouteDecision(unavailable=notes + unavailable_message(chat_name))


def _ocr_settings(base: CompletionSettings) -> CompletionSettings:
    """Transcription settings: vision on, temperature 0 when the profile
    sends one at all (some models reject any value)."""
    return CompletionSettings(
        model=base.model,
        temperature=0.0 if base.temperature is not None else None,
        supports_vision=True,
        max_tokens=base.max_tokens,
    )


ConvertPages = Callable[[Path, list[int]], Awaitable[ConversionResult]]


__all__ = [
    "LOCAL_CONCURRENCY",
    "OCR_CONFIRM_NAME",
    "OCR_PROMPT",
    "REMOTE_CONCURRENCY",
    "RENDER_LONGEST_SIDE_PX",
    "ConversionResult",
    "ConvertPages",
    "OcrRoute",
    "PageConversionError",
    "PdfPageConverter",
    "RouteDecision",
    "build_routes",
    "clean_page_markdown",
    "convert_page",
    "describe_route",
    "is_loopback_url",
    "render_page",
    "unavailable_message",
]
