"""Xpoz collector normalization tests."""

from __future__ import annotations

from stock_sum.collectors.api.xpoz import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE, XpozRedditSubredditCollector, XpozXUserTimelineCollector
from stock_sum.config.models import CollectorConfig, XpozProviderConfig
from stock_sum.core.context import RuntimeContext


class FakeXpozClient:
    def __init__(self) -> None:
        self.detail_ids: list[str] = []
        self.comment_calls: list[str] = []

    async def twitter_posts_by_author(self, *, username: str, limit: int, fields: list[str] | None = None, force_latest: bool = True):
        return [
            {"id": "2070796064698044849", "text": "older", "authorUsername": username, "createdAt": "2026-06-27T09:07:10.000Z"},
            {"id": "2071074680253911267", "text": "newest", "authorUsername": username, "createdAt": "2026-06-28T03:34:17.000Z"},
        ]

    async def twitter_posts_by_ids(self, *, post_ids: list[str], fields: list[str] | None = None, force_latest: bool = True):
        self.detail_ids = post_ids
        return [
            {
                "id": "2071074680253911267",
                "text": "newest enriched",
                "authorUsername": "aleabitoreddit",
                "createdAt": "2026-06-28T03:34:17.000Z",
                "mediaUrls": ["https://pbs.twimg.com/media/new.jpg"],
                "likeCount": "10",
                "impressionCount": "100",
            },
            {
                "id": "2070796064698044849",
                "text": "older enriched",
                "authorUsername": "aleabitoreddit",
                "createdAt": "2026-06-27T09:07:10.000Z",
            },
        ]

    async def reddit_subreddit_posts(self, *, subreddit: str, limit: int, fields: list[str] | None = None, force_latest: bool = True):
        return [
            {"id": "old", "title": "older", "createdAt": "100", "subredditName": subreddit, "postUrl": "https://reddit.com/r/test/comments/old/", "commentsCount": "1"},
            {
                "id": "new",
                "title": "newer",
                "createdAt": "200",
                "subredditName": subreddit,
                "postUrl": "https://reddit.com/r/test/comments/new/",
                "url": "https://i.redd.it/new.jpg",
                "thumbnail": "https://preview.redd.it/new.jpg",
                "commentsCount": "2",
            },
        ]

    async def reddit_post_with_comments(
        self,
        *,
        post_id: str,
        limit: int,
        post_fields: list[str] | None = None,
        comment_fields: list[str] | None = None,
        force_latest: bool = True,
    ):
        self.comment_calls.append(post_id)
        return {
            "posts": [],
            "comments": [
                {"id": "c1", "body": "comment", "authorUsername": "user", "parentId": post_id, "score": "3", "createdAt": "201"}
            ],
        }


async def test_x_collector_sorts_and_enriches_media() -> None:
    fake = FakeXpozClient()
    collector = XpozXUserTimelineCollector(
        collector_id="x.aleabitoreddit",
        collector_config=CollectorConfig(kind=X_SOURCE_TYPE, handle="aleabitoreddit", limit=2),
        provider_config=XpozProviderConfig(),
        client=fake,
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert [item.source_id for item in items] == ["2071074680253911267", "2070796064698044849"]
    assert fake.detail_ids == ["2071074680253911267", "2070796064698044849"]
    assert items[0].text == "newest enriched"
    assert items[0].metadata["media"][0]["url"] == "https://pbs.twimg.com/media/new.jpg"
    assert items[0].metadata["like_count"] == 10
    assert items[0].metadata["view_count"] == 100


async def test_reddit_collector_sorts_media_and_optional_comments() -> None:
    fake = FakeXpozClient()
    collector = XpozRedditSubredditCollector(
        collector_id="reddit.test",
        collector_config=CollectorConfig(
            kind=REDDIT_SOURCE_TYPE,
            subreddit="test",
            limit=2,
            include_comments=True,
            comments_per_post=1,
        ),
        provider_config=XpozProviderConfig(),
        client=fake,
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert [item.source_id for item in items if item.metadata["entity_type"] == "reddit_post"] == ["new", "old"]
    assert items[0].metadata["media"][0]["url"] == "https://i.redd.it/new.jpg"
    assert items[1].metadata["entity_type"] == "reddit_comment"
    assert items[1].source_id == "new:c1"
    assert fake.comment_calls == ["new", "old"]
