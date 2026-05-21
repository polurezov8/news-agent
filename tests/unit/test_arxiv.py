from __future__ import annotations

from pathlib import Path

from news_agent.core.types import SourceId
from news_agent.sources.arxiv import build_url, parse_atom

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.arxiv.atom"


def test_build_url_includes_categories_and_sort():
    url = build_url(["cs.AI", "cs.LG"], 25)
    assert "cat%3Acs.AI" in url
    assert "cat%3Acs.LG" in url
    assert "sortBy=submittedDate" in url
    assert "max_results=25" in url


def test_parse_atom_extracts_papers():
    arts = parse_atom(SourceId("arxiv"), FIXTURE.read_bytes())
    assert len(arts) == 2
    assert arts[0].title.startswith("A novel approach")
    assert arts[0].url.startswith("http://arxiv.org/abs/")
    assert arts[0].source == "arxiv"
