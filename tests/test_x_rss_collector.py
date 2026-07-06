"""Nitter RSS collector tests."""

from __future__ import annotations

import httpx

from stock_sum.collectors.rss.x import X_RSS_SOURCE_TYPE, NitterRssXUserTimelineCollector, parse_nitter_rss_entries
from stock_sum.config.models import CollectorConfig, NitterRssProviderConfig
from stock_sum.core.context import RuntimeContext


FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <title>Serenity: NBIS update</title>
      <link>https://nitter.net/aleabitoreddit/status/2071074680253911267#m</link>
      <guid>https://nitter.net/aleabitoreddit/status/2071074680253911267#m</guid>
      <dc:creator>@aleabitoreddit</dc:creator>
      <pubDate>Mon, 06 Jul 2026 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;NBIS update&lt;/p&gt;&lt;img src="https://pbs.twimg.com/media/example.jpg" /&gt;</description>
      <enclosure url="https://pbs.twimg.com/media/example.jpg" type="image/jpeg" />
    </item>
  </channel>
</rss>
"""


MIXED_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Malformed</title>
      <link>https://nitter.net/aleabitoreddit</link>
      <description>No status link</description>
    </item>
    <item>
      <title>Serenity: valid</title>
      <link>https://nitter.net/aleabitoreddit/status/2071074680253911267#m</link>
      <pubDate>2026-07-06T10:00:00Z</pubDate>
      <description>Valid post</description>
    </item>
  </channel>
</rss>
"""


def test_parse_nitter_rss_entries() -> None:
    entries = parse_nitter_rss_entries(FEED_XML)

    assert len(entries) == 1
    assert entries[0].title == "Serenity: NBIS update"
    assert entries[0].author == "@aleabitoreddit"
    assert entries[0].link.endswith("/status/2071074680253911267#m")
    assert entries[0].enclosures == ["https://pbs.twimg.com/media/example.jpg"]


async def test_nitter_rss_collector_maps_posts_and_media() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=FEED_XML)

    collector = NitterRssXUserTimelineCollector(
        collector_id="x.aleabitoreddit",
        collector_config=CollectorConfig(kind=X_RSS_SOURCE_TYPE, handle="aleabitoreddit"),
        provider_config=NitterRssProviderConfig(retry_delay_seconds=0),
        transport=httpx.MockTransport(handler),
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert len(items) == 1
    assert seen_urls == ["https://nitter.net/aleabitoreddit/rss"]
    assert items[0].source_id == "2071074680253911267"
    assert items[0].source_type == X_RSS_SOURCE_TYPE
    assert items[0].url == "https://x.com/aleabitoreddit/status/2071074680253911267"
    assert items[0].text == "NBIS update"
    assert items[0].metadata["posted_at_text"] == "2026-07-06T10:00:00+00:00"
    assert items[0].metadata["media"][0]["url"] == "https://pbs.twimg.com/media/example.jpg"
    assert collector.api_responses[0].provider == "nitter_rss"


async def test_nitter_rss_malformed_item_warns_and_continues() -> None:
    collector = NitterRssXUserTimelineCollector(
        collector_id="x.aleabitoreddit",
        collector_config=CollectorConfig(kind=X_RSS_SOURCE_TYPE, handle="aleabitoreddit"),
        provider_config=NitterRssProviderConfig(retry_delay_seconds=0),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=MIXED_FEED_XML)),
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert [item.source_id for item in items] == ["2071074680253911267"]
    assert collector.warnings
    assert "Skipped malformed" in collector.warnings[0].message


async def test_nitter_rss_listing_failure_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, text=FEED_XML)

    collector = NitterRssXUserTimelineCollector(
        collector_id="x.aleabitoreddit",
        collector_config=CollectorConfig(kind=X_RSS_SOURCE_TYPE, handle="aleabitoreddit"),
        provider_config=NitterRssProviderConfig(retry_delay_seconds=0, max_retries=2),
        transport=httpx.MockTransport(handler),
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert calls == 3
    assert [item.source_id for item in items] == ["2071074680253911267"]
