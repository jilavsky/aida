from __future__ import annotations

from aida.core.cost import (
    DEFAULT_INPUT_USD_PER_MILLION,
    DEFAULT_OUTPUT_USD_PER_MILLION,
    estimate_cost_usd,
)


def test_estimate_cost_usd_zero_tokens_is_zero():
    assert estimate_cost_usd(0, 0) == 0.0


def test_estimate_cost_usd_uses_the_documented_reference_rates():
    cost = estimate_cost_usd(1_000_000, 1_000_000)
    assert cost == DEFAULT_INPUT_USD_PER_MILLION + DEFAULT_OUTPUT_USD_PER_MILLION


def test_estimate_cost_usd_scales_linearly():
    assert estimate_cost_usd(500_000, 0) == DEFAULT_INPUT_USD_PER_MILLION / 2
    assert estimate_cost_usd(0, 250_000) == DEFAULT_OUTPUT_USD_PER_MILLION / 4
