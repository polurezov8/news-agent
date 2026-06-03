"""LLM pricing table. Cents-per-1M-tokens to avoid float drift.

Update when Anthropic changes pricing. Keep precision: int cents, not float dollars.
"""

from __future__ import annotations

# (input_cents_per_million_tokens, output_cents_per_million_tokens)
PRICING: dict[str, tuple[int, int]] = {
    "claude-haiku-4-5-20251001": (80, 400),
    "claude-sonnet-4-6": (300, 1500),
}

_FALLBACK = (300, 1500)  # conservative if model unknown


def usd_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    """Cost in whole US cents for one LLM call. Rounded down.

    Note: every real per-call cost is sub-cent, so this floors to 0 — fine for a
    single-row display, useless for a running total. Sum `cost_microcents` and
    divide once instead (see repository.monthly_usd_cents)."""
    p_in, p_out = PRICING.get(model, _FALLBACK)
    return (input_tokens * p_in + output_tokens * p_out) // 1_000_000


def cost_microcents(model: str, input_tokens: int, output_tokens: int) -> int:
    """Cost of one call in millionths of a cent — the undivided numerator.

    Keeping full precision per call and dividing only at the aggregate is what
    makes a month of sub-cent calls add up to a real figure (1e6 = 1¢, 1e8 = $1).
    """
    p_in, p_out = PRICING.get(model, _FALLBACK)
    return input_tokens * p_in + output_tokens * p_out


def cents_to_dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"
