"""Reddit RSS collector tests."""

from __future__ import annotations

import httpx

from stock_sum.collectors.rss.reddit import REDDIT_RSS_SOURCE_TYPE, RedditRssSubredditCollector, parse_reddit_rss_entries
from stock_sum.config.models import CollectorConfig, RedditRssProviderConfig
from stock_sum.core.context import RuntimeContext


LISTING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_abc</id>
    <title>Post title</title>
    <author><name>/u/poster</name></author>
    <published>2026-07-06T10:00:00+00:00</published>
    <updated>2026-07-06T10:00:00+00:00</updated>
    <link href="https://www.reddit.com/r/wallstreetbets/comments/abc/post_title/" />
    <content type="html">&lt;p&gt;Post body&lt;/p&gt;</content>
  </entry>
</feed>
"""


COMMENTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_abc</id>
    <title>Post title</title>
    <author><name>/u/poster</name></author>
    <published>2026-07-06T10:00:00+00:00</published>
    <link href="https://www.reddit.com/r/wallstreetbets/comments/abc/post_title/" />
    <content type="html">&lt;p&gt;Post body&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>t1_c1</id>
    <title>comment by user</title>
    <author><name>/u/commenter</name></author>
    <published>2026-07-06T10:05:00+00:00</published>
    <link href="https://www.reddit.com/r/wallstreetbets/comments/abc/post_title/comment/c1/" />
    <content type="html">&lt;p&gt;Comment body&lt;/p&gt;</content>
  </entry>
</feed>
"""


def test_parse_reddit_rss_entries() -> None:
    entries = parse_reddit_rss_entries(LISTING_XML)

    assert len(entries) == 1
    assert entries[0].entry_id == "t3_abc"
    assert entries[0].title == "Post title"
    assert entries[0].author == "/u/poster"
    assert entries[0].link.endswith("/comments/abc/post_title/")


async def test_reddit_rss_collector_stores_listing_and_comments() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if str(request.url).endswith("/r/wallstreetbets.rss?limit=100"):
            return httpx.Response(200, text=LISTING_XML)
        if "/comments/abc/post_title.rss?limit=500" in str(request.url):
            return httpx.Response(200, text=COMMENTS_XML)
        return httpx.Response(404, text="missing")

    collector = RedditRssSubredditCollector(
        collector_id="reddit.wallstreetbets",
        collector_config=CollectorConfig(kind=REDDIT_RSS_SOURCE_TYPE, subreddit="wallstreetbets"),
        provider_config=RedditRssProviderConfig(retry_delay_seconds=0),
        transport=httpx.MockTransport(handler),
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert [item.metadata["entity_type"] for item in items] == ["reddit_post", "reddit_comment"]
    assert items[0].source_id == "abc"
    assert items[0].source_type == REDDIT_RSS_SOURCE_TYPE
    assert items[0].metadata["author"] == "poster"
    assert items[0].text == "Post body"
    assert items[1].source_id == "abc:c1"
    assert items[1].metadata["author"] == "commenter"
    assert items[1].text == "Comment body"
    assert len(collector.api_responses) == 2
    assert collector.api_responses[0].provider == "reddit_rss"
    assert seen_urls[0].endswith("limit=100")


async def test_reddit_rss_comment_failure_warns_and_continues() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if str(request.url).endswith("/r/wallstreetbets.rss?limit=100"):
            return httpx.Response(200, text=LISTING_XML)
        calls += 1
        return httpx.Response(429, text="slow down")

    collector = RedditRssSubredditCollector(
        collector_id="reddit.wallstreetbets",
        collector_config=CollectorConfig(kind=REDDIT_RSS_SOURCE_TYPE, subreddit="wallstreetbets"),
        provider_config=RedditRssProviderConfig(retry_delay_seconds=0, max_retries=2),
        transport=httpx.MockTransport(handler),
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert [item.metadata["entity_type"] for item in items] == ["reddit_post"]
    assert calls == 3
    assert collector.warnings
    assert "comments failed" in collector.warnings[0].message
