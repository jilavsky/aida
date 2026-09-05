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


def test_estimate_cost_usd_uses_a_profile_rate_override_when_given():
    """B2: a profile with its own usd_per_m_input/usd_per_m_output (e.g. a
    free local model at $0) must not be priced at the fixed cloud-model
    default rate."""
    cost = estimate_cost_usd(
        1_000_000, 1_000_000, input_usd_per_million=0.0, output_usd_per_million=0.0
    )
    assert cost == 0.0


def test_estimate_cost_usd_override_replaces_only_the_given_rate():
    cost = estimate_cost_usd(1_000_000, 0, input_usd_per_million=1.0)
    assert cost == 1.0  # not DEFAULT_INPUT_USD_PER_MILLION


def test_estimate_cost_usd_none_override_falls_back_to_default():
    """A profile that never set usd_per_m_input/usd_per_m_output (None,
    the field's default) must behave exactly as before B2."""
    cost = estimate_cost_usd(
        1_000_000, 1_000_000, input_usd_per_million=None, output_usd_per_million=None
    )
    assert cost == DEFAULT_INPUT_USD_PER_MILLION + DEFAULT_OUTPUT_USD_PER_MILLION
