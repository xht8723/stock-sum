"""Summary input builder tests."""

from datetime import datetime, timedelta, timezone

from stock_sum.config.models import AppConfig, LLMConfig, RedditSubredditSourceConfig, ReportInputConfig, StorageConfig, XUserSourceConfig, SourcesConfig
from stock_sum.reports.summary_input import SummaryInputBuilder
from stock_sum.storage.models import (
    StoredCollectionRun,
    StoredMediaAsset,
    StoredRedditComment,
    StoredRedditPost,
    StoredXPost,
)


def _iso(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class FakeSummaryRepository:
    def __init__(self) -> None:
        self.x_read_calls = []
        self.reddit_read_calls = []

    async def list_collection_runs(self, *, limit=None):
        return [
            StoredCollectionRun(
                run_id="run-1",
                collector_id="x.alpha",
                source_type="x_user_timeline",
                status="succeeded",
                started_at="2026-06-27T00:00:00+00:00",
                finished_at="2026-06-27T00:00:01+00:00",
                collected_count=1,
                inserted_count=1,
                updated_count=0,
                error_text=None,
            )
        ]

    async def read_x_posts(self, *, handles=None, since_posted_at=None, limit=None):
        assert handles == ["alpha"]
        assert since_posted_at is not None
        self.x_read_calls.append({"handles": handles, "since_posted_at": since_posted_at, "limit": limit})
        return [
            StoredXPost(
                status_id="1",
                handle="alpha",
                author_handle="alpha",
                author_name="Alpha",
                posted_at_text=_iso(1),
                url="https://x.com/alpha/status/1",
                text="x text",
                reply_count=1,
                repost_count=2,
                like_count=3,
                quote_count=4,
                view_count=5,
                raw_metadata={},
                collected_at="2026-06-27T00:00:01+00:00",
                media=[
                    StoredMediaAsset(
                        remote_url="https://cdn.example/x.jpg",
                        media_type="photo",
                        raw_metadata={"source_path": "legacy.entities.media", "raw": {"ignored": True}},
                        local_path="data/media/x/x.jpg",
                    )
                ],
            ),
            StoredXPost(
                status_id="old",
                handle="alpha",
                author_handle="alpha",
                author_name="Alpha",
                posted_at_text=_iso(30),
                url="https://x.com/alpha/status/old",
                text="old x text",
                reply_count=None,
                repost_count=None,
                like_count=None,
                quote_count=None,
                view_count=None,
                raw_metadata={},
                collected_at="2026-06-27T00:00:01+00:00",
                media=[],
            ),
        ]

    async def read_reddit_posts(self, *, subreddits=None, since_posted_at=None, limit=None):
        assert subreddits == ["bets"]
        assert since_posted_at is not None
        self.reddit_read_calls.append({"subreddits": subreddits, "since_posted_at": since_posted_at, "limit": limit})
        return [
            StoredRedditPost(
                post_id="abc",
                subreddit="bets",
                fullname="t3_abc",
                title="reddit title",
                author="author",
                url="https://reddit.example/post",
                permalink="https://www.reddit.com/r/bets/comments/abc/post/",
                selftext="reddit body",
                score=10,
                ups=11,
                upvote_ratio=0.9,
                num_comments=2,
                thumbnail_url=None,
                created_at_text=_iso(1),
                raw_metadata={},
                collected_at="2026-06-27T00:00:01+00:00",
                media=[
                    StoredMediaAsset(
                        remote_url="https://cdn.example/reddit.jpg",
                        media_type="image",
                        raw_metadata={"source_field": "preview.images.source", "width": 640, "height": 480},
                        local_path="data/media/reddit/reddit.jpg",
                    )
                ],
                comments=[
                    StoredRedditComment(
                        comment_id="c1",
                        post_id="abc",
                        parent_id="t3_abc",
                        author="commenter",
                        body="comment body",
                        score=1,
                        ups=1,
                        url="https://reddit.example/comment",
                        created_at_text=_iso(1),
                        depth=0,
                        raw_metadata={},
                        collected_at="2026-06-27T00:00:02+00:00",
                    ),
                    StoredRedditComment(
                        comment_id="c2",
                        post_id="abc",
                        parent_id="t3_abc",
                        author="commenter",
                        body="newer comment body",
                        score=2,
                        ups=2,
                        url="https://reddit.example/comment2",
                        created_at_text=_iso(0),
                        depth=0,
                        raw_metadata={},
                        collected_at="2026-06-27T00:00:03+00:00",
                    )
                ],
            ),
            StoredRedditPost(
                post_id="old",
                subreddit="bets",
                fullname="t3_old",
                title="old reddit title",
                author="author",
                url="https://reddit.example/old",
                permalink="https://www.reddit.com/r/bets/comments/old/post/",
                selftext="old reddit body",
                score=1,
                ups=1,
                upvote_ratio=0.1,
                num_comments=0,
                thumbnail_url=None,
                created_at_text=_iso(30),
                raw_metadata={},
                collected_at="2026-06-27T00:00:01+00:00",
                media=[],
                comments=[],
            ),
        ]


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(sqlite_path=str(tmp_path / "test.sqlite3")),
        llm=LLMConfig(provider="deepseek", model="deepseek-v4-flash", api_key_env="DEEPSEEK_API_KEY"),
        sources=SourcesConfig(
            x_users=[XUserSourceConfig(handle="alpha", limit=100, lookback_hours=24)],
            subreddits=[RedditSubredditSourceConfig(subreddit="bets", limit=100, lookback_hours=24)],
        ),
    )


