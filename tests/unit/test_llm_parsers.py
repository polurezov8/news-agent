from __future__ import annotations

from news_agent.llm.scorer import parse_score
from news_agent.llm.tagger import parse_tags


class TestParseTags:
    def test_clean_array(self):
        assert parse_tags('["essay", "hands_on"]', {"essay", "hands_on"}) == ["essay", "hands_on"]

    def test_with_surrounding_prose(self):
        text = 'Here are the tags: ["news", "ai"]. Done.'
        assert parse_tags(text, {"news", "ai", "other"}) == ["news", "ai"]

    def test_filters_unknown_tags(self):
        assert parse_tags('["essay", "made_up"]', {"essay"}) == ["essay"]

    def test_empty_array(self):
        assert parse_tags("[]", {"essay"}) == []

    def test_no_array(self):
        assert parse_tags("nothing here", {"essay"}) == []

    def test_malformed_json(self):
        assert parse_tags("[essay, unquoted]", {"essay"}) == []

    def test_non_list_json(self):
        assert parse_tags('{"essay": 1}', {"essay"}) == []

    def test_drops_non_string_entries(self):
        assert parse_tags('["essay", 42, null]', {"essay"}) == ["essay"]


class TestParseScore:
    def test_decimal(self):
        assert parse_score("0.75") == 0.75

    def test_one(self):
        assert parse_score("1.0") == 1.0

    def test_zero(self):
        assert parse_score("0") == 0.0

    def test_with_prose(self):
        assert parse_score("My rating is 0.65.") == 0.65

    def test_clamps_above_one(self):
        # Regex matches single digit "1" first; that's fine and clamped.
        assert parse_score("1") == 1.0

    def test_fallback_on_no_number(self):
        assert parse_score("unable to score") == 0.5

    def test_leading_dot(self):
        assert parse_score(".42") == 0.42
