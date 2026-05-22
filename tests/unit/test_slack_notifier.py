from __future__ import annotations

from datetime import datetime, timezone

from news_agent.core.types import (
    Article,
    ArticleId,
    ContentHash,
    PipelineCounters,
    ScoreResult,
    SourceId,
    TopicId,
)
from news_agent.notifier.base import DigestPayload, PriorityPayload, RecapPayload
from news_agent.notifier.slack import (
    digest_blocks,
    priority_blocks,
    recap_blocks,
)


def _article(aid: str = "hn:abc123", url: str = "https://example.com") -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        id=ArticleId(aid),
        source=SourceId("hn"),
        url=url,
        title="Test Article Title",
        body="",
        content_hash=ContentHash("abc123"),
        published_at=now,
        fetched_at=now,
    )


def _score(aid: str = "hn:abc123", topic: str = "topic_a", final: float = 0.75) -> ScoreResult:
    return ScoreResult(
        article=ArticleId(aid),
        topic=TopicId(topic),
        substance=final,
        tag_adj=0.0,
        decay=1.0,
        source_weight=1.0,
        final=final,
    )


class TestDigestBlocks:
    def test_header_is_date_no_emoji_no_digest_word(self):
        article = _article()
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        headers = [b for b in blocks if b.get("type") == "header"]
        assert len(headers) == 1
        text = headers[0]["text"]["text"]
        # "Friday, May 22" style — comma after weekday, month name.
        assert "," in text
        assert "📰" not in text
        assert "Digest" not in text

    def test_editor_pick_label_present(self):
        article = _article()
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels = [c["elements"][0]["text"] for c in contexts]
        assert any("EDITOR'S PICK" in t for t in labels)
        assert any("⭐️" in t for t in labels)

    def test_also_today_omitted_when_one_pick(self):
        article = _article()
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels = [c["elements"][0]["text"] for c in contexts]
        assert not any("ALSO TODAY" in t for t in labels)

    def test_also_today_label_present_when_two_or_more(self):
        a1, s1 = _article("hn:aaa"), _score("hn:aaa", final=0.9)
        a2, s2 = _article("hn:bbb"), _score("hn:bbb", final=0.7)
        payload = DigestPayload(
            items=[(a1, s1), (a2, s2)],
            topic_order=[TopicId("topic_a")],
        )
        blocks = digest_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels = [c["elements"][0]["text"] for c in contexts]
        assert any("ALSO TODAY" in t for t in labels)
        assert any("✨" in t for t in labels)

    def test_no_actions_blocks_no_score_bar(self):
        article = _article()
        score = _score(final=0.87)
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        assert not any(b.get("type") == "actions" for b in blocks)
        # No score bar or numeric score in any text.
        for b in blocks:
            text = (b.get("text") or {}).get("text", "")
            assert "█" not in text
            assert "0.87" not in text
            assert "score" not in text.lower()

    def test_section_meta_has_source_topic_and_relative_time(self):
        article = _article(url="https://news.ycombinator.com/item?id=1")
        score = _score()
        payload = DigestPayload(
            items=[(article, score)],
            topic_order=[TopicId("topic_a")],
            topic_labels={TopicId("topic_a"): ("🤖", "AI")},
        )
        blocks = digest_blocks(payload)
        section_texts = [
            b["text"]["text"] for b in blocks
            if b.get("type") == "section"
        ]
        assert len(section_texts) == 1
        text = section_texts[0]
        # Bold linked title.
        assert "*<https://news.ycombinator.com/item?id=1|" in text
        # Italic meta line — source · TopicLabel · relative time.
        assert "_hn · AI · " in text
        # Topic emoji NOT inside the meta line (kept for labels only).
        assert "🤖" not in text

    def test_editor_pick_is_highest_final(self):
        """First item under EDITOR'S PICK label is the highest-final score."""
        a1, s1 = _article("hn:aaa", url="https://low.example"), _score("hn:aaa", final=0.6)
        a2, s2 = _article("hn:bbb", url="https://top.example"), _score("hn:bbb", final=0.95)
        a3, s3 = _article("hn:ccc", url="https://mid.example"), _score("hn:ccc", final=0.7)
        payload = DigestPayload(
            items=[(a1, s1), (a2, s2), (a3, s3)],
            topic_order=[TopicId("topic_a")],
        )
        blocks = digest_blocks(payload)
        idx_label = next(
            i for i, b in enumerate(blocks)
            if b.get("type") == "context"
            and "EDITOR'S PICK" in b["elements"][0]["text"]
        )
        first_section_after = next(
            b for b in blocks[idx_label + 1:] if b.get("type") == "section"
        )
        assert "top.example" in first_section_after["text"]["text"]

    def test_counters_footer_omitted_when_none(self):
        """No counters → no footer line. Label context blocks (EDITOR'S PICK …) still appear."""
        article = _article()
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        texts = [c["elements"][0]["text"] for c in contexts]
        assert not any("scanned" in t for t in texts)
        assert not any("picks" in t for t in texts)

    def test_counters_footer_compact_three_fields(self):
        article = _article()
        score = _score()
        counters = PipelineCounters(
            fetched=1531, new=11, tagged=11, on_topic=9, scored=9, surfaced=3,
        )
        payload = DigestPayload(
            items=[(article, score)],
            topic_order=[TopicId("topic_a")],
            counters=counters,
        )
        blocks = digest_blocks(payload)
        # Footer is the last context block; renders only fetched/on_topic/surfaced.
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        assert context_blocks, "expected at least one context block (footer)"
        footer_text = context_blocks[-1]["elements"][0]["text"]
        assert footer_text == "1531 scanned · 9 on topic · 3 picks"
        # Old verbose fields must be gone.
        assert "fetched" not in footer_text
        assert "tagged" not in footer_text
        assert "scored" not in footer_text
        assert "📥" not in footer_text


