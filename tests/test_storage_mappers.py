"""Source-aware storage mapper tests."""

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem
from stock_sum.storage.mappers import map_raw_item


def test_x_mapper_maps_metadata() -> None:
    row = map_raw_item(
        RawItem(
            source_id="123",
            source_type="x_user_timeline",
            url="https://x.com/user/status/123",
            text="hello",
            metadata={"handle": "user", "author": "User @user", "timestamp": "Jan 1, 2026"},
        )
    )

    assert row.table_name == "raw_x_posts"
    assert row.values["status_id"] == "123"
    assert row.values["handle"] == "user"
    assert row.values["posted_at_text"] == "Jan 1, 2026"


def test_reddit_mapper_accepts_planned_shape() -> None:
    row = map_raw_item(
        RawItem(
            source_id="abc",
            source_type="subreddit_posts",
            url="https://reddit.com/r/stocks/comments/abc",
            text="body",
            metadata={"subreddit": "stocks", "title": "title", "score": 10, "comment_count": 2},
        )
    )

    assert row.table_name == "raw_reddit_posts"
    assert row.values["subreddit"] == "stocks"
    assert row.values["post_id"] == "abc"
    assert row.values["title"] == "title"


def test_unsupported_source_type_raises() -> None:
    item = RawItem(source_id="1", source_type="generic_api", url=None, text="data")

    try:
        map_raw_item(item)
    except UnsupportedSourceTypeError as exc:
        assert "Unsupported raw item source type" in str(exc)
    else:
        raise AssertionError("unsupported source type should raise")
