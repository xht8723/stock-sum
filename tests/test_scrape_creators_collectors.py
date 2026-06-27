"""Scrape Creators collector tests."""

from stock_sum.collectors.api.scrape_creators import (
    REDDIT_SOURCE_TYPE,
    X_SOURCE_TYPE,
    ScrapeCreatorsRedditSubredditCollector,
    ScrapeCreatorsXUserTweetsCollector,
)
from stock_sum.config.models import CollectorConfig, ScrapeCreatorsProviderConfig
from stock_sum.core.context import RuntimeContext


class FakeScrapeCreatorsClient:
    def __init__(self):
        self.comment_calls = []

    async def twitter_user_tweets(self, *, handle: str, trim: bool):
        return {
            "tweets": [
                {
                    "id": "1",
                    "text": "first",
                    "url": "https://x.com/aleabitoreddit/status/1",
                    "author": {"userName": "aleabitoreddit", "name": "Ale"},
                    "entities": {"media": [{"media_url_https": "https://cdn.example/1.jpg", "type": "photo"}]},
                },
                {"id": "2", "text": "second"},
            ]
        }

    async def reddit_subreddit(self, *, subreddit: str, sort: str, timeframe: str, trim: bool):
        return {
            "posts": [
                {
                    "id": "abc",
                    "subreddit": "wallstreetbets",
                    "title": "post",
                    "selftext": "body",
                    "permalink": "/r/wallstreetbets/comments/abc/post/",
                    "preview": {"images": [{"source": {"url": "https://preview.example/abc.jpg"}}]},
                },
                {"id": "def", "title": "second"},
            ]
        }

    async def reddit_post_comments(self, *, url: str, trim: bool):
        self.comment_calls.append(url)
        return {
            "comments": [
                {"id": "c1", "body": "one", "author": "user1"},
                {"id": "c2", "body": "two", "author": "user2"},
            ]
        }


async def test_x_collector_normalizes_limit_and_media() -> None:
    collector = ScrapeCreatorsXUserTweetsCollector(
        collector_id="x.aleabitoreddit",
        collector_config=CollectorConfig(
            kind=X_SOURCE_TYPE,
            handle="aleabitoreddit",
            limit=1,
            trim=True,
        ),
        provider_config=ScrapeCreatorsProviderConfig(),
        client=FakeScrapeCreatorsClient(),
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert len(items) == 1
    assert items[0].source_type == X_SOURCE_TYPE
    assert items[0].source_id == "1"
    assert items[0].metadata["media"][0]["url"] == "https://cdn.example/1.jpg"


async def test_reddit_collector_normalizes_posts_and_optional_comments() -> None:
    fake_client = FakeScrapeCreatorsClient()
    collector = ScrapeCreatorsRedditSubredditCollector(
        collector_id="reddit.wallstreetbets",
        collector_config=CollectorConfig(
            kind=REDDIT_SOURCE_TYPE,
            subreddit="wallstreetbets",
            limit=1,
            trim=True,
            include_comments=True,
            comments_per_post=1,
        ),
        provider_config=ScrapeCreatorsProviderConfig(),
        client=fake_client,
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert len(items) == 2
    assert items[0].metadata["entity_type"] == "reddit_post"
    assert items[0].metadata["media"][0]["url"] == "https://preview.example/abc.jpg"
    assert items[1].metadata["entity_type"] == "reddit_comment"
    assert items[1].source_id == "abc:c1"
    assert fake_client.comment_calls == ["https://www.reddit.com/r/wallstreetbets/comments/abc/post/"]
