from __future__ import annotations

import pytest

from news_agent.core.types import CorrectionKind
from news_agent.learning.priors import prior_delta, updated_prior


class TestPriorDelta:
    def test_save_strongest_positive(self):
        assert prior_delta(CorrectionKind.SAVE) > prior_delta(CorrectionKind.BOOST)

    def test_demote_negative(self):
        assert prior_delta(CorrectionKind.DEMOTE) < 0

    def test_skip_gentler_than_demote(self):
        assert prior_delta(CorrectionKind.DEMOTE) < prior_delta(CorrectionKind.SKIP) < 0

    def test_retag_zero(self):
        assert prior_delta(CorrectionKind.RETAG) == 0.0

    def test_scales_with_lr(self):
        small = prior_delta(CorrectionKind.BOOST, lr=0.01)
        big = prior_delta(CorrectionKind.BOOST, lr=0.1)
        assert big == pytest.approx(10 * small)


class TestUpdatedPrior:
    def test_boost_increases(self):
        new = updated_prior(0.5, CorrectionKind.BOOST)
        assert new > 0.5

    def test_demote_decreases(self):
        new = updated_prior(0.5, CorrectionKind.DEMOTE)
        assert new < 0.5

    def test_save_increases_more_than_boost(self):
        save_new = updated_prior(0.5, CorrectionKind.SAVE)
        boost_new = updated_prior(0.5, CorrectionKind.BOOST)
        assert save_new > boost_new

    def test_clamped_above_one(self):
        assert updated_prior(0.99, CorrectionKind.SAVE) == 1.0

    def test_clamped_below_zero(self):
        assert updated_prior(0.01, CorrectionKind.DEMOTE) == 0.0

    def test_retag_no_change(self):
        assert updated_prior(0.42, CorrectionKind.RETAG) == 0.42

    def test_repeated_boost_converges_to_one(self):
        p = 0.5
        for _ in range(50):
            p = updated_prior(p, CorrectionKind.BOOST)
        assert p == 1.0