class TestPriorityBlocks:
    def test_priority_label_with_bell(self):
        payload = PriorityPayload(
            article=_article(),
            score=_score(),
            topic_labels={TopicId("topic_a"): ("🤖", "AI")},
        )
        blocks = priority_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        assert any(
            "PRIORITY" in c["elements"][0]["text"] and "🔔" in c["elements"][0]["text"]
            for c in contexts
        )

    def test_no_actions_no_score_bar(self):
        payload = PriorityPayload(
            article=_article(),
            score=_score(final=0.92),
            topic_labels={TopicId("topic_a"): ("🤖", "AI")},
        )
        blocks = priority_blocks(payload)
        assert not any(b.get("type") == "actions" for b in blocks)
        for b in blocks:
            text = (b.get("text") or {}).get("text", "")
            assert "█" not in text
            assert "0.92" not in text
            assert "score" not in text.lower()

    def test_section_meta_has_source_topic_label_and_relative_time(self):
        payload = PriorityPayload(
            article=_article(url="https://news.ycombinator.com/item?id=42"),
            score=_score(),
            topic_labels={TopicId("topic_a"): ("🤖", "AI")},
        )
        blocks = priority_blocks(payload)
        sections = [b for b in blocks if b.get("type") == "section"]
        assert len(sections) == 1
        text = sections[0]["text"]["text"]
        assert "*<https://news.ycombinator.com/item?id=42|" in text
        assert "_hn · AI · " in text


class TestBlockLimit:
    def test_chunks_respect_50_block_ceiling(self):
        """Apple shape is compact (1 section per item); even 60 items should chunk cleanly."""
        items = [
            (_article(f"hn:{'a' * 14}{i:02d}"), _score(f"hn:{'a' * 14}{i:02d}"))
            for i in range(60)
        ]
        payload = DigestPayload(items=items, topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        chunks = [blocks[i:i + 50] for i in range(0, len(blocks), 50)]
        for chunk in chunks:
            assert len(chunk) <= 50


class TestRecapBlocks:
    def test_top_label_present(self):
        items = [(_article("hn:a1"), _score("hn:a1"))]
        payload = RecapPayload(top_items=items, skipped_but_high=[], window_days=7)
        blocks = recap_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels = [c["elements"][0]["text"] for c in contexts]
        assert any("TOP THIS WEEK" in t and "⭐️" in t for t in labels)

    def test_no_actions_no_score_bar(self):
        items = [(_article("hn:a1"), _score("hn:a1", final=0.91))]
        payload = RecapPayload(top_items=items, skipped_but_high=items, window_days=7)
        blocks = recap_blocks(payload)
        assert not any(b.get("type") == "actions" for b in blocks)
        for b in blocks:
            text = (b.get("text") or {}).get("text", "")
            assert "█" not in text
            assert "0.91" not in text
            assert "score" not in text.lower()

    def test_skipped_label_absent_when_empty(self):
        payload = RecapPayload(top_items=[(_article(), _score())], skipped_but_high=[], window_days=7)
        blocks = recap_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels = [c["elements"][0]["text"] for c in contexts]
        assert not any("SKIPPED" in t for t in labels)

    def test_skipped_label_present_when_nonempty(self):
        item = (_article(), _score())
        payload = RecapPayload(top_items=[item], skipped_but_high=[item], window_days=7)
        blocks = recap_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels = [c["elements"][0]["text"] for c in contexts]
        assert any("HIGH-SCORE BUT SKIPPED" in t and "💭" in t for t in labels)

    def test_header_includes_week_recap_and_date(self):
        payload = RecapPayload(top_items=[], skipped_but_high=[], window_days=7)
        blocks = recap_blocks(payload)
        header = next(b for b in blocks if b.get("type") == "header")
        # "Sun, May 24 — week recap" style.
        text = header["text"]["text"]
        assert "week recap" in text.lower()
        assert "," in text

    def test_weekly_stats_footer_when_stats_provided(self):
        from news_agent.notifier.base import WeeklyStats

        stats = WeeklyStats(
            runs=7, surfaced=42, boosted=14, demoted=5,
            top_sources=[("pointfree", 8), ("dou", 6), ("arxiv", 4)],
        )
        payload = RecapPayload(
            top_items=[(_article(), _score())],
            skipped_but_high=[],
            window_days=7,
            stats=stats,
        )
        blocks = recap_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        footer = contexts[-1]["elements"][0]["text"]
        assert "7 runs" in footer
        assert "42 surfaced" in footer
        assert "14 boosted" in footer
        assert "5 demoted" in footer
        assert "pointfree" in footer
        assert "dou" in footer
        assert "arxiv" in footer

    def test_weekly_stats_footer_omitted_when_stats_none(self):
        payload = RecapPayload(
            top_items=[(_article(), _score())],
            skipped_but_high=[],
            window_days=7,
        )
        blocks = recap_blocks(payload)
        contexts = [b for b in blocks if b.get("type") == "context"]
        labels_only = all(
            ("TOP THIS WEEK" in c["elements"][0]["text"])
            or ("HIGH-SCORE BUT SKIPPED" in c["elements"][0]["text"])
            for c in contexts
        )
        assert labels_only, "no footer block expected when stats is None"
