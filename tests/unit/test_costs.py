from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from news_agent.llm.costs import PRICING, cents_to_dollars, cost_microcents, usd_cents
from news_agent.storage.repository import (
    connect,
    init_db,
    monthly_usd_cents,
    save_llm_cost,
)


class TestUsdCents:
    def test_haiku_input_only(self):
        # Haiku: 80 cents per 1M input tokens → 1M tokens = 80 cents
        assert usd_cents("claude-haiku-4-5-20251001", 1_000_000, 0) == 80

    def test_haiku_output_only(self):
        # 400 cents per 1M output → 1M tokens = 400 cents
        assert usd_cents("claude-haiku-4-5-20251001", 0, 1_000_000) == 400

    def test_sonnet_combined(self):
        # 300 in + 1500 out, 1M each
        assert usd_cents("claude-sonnet-4-6", 1_000_000, 1_000_000) == 1800

    def test_small_call_rounds_down(self):
        # Tiny call: 100 input + 50 output Haiku
        # cents = (100 * 80 + 50 * 400) / 1_000_000 = 28000 / 1M = 0 cents
        assert usd_cents("claude-haiku-4-5-20251001", 100, 50) == 0

    def test_unknown_model_uses_fallback(self):
        # Fallback is Sonnet-level pricing — conservative
        assert usd_cents("unknown-model", 1_000_000, 0) > 0


class TestCostMicrocents:
    def test_keeps_subcent_precision(self):
        # 100 in + 50 out Haiku = 28_000 microcents — usd_cents floors this to 0.
        assert cost_microcents("claude-haiku-4-5-20251001", 100, 50) == 28_000
        assert usd_cents("claude-haiku-4-5-20251001", 100, 50) == 0

    def test_one_cent_at_1e6(self):
        # 1M microcents == 1 cent. Haiku 12_500 input = 1_000_000 microcents.
        assert cost_microcents("claude-haiku-4-5-20251001", 12_500, 0) == 1_000_000


class TestCentsToDollars:
    def test_zero(self):
        assert cents_to_dollars(0) == "$0.00"

    def test_two_dollars(self):
        assert cents_to_dollars(200) == "$2.00"

    def test_fractional(self):
        assert cents_to_dollars(42) == "$0.42"


class TestMonthlySpend:
    def test_empty_db_zero(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert monthly_usd_cents(conn, now=datetime.now(timezone.utc)) == 0
        finally:
            conn.close()

    def test_sums_within_month(self, tmp_path: Path):
        # Recomputed from tokens, not the (always-0) stored usd_cents column.
        # haiku 1M in = 80¢; sonnet 1M in + 1M out = 1800¢ → 1880¢.
        db = tmp_path / "t.db"
        init_db(db)
        now = datetime.now(timezone.utc)
        conn = connect(db)
        try:
            save_llm_cost(
                conn, model="claude-haiku-4-5-20251001",
                input_tokens=1_000_000, output_tokens=0,
                usd_cents=0, purpose="tag", article_id=None, at=now,
            )
            save_llm_cost(
                conn, model="claude-sonnet-4-6",
                input_tokens=1_000_000, output_tokens=1_000_000,
                usd_cents=0, purpose="score", article_id=None, at=now,
            )
            conn.commit()
            assert monthly_usd_cents(conn, now=now) == 1880
        finally:
            conn.close()

    def test_subcent_calls_accumulate(self, tmp_path: Path):
        # The original bug: each call floored to 0¢, so a month summed to $0 and
        # the budget gate never tripped. 100 sub-cent haiku calls must now total.
        db = tmp_path / "t.db"
        init_db(db)
        now = datetime.now(timezone.utc)
        conn = connect(db)
        try:
            for _ in range(100):
                save_llm_cost(
                    conn, model="claude-haiku-4-5-20251001",
                    input_tokens=1000, output_tokens=0,
                    usd_cents=0, purpose="tag", article_id=None, at=now,
                )
            conn.commit()
            # 100 * (1000 * 80) = 8_000_000 microcents = 8¢.
            assert monthly_usd_cents(conn, now=now) == 8
        finally:
            conn.close()

    def test_excludes_prior_months(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        now = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
        last_month = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        conn = connect(db)
        try:
            save_llm_cost(
                conn, model="claude-haiku-4-5-20251001",
                input_tokens=9_000_000, output_tokens=0, usd_cents=0,
                purpose="tag", article_id=None, at=last_month,
            )
            save_llm_cost(
                conn, model="claude-haiku-4-5-20251001",
                input_tokens=1_000_000, output_tokens=0, usd_cents=0,
                purpose="tag", article_id=None, at=now,
            )
            conn.commit()
            assert monthly_usd_cents(conn, now=now) == 80
        finally:
            conn.close()


class TestPricingTable:
    def test_models_listed(self):
        assert "claude-haiku-4-5-20251001" in PRICING
        assert "claude-sonnet-4-6" in PRICING

    def test_sonnet_more_expensive_than_haiku(self):
        h_in, h_out = PRICING["claude-haiku-4-5-20251001"]
        s_in, s_out = PRICING["claude-sonnet-4-6"]
        assert s_in > h_in
        assert s_out > h_out
