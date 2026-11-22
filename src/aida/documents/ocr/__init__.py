"""Converting PDF pages that have no usable text layer.

``vlm`` renders such pages and sends them to a model with vision through the
ordinary provider layer; ``common`` pairs figure captions from the resulting
reading-order Markdown. Without a model that can see, nothing is converted
and the reader says so — see ``aida.documents.ocr.vlm``.
"""

from aida.documents.ocr.common import apply_page_captions
from aida.documents.ocr.vlm import (
    ConversionResult,
    PdfPageConverter,
    RouteDecision,
    build_routes,
    describe_route,
)

__all__ = [
    "ConversionResult",
    "PdfPageConverter",
    "RouteDecision",
    "apply_page_captions",
    "build_routes",
    "describe_route",
]
