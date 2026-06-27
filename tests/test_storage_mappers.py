"""Source-aware storage mapper tests."""

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem
from stock_sum.storage.mappers import map_raw_item


def test_x_post_maps_to_source_table_and_media() -> None:
    item = RawItem(
        source_id="123",
        source_type="scrape_creators_x_user_tweets",
        url="https://x.com/example/status/123",
        text="hello",
        metadata={
            "entity_type": "x_post",
            "handle": "example",
            "author_handle": "example",
            "media": [{"media_type": "photo", "url": "https://cdn.example/img.jpg"}],
            "raw": {"id": "123"},
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_scrape_creators_x_posts"
    assert mapped.key == ("example", "123")
    assert mapped.row["status_id"] == "123"
    assert mapped.media_rows[0]["media_url"] == "https://cdn.example/img.jpg"


def test_reddit_post_maps_to_source_table_and_media() -> None:
    item = RawItem(
        source_id="abc",
        source_type="scrape_creators_reddit_subreddit",
        url="https://www.reddit.com/r/wallstreetbets/comments/abc/post/",
        text="body",
        metadata={
            "entity_type": "reddit_post",
            "subreddit": "wallstreetbets",
            "title": "Post title",
            "media": [{"media_type": "image", "url": "https://preview.example/img.jpg"}],
            "raw": {"id": "abc"},
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_scrape_creators_reddit_posts"
    assert mapped.key == ("wallstreetbets", "abc")
    assert mapped.row["title"] == "Post title"
    assert mapped.media_rows[0]["media_url"] == "https://preview.example/img.jpg"


def test_reddit_comment_maps_to_source_table() -> None:
    item = RawItem(
        source_id="abc:def",
        source_type="scrape_creators_reddit_subreddit",
        url="https://www.reddit.com/r/wallstreetbets/comments/abc/post/def/",
        text="comment",
        metadata={
            "entity_type": "reddit_comment",
            "post_id": "abc",
            "comment_id": "def",
            "body": "comment",
            "raw": {"id": "def"},
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_scrape_creators_reddit_comments"
    assert mapped.key == ("abc", "def")
    assert mapped.row["body"] == "comment"


def test_unsupported_source_type_raises() -> None:
    item = RawItem(source_id="1", source_type="generic_api", url=None, text="data")

    try:
        map_raw_item(item)
    except UnsupportedSourceTypeError as exc:
        assert "Unsupported raw item source type" in str(exc)
    else:
        raise AssertionError("unsupported source type should raise")
