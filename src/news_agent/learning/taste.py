"""Pure taste logic: turn reading-list activity into a per-tag interest profile.

The profile is an exponential moving average over the tags of items the user
saves and reads in Safari. Reading an item is the strong signal (weight 1.0);
saving-without-reading is the weak one. The EMA lets recent reading dominate
over time, so taste tracks what you're into now, not what you read a year ago.
"""

from __future__ import annotations

from collections.abc import Iterable

from news_agent.core.types import Tag

# Taste learns from what an article is *about* and how good it is — not its
# shape. Format tags (news/essay/listicle/...) live in the `type` category and
# are excluded, so reading lots of listicles doesn't teach the agent to surface
# more listicles (which every topic's must_not_have already rejects).
TASTE_CATEGORIES = frozenset({"domain", "quality"})

# EMA smoothing. Lower = slower to move, more history retained. Tunable.
DEFAULT_ALPHA = 0.1

# How much a fully-read item pulls its tags up vs a merely-saved one.
READ_WEIGHT = 1.0
SAVED_WEIGHT = 0.25

# Scale of the interest boost the profile contributes to the digest gate.
DEFAULT_TASTE_K = 0.3


def reading_weight(*, is_read: bool) -> float:
    """Signal strength of one reading-list item: read >> merely saved."""
    return READ_WEIGHT if is_read else SAVED_WEIGHT


def interest_tags(
    tags: Iterable[Tag],
    category_lookup: dict[str, str],
) -> frozenset[Tag]:
    """Keep only the domain/quality tags that signal genuine interest, dropping
    format tags that merely describe an article's shape."""
    return frozenset(
        t for t in tags if category_lookup.get(str(t)) in TASTE_CATEGORIES
    )


def taste_update(
    weights: dict[str, float],
    tags: Iterable[Tag],
    item_weight: float,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, float]:
    """Fold one item's tags into the profile via EMA. Returns a new dict.

    Each tag moves toward `item_weight`: new = (1-alpha)*old + alpha*item_weight.
    A tag the user keeps reading converges to READ_WEIGHT; one they stop seeing
    decays as other items pull the average elsewhere.
    """
    out = dict(weights)
    for tag in tags:
        key = str(tag)
        old = out.get(key, 0.0)
        out[key] = (1.0 - alpha) * old + alpha * item_weight
    return out


def top_taste(weights: dict[str, float], *, n: int = 8) -> list[tuple[str, float]]:
    """The strongest interest tags, highest first."""
    return sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:n]


def uncovered_interest_tags(
    weights: dict[str, float],
    covered: set[str],
    category_lookup: dict[str, str],
    *,
    n: int = 5,
    floor: float = 0.3,
) -> list[str]:
    """Domain tags you read a lot that no topic's must_have_any captures —
    candidates for a new or widened topic. Quality tags are excluded (they're
    cross-cutting, not topics)."""
    domain = [
        (t, w) for t, w in weights.items()
        if category_lookup.get(t) == "domain" and t not in covered and w >= floor
    ]
    domain.sort(key=lambda kv: kv[1], reverse=True)
    return [t for t, _ in domain[:n]]


def taste_adjustment(
    tags: frozenset[Tag],
    weights: dict[str, float],
    *,
    k: float = DEFAULT_TASTE_K,
) -> float:
    """Interest boost for an article: k * mean taste-weight of its tags.

    Time-independent by design — it is added outside the recency/source-weight
    multiply so a topic you care about isn't decayed away. Returns 0.0 when the
    article has no tags or none are in the profile.
    """
    if not tags:
        return 0.0
    total = sum(weights.get(str(t), 0.0) for t in tags)
    return k * (total / len(tags))