async def test_summary_input_groups_sources_and_links_reddit_comments(tmp_path) -> None:
    repository = FakeSummaryRepository()
    builder = SummaryInputBuilder(config=_config(tmp_path), repository=repository)

    payload = await builder.build(download_images=False)
    data = payload.to_dict()

    assert data["report_type"] == "social"
    assert data["x"][0]["handle"] == "alpha"
    assert data["x"][0]["posts"][0]["status_id"] == "1"
    assert data["x"][0]["posts"][0]["media"][0]["source_metadata"] == {"source_path": "legacy.entities.media"}
    assert data["reddit"][0]["subreddit"] == "bets"
    assert data["reddit"][0]["posts"][0]["post_id"] == "abc"
    assert data["reddit"][0]["posts"][0]["comments"][0]["post_id"] == "abc"
    assert "raw" not in data["reddit"][0]["posts"][0]["media"][0]["source_metadata"]
    assert repository.x_read_calls[0]["limit"] == 100
    assert repository.reddit_read_calls[0]["limit"] == 100


async def test_summary_input_compact_mode_uses_shared_media_map_and_drops_redundancy(tmp_path) -> None:
    builder = SummaryInputBuilder(config=_config(tmp_path), repository=FakeSummaryRepository())

    payload = await builder.build(download_images=False)
    data = payload.to_dict(mode="compact")

    assert set(data) == {"report_type", "generated_at", "sources", "media", "metadata"}
    assert data["sources"]["x"][0]["posts"][0]["media"] == ["m1"]
    assert data["sources"]["reddit"][0]["posts"][0]["media"] == ["m2"]
    assert data["media"]["m1"] == {
        "source": "x",
        "source_ref": "x1",
        "kind": "photo",
        "remote_url": "https://cdn.example/x.jpg",
        "local_path": "data/media/x/x.jpg",
        "source_hint": "legacy.entities.media",
    }
    comment = data["sources"]["reddit"][0]["posts"][0]["comments"][0]
    assert "post_id" not in comment
    assert comment["parent"] == "t3_abc"
    assert "raw" not in str(data)


async def test_summary_input_vision_mode_adds_ordered_attachments(tmp_path) -> None:
    builder = SummaryInputBuilder(config=_config(tmp_path), repository=FakeSummaryRepository())

    payload = await builder.build(download_images=False)
    data = payload.to_dict(mode="vision", max_images_per_post=1, max_images_total=1)

    assert list(data["media"]) == ["m1"]
    assert data["vision"]["attachments"] == [
        {
            "id": "m1",
            "source": "x",
            "source_ref": "x1",
            "kind": "photo",
            "local_path": "data/media/x/x.jpg",
            "remote_url": "https://cdn.example/x.jpg",
        }
    ]


async def test_summary_input_caps_posts_and_comments_per_source(tmp_path) -> None:
    config = _config(tmp_path).model_copy(
        update={
            "report_input": ReportInputConfig(
                max_x_posts_per_source=1,
                max_reddit_posts_per_source=1,
                max_reddit_comments_per_post=1,
            )
        }
    )
    repository = FakeSummaryRepository()
    builder = SummaryInputBuilder(config=config, repository=repository)

    payload = await builder.build(download_images=False)
    data = payload.to_dict()

    assert repository.x_read_calls[0]["limit"] == 1
    assert repository.reddit_read_calls[0]["limit"] == 1
    assert [post["status_id"] for post in data["x"][0]["posts"]] == ["1"]
    assert [post["post_id"] for post in data["reddit"][0]["posts"]] == ["abc"]
    assert [comment["comment_id"] for comment in data["reddit"][0]["posts"][0]["comments"]] == ["c2"]

