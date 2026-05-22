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
    _demo_payload,
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
    def test_has_header(self):
        payload = _demo_payload()
        blocks = digest_blocks(payload)
        headers = [b for b in blocks if b.get("type") == "header"]
        assert len(headers) == 1
        assert "Digest" in headers[0]["text"]["text"]

    def test_action_ids_present(self):
        payload = _demo_payload()
        blocks = digest_blocks(payload)
        action_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert len(action_blocks) > 0
        ids = {el["action_id"] for b in action_blocks for el in b["elements"]}
        assert ids == {"boost", "save", "skip", "demote"}

    def test_button_value_contains_article_id_and_topic(self):
        article = _article()
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        action_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert action_blocks
        values = {el["value"] for el in action_blocks[0]["elements"]}
        assert all("|" in v for v in values)
        for v in values:
            article_id_part, _, topic_part = v.partition("|")
            assert article_id_part == "hn:abc123"
            assert topic_part == "topic_a"

    def test_article_url_in_section_text(self):
        article = _article(url="https://news.ycombinator.com/item?id=1")
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        section_texts = [
            b["text"]["text"] for b in blocks
            if b.get("type") == "section" and b.get("text", {}).get("type") == "mrkdwn"
        ]
        assert any("https://news.ycombinator.com" in t for t in section_texts)

    def test_topic_order_respected(self):
        a1, s1 = _article("hn:aaa"), _score("hn:aaa", "topic_b")
        a2, s2 = _article("hn:bbb"), _score("hn:bbb", "topic_a")
        payload = DigestPayload(
            items=[(a1, s1), (a2, s2)],
            topic_order=[TopicId("topic_a"), TopicId("topic_b")],
            topic_labels={
                TopicId("topic_a"): ("🔵", "Topic A"),
                TopicId("topic_b"): ("🟢", "Topic B"),
            },
        )
        blocks = digest_blocks(payload)
        header_sections = [
            b for b in blocks
            if b.get("type") == "section"
            and "Topic" in b.get("text", {}).get("text", "")
        ]
        assert len(header_sections) == 2
        assert "Topic A" in header_sections[0]["text"]["text"]
        assert "🔵" in header_sections[0]["text"]["text"]
        assert "Topic B" in header_sections[1]["text"]["text"]

    def test_counters_context_block_absent_when_none(self):
        article = _article()
        score = _score()
        payload = DigestPayload(items=[(article, score)], topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        assert not any(b.get("type") == "context" for b in blocks)

    def test_counters_context_block_present_when_set(self):
        article = _article()
        score = _score()
        counters = PipelineCounters(
            fetched=42, new=31, tagged=29, on_topic=18, scored=18, surfaced=3,
        )
        payload = DigestPayload(
            items=[(article, score)],
            topic_order=[TopicId("topic_a")],
            counters=counters,
        )
        blocks = digest_blocks(payload)
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        assert len(context_blocks) == 1
        text = context_blocks[0]["elements"][0]["text"]
        assert "42 fetched" in text
        assert "31 new" in text
        assert "29 tagged" in text
        assert "18 on-topic" in text
        assert "18 scored" in text
        assert "3 surfaced" in text

    def test_empty_topic_skipped(self):
        article = _article()
        score = _score(topic="topic_a")
        payload = DigestPayload(
            items=[(article, score)],
            topic_order=[TopicId("topic_a"), TopicId("topic_missing")],
            topic_labels={
                TopicId("topic_a"): ("🔵", "Topic A"),
                TopicId("topic_missing"): ("🟠", "Missing"),
            },
        )
        blocks = digest_blocks(payload)
        header_sections = [
            b for b in blocks
            if b.get("type") == "section"
            and ("Topic A" in b.get("text", {}).get("text", "")
                 or "Missing" in b.get("text", {}).get("text", ""))
        ]
        assert len(header_sections) == 1
        assert "Topic A" in header_sections[0]["text"]["text"]



class TestPriorityBlocks:
    def test_has_priority_header(self):
        payload = PriorityPayload(article=_article(), score=_score())
        blocks = priority_blocks(payload)
        assert blocks[0]["type"] == "header"
        assert "Priority" in blocks[0]["text"]["text"]

    def test_has_actions(self):
        payload = PriorityPayload(article=_article(), score=_score())
        blocks = priority_blocks(payload)
        assert any(b["type"] == "actions" for b in blocks)


class TestBlockLimit:
    def test_large_digest_stays_within_50_blocks_per_chunk(self):
        items = [
            (_article(f"hn:{'a' * 14}{i:02d}"), _score(f"hn:{'a' * 14}{i:02d}"))
            for i in range(30)
        ]
        payload = DigestPayload(items=items, topic_order=[TopicId("topic_a")])
        blocks = digest_blocks(payload)
        chunks = [blocks[i:i + 50] for i in range(0, len(blocks), 50)]
        assert len(chunks) > 1, "expected multiple chunks for 30 items"
        for chunk in chunks:
            assert len(chunk) <= 50


class TestRecapBlocks:
    def test_includes_top_items(self):
        items = [(a, s) for a, s in [(_article("hn:a1"), _score("hn:a1")), (_article("hn:a2"), _score("hn:a2"))]]
        payload = RecapPayload(top_items=items, skipped_but_high=[], window_days=7)
        blocks = recap_blocks(payload)
        action_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert len(action_blocks) == 2

    def test_skipped_section_absent_when_empty(self):
        payload = RecapPayload(top_items=[(_article(), _score())], skipped_but_high=[], window_days=7)
        blocks = recap_blocks(payload)
        section_texts = [b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"]
        assert not any("skipped" in t.lower() for t in section_texts)

    def test_skipped_section_present_when_nonempty(self):
        item = (_article(), _score())
        payload = RecapPayload(top_items=[item], skipped_but_high=[item], window_days=7)
        blocks = recap_blocks(payload)
        section_texts = [b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"]
        assert any("skipped" in t.lower() for t in section_texts)

    def test_window_days_in_header(self):
        payload = RecapPayload(top_items=[], skipped_but_high=[], window_days=14)
        blocks = recap_blocks(payload)
        header = next(b for b in blocks if b.get("type") == "header")
        assert "14" in header["text"]["text"]
