"""Pure learning logic: turn corrections into prior-weight updates."""

from __future__ import annotations

from news_agent.core.types import CorrectionKind

# Learning rate. Lower = more conservative. Tunable.
DEFAULT_LR = 0.05

# Per-correction delta in units of LR. SAVE > BOOST > DEMOTE > SKIP (gentlest signal).
_DELTAS: dict[CorrectionKind, float] = {
    CorrectionKind.SAVE: +2.0,
    CorrectionKind.BOOST: +1.0,
    CorrectionKind.SKIP: -0.5,
    CorrectionKind.DEMOTE: -1.0,
    CorrectionKind.RETAG: 0.0,        # routing concern, not prior
}


def prior_delta(kind: CorrectionKind, *, lr: float = DEFAULT_LR) -> float:
    return lr * _DELTAS.get(kind, 0.0)


def updated_prior(current: float, kind: CorrectionKind, *, lr: float = DEFAULT_LR) -> float:
    """Apply a correction to a (source, topic) prior weight. Clamped to [0, 1]."""
    return max(0.0, min(1.0, current + prior_delta(kind, lr=lr)))
