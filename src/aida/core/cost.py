"""Rough token-cost estimation — a ballpark for the user's own running
total, not real provider billing.

AIDA's providers span free local models (Ollama, LM Studio) through metered
cloud APIs at their own, varying rates — there is no single "real" price
this module could compute today (a per-``ProviderProfile`` rate is future
work, not attempted here). ``estimate_cost_usd`` uses one fixed reference
rate (the user's own stated ballpark for a mid-tier cloud model) purely so a
token count comes with *some* sense of scale rather than being a bare,
uncontextualized number.
"""

from __future__ import annotations

DEFAULT_INPUT_USD_PER_MILLION = 5.0
DEFAULT_OUTPUT_USD_PER_MILLION = 25.0


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * DEFAULT_INPUT_USD_PER_MILLION
        + output_tokens / 1_000_000 * DEFAULT_OUTPUT_USD_PER_MILLION
    )


__all__ = ["DEFAULT_INPUT_USD_PER_MILLION", "DEFAULT_OUTPUT_USD_PER_MILLION", "estimate_cost_usd"]
