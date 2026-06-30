"""Xpoz collector normalization tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from stock_sum.collectors.api.xpoz import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE, XpozRedditSubredditCollector, XpozXUserTimelineCollector
from stock_sum.config.models import CollectorConfig, XpozProviderConfig
from stock_sum.core.context import RuntimeContext


def _iso(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def _snowflake_id(hours_ago: int) -> str:
    timestamp = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    milliseconds = int(timestamp.timestamp() * 1000)
    return str((milliseconds - 1288834974657) << 22)


class FakeXpozClient:
    def __init__(self) -> None:
        self.detail_ids: list[str] = []
        self.comment_calls: list[str] = []

    async def twitter_posts_by_author(self, *, username: str, limit: int, fields: list[str] | None = None, force_latest: bool = True):
        return [
            {"id": "2070796064698044849", "text": "older", "authorUsername": username, "createdAt": _iso(30)},
            {"id": "2071074680253911267", "text": "newest", "authorUsername": username, "createdAt": _iso(1)},
        ]

    async def twitter_posts_by_ids(self, *, post_ids: list[str], fields: list[str] | None = None, force_latest: bool = True):
        self.detail_ids = post_ids
        return [
            {
                "id": "2071074680253911267",
                "text": "newest enriched",
                "authorUsername": "aleabitoreddit",
                "createdAt": _iso(1),
                "mediaUrls": ["https://pbs.twimg.com/media/new.jpg"],
                "likeCount": "10",
                "impressionCount": "100",
            },
            {
                "id": "2070796064698044849",
                "text": "older enriched",
                "authorUsername": "aleabitoreddit",
                "createdAt": _iso(30),
            },
        ]

    async def reddit_subreddit_posts(self, *, subreddit: str, limit: int, fields: list[str] | None = None, force_latest: bool = True):
        return [
            {"id": "old", "title": "older", "createdAt": _iso(30), "subredditName": subreddit, "postUrl": "https://reddit.com/r/test/comments/old/", "commentsCount": "1"},
            {
                "id": "new",
                "title": "newer",
                "createdAt": _iso(1),
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
                {"id": "c1", "body": "comment", "authorUsername": "user", "parentId": post_id, "score": "3", "createdAt": _iso(1)}
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


async def test_x_collector_uses_snowflake_timestamp_when_timestamp_is_missing() -> None:
    class SnowflakeClient(FakeXpozClient):
        async def twitter_posts_by_author(self, *, username: str, limit: int, fields: list[str] | None = None, force_latest: bool = True):
            return [
                {"id": _snowflake_id(1), "text": "recent by id", "authorUsername": username},
                {"id": _snowflake_id(30), "text": "old by id", "authorUsername": username},
            ]

        async def twitter_posts_by_ids(self, *, post_ids: list[str], fields: list[str] | None = None, force_latest: bool = True):
            self.detail_ids = post_ids
            return [{"id": post_id, "text": f"detail {post_id}", "authorUsername": "aleabitoreddit"} for post_id in post_ids]

    fake = SnowflakeClient()
    collector = XpozXUserTimelineCollector(
        collector_id="x.aleabitoreddit",
        collector_config=CollectorConfig(kind=X_SOURCE_TYPE, handle="aleabitoreddit", limit=2),
        provider_config=XpozProviderConfig(),
        client=fake,
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert len(items) == 2
    assert items[0].source_id == fake.detail_ids[0]


async def test_collector_warns_when_fetch_cap_may_truncate_lookback_window() -> None:
    fake = FakeXpozClient()
    collector = XpozRedditSubredditCollector(
        collector_id="reddit.test",
        collector_config=CollectorConfig(kind=REDDIT_SOURCE_TYPE, subreddit="test", limit=1),
        provider_config=XpozProviderConfig(),
        client=fake,
    )

    await collector.collect(RuntimeContext(config=None))

    assert collector.warnings
    assert "fetch cap" in collector.warnings[0].message


async def test_reddit_comment_fetches_are_bounded_concurrent() -> None:
    class SlowCommentClient(FakeXpozClient):
        def __init__(self) -> None:
            super().__init__()
            self.active_comments = 0
            self.max_active_comments = 0

        async def reddit_subreddit_posts(self, *, subreddit: str, limit: int, fields: list[str] | None = None, force_latest: bool = True):
            return [
                {
                    "id": f"post-{index}",
                    "title": f"post {index}",
                    "createdAt": _iso(1),
                    "subredditName": subreddit,
                    "postUrl": f"https://reddit.com/r/test/comments/post-{index}/",
                    "commentsCount": "1",
                }
                for index in range(3)
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
            self.active_comments += 1
            self.max_active_comments = max(self.max_active_comments, self.active_comments)
            try:
                await asyncio.sleep(0.01)
                self.comment_calls.append(post_id)
                return {
                    "posts": [],
                    "comments": [
                        {
                            "id": f"comment-{post_id}",
                            "body": "comment",
                            "authorUsername": "user",
                            "parentId": post_id,
                            "score": "3",
                            "createdAt": _iso(1),
                        }
                    ],
                }
            finally:
                self.active_comments -= 1

    fake = SlowCommentClient()
    collector = XpozRedditSubredditCollector(
        collector_id="reddit.test",
        collector_config=CollectorConfig(
            kind=REDDIT_SOURCE_TYPE,
            subreddit="test",
            limit=3,
            include_comments=True,
            comments_per_post=1,
        ),
        provider_config=XpozProviderConfig(max_concurrent_requests=2),
        client=fake,
    )

    items = await collector.collect(RuntimeContext(config=None))

    assert fake.max_active_comments == 2
    assert fake.comment_calls == ["post-2", "post-1", "post-0"]
    assert [item.source_id for item in items] == [
        "post-2",
        "post-2:comment-post-2",
        "post-1",
        "post-1:comment-post-1",
        "post-0",
        "post-0:comment-post-0",
    ]
