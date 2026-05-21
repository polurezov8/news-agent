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
    """Cost in whole US cents for one LLM call. Rounded down."""
    p_in, p_out = PRICING.get(model, _FALLBACK)
    return (input_tokens * p_in + output_tokens * p_out) // 1_000_000


def cents_to_dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"
