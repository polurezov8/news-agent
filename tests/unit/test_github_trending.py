from __future__ import annotations

from pathlib import Path

from news_agent.core.types import SourceId
from news_agent.sources.github_trending import build_url, parse_trending_html

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.github_trending.html"


def test_build_url_with_language():
    assert build_url("Swift", "daily") == "https://github.com/trending/Swift?since=daily"


def test_build_url_without_language():
    assert build_url(None, "weekly") == "https://github.com/trending?since=weekly"


def test_parse_trending_extracts_repos_and_stars():
    pairs = parse_trending_html(SourceId("gh"), FIXTURE.read_text())
    assert len(pairs) == 3
    titles = {a.title for a, _ in pairs}
    assert "octocat/hello-world" in titles
    assert "another/big-repo" in titles

    by_title = {a.title: stars for a, stars in pairs}
    assert by_title["octocat/hello-world"] == 250
    assert by_title["example/lowtraction"] == 10
    assert by_title["another/big-repo"] == 1234


def test_parsed_articles_have_correct_url():
    pairs = parse_trending_html(SourceId("gh"), FIXTURE.read_text())
    by_title = {a.title: a.url for a, _ in pairs}
    assert by_title["octocat/hello-world"] == "https://github.com/octocat/hello-world"


def test_parse_trending_weekly_stars():
    html = """
    <main>
    <article class="Box-row">
      <h2 class="h3 lh-condensed"><a href="/foo/bar">foo/bar</a></h2>
      <span>500 stars this week</span>
    </article>
    <article class="Box-row">
      <h2 class="h3 lh-condensed"><a href="/baz/qux">baz/qux</a></h2>
      <span>1,200 stars this month</span>
    </article>
    </main>
    """
    pairs = parse_trending_html(SourceId("gh"), html)
    by_title = {a.title: stars for a, stars in pairs}
    assert by_title["foo/bar"] == 500
    assert by_title["baz/qux"] == 1200


def test_build_url_encodes_special_language():
    assert build_url("C#", "daily") == "https://github.com/trending/C%23?since=daily"
    assert build_url("F#", "weekly") == "https://github.com/trending/F%23?since=weekly"
