"""Shared vision-input support (B1): turning an on-disk ``ImageRef`` into
the base64 payload each provider's own content-block shape wraps, plus the
"how many, how big" policy both providers apply the same way.

Both ``to_anthropic_params`` and ``to_openai_messages`` face the same three
concerns — bound per-image token cost (downscale), bound total image count
as history grows (cap to the most recent few), and never let one stale or
unreadable path break a whole turn (skip it, don't raise) — so that logic
lives here once rather than being duplicated in ``anthropic_.py``/
``openai_compat.py``.
"""

from __future__ import annotations

import io
import mimetypes
from base64 import b64encode
from pathlib import Path

from aida.providers.base import ImageRef, Message

#: Longest edge an attached image is downscaled to before being sent as
#: vision input. Token cost for both providers' vision encoders scales
#: with pixel count, not file size — a full-resolution instrument camera
#: frame or a saved matplotlib figure at 2x DPI can otherwise cost more
#: tokens than the rest of the turn combined, for no visible benefit at
#: typical chat-window display size. Only applied when Pillow (the
#: ``docs`` extra) is importable; without it, the image is sent at its
#: original size rather than being dropped — a working (if pricier) vision
#: turn beats a silently-skipped image.
MAX_IMAGE_EDGE_PX = 1024

#: How many of the most recent image-bearing messages actually get their
#: pixels sent to the model per turn. A long analysis session can
#: accumulate dozens of plots; unconditionally resending every one, every
#: turn, would make token cost (and latency) grow with conversation length
#: rather than with what's actually relevant right now. Every image stays
#: described in its originating message's text (aida.artifacts.policy) —
#: this only bounds how many also get their real pixels resent.
MAX_ATTACHED_IMAGES = 4


def images_within_cap(messages: list[Message], *, max_images: int = MAX_ATTACHED_IMAGES) -> set[int]:
    """Indices (into ``messages``) of the most recent ``max_images``
    messages that carry at least one image — the set a translation
    function should actually attach pixels for. Every other image-bearing
    message further back keeps its text description only, same as before
    B1 existed."""
    if max_images <= 0:
        return set()
    bearing = [i for i, m in enumerate(messages) if m.images]
    return set(bearing[-max_images:])


def read_image_b64(ref: ImageRef) -> tuple[str, str] | None:
    """Read, downscale, and base64-encode one attached image.

    Returns ``(mime_type, base64_data)``, or ``None`` if the file can't be
    read at all (moved/deleted since the message was recorded, or the path
    was never valid) — the caller drops that one image rather than failing
    the whole turn over a stale path.
    """
    path = Path(ref.path)
    mime_type = ref.mime_type or mimetypes.guess_type(ref.path)[0] or "image/png"
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    try:
        from PIL import Image  # optional: the `docs` extra

        with Image.open(io.BytesIO(raw)) as img:
            img.load()  # force the decode now, inside this try, not lazily later
            if max(img.size) > MAX_IMAGE_EDGE_PX:
                img.thumbnail((MAX_IMAGE_EDGE_PX, MAX_IMAGE_EDGE_PX))
                fmt = "JPEG" if img.mode == "RGB" else "PNG"
                if fmt == "JPEG" and img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format=fmt)
                raw = buf.getvalue()
                mime_type = "image/jpeg" if fmt == "JPEG" else "image/png"
    except ImportError:
        pass  # Pillow not installed — send the original bytes/size
    except Exception:
        pass  # corrupt/unreadable image data — fall back to the raw bytes

    return mime_type, b64encode(raw).decode("ascii")


__all__ = ["MAX_ATTACHED_IMAGES", "MAX_IMAGE_EDGE_PX", "images_within_cap", "read_image_b64"]
