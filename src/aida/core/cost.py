"""Rough token-cost estimation — a ballpark for the user's own running
total, not real provider billing.

AIDA's providers span free local models (Ollama, LM Studio) through metered
cloud APIs at their own, varying rates. B2 added a per-``ProviderProfile``
rate (``usd_per_m_input``/``usd_per_m_output``) for exactly this — callers
that have an active profile on hand should pass its rate through;
``estimate_cost_usd`` falls back to one fixed reference rate (the user's
own stated ballpark for a mid-tier cloud model) when the profile doesn't
set one, or when no profile is available at all, so a token count still
comes with *some* sense of scale rather than being a bare, uncontextualized
number — just no longer a falsely-precise one for a free local model.
"""

from __future__ import annotations

DEFAULT_INPUT_USD_PER_MILLION = 5.0
DEFAULT_OUTPUT_USD_PER_MILLION = 25.0


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> float:
    """``input_usd_per_million``/``output_usd_per_million`` are normally
    ``ProviderProfile.usd_per_m_input``/``usd_per_m_output`` — ``None``
    (unset on the profile, or no profile at hand) falls back to the fixed
    default rate, same as every call site before B2."""
    input_rate = input_usd_per_million if input_usd_per_million is not None else DEFAULT_INPUT_USD_PER_MILLION
    output_rate = output_usd_per_million if output_usd_per_million is not None else DEFAULT_OUTPUT_USD_PER_MILLION
    return input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate


__all__ = ["DEFAULT_INPUT_USD_PER_MILLION", "DEFAULT_OUTPUT_USD_PER_MILLION", "estimate_cost_usd"]
