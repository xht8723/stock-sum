"""SQLite storage repository tests."""

import aiosqlite
import pytest

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem
from stock_sum.media.downloader import remote_url_hash
from stock_sum.storage.models import StoredDownloadedMedia
from stock_sum.storage.sqlite import SQLiteStorageRepository


async def test_initialize_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)

    await repository.initialize()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name IN (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "collection_runs",
                "raw_item_index",
                "raw_x_posts",
                "raw_x_post_media",
                "raw_reddit_posts",
                "raw_reddit_comments",
                "raw_reddit_post_media",
                "downloaded_media",
            ),
        )
        try:
            tables = {row[0] for row in await cursor.fetchall()}
        finally:
            await cursor.close()

    assert tables == {
        "collection_runs",
        "raw_item_index",
        "raw_x_posts",
        "raw_x_post_media",
        "raw_reddit_posts",
        "raw_reddit_comments",
        "raw_reddit_post_media",
        "downloaded_media",
    }


async def test_save_x_items_upserts_posts_media_and_index(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    item = RawItem(
        source_id="123",
        source_type="x_user_timeline",
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

    first = await repository.save_raw_items([item])
    second = await repository.save_raw_items([item])

    assert first.inserted_count == 1
    assert first.updated_count == 0
    assert second.inserted_count == 0
    assert second.updated_count == 1

    async with aiosqlite.connect(db_path) as db:
        post_count = await _count_rows(db, "raw_x_posts")
        media_count = await _count_rows(db, "raw_x_post_media")
        index_count = await _count_rows(db, "raw_item_index")

    assert post_count == 1
    assert media_count == 1
    assert index_count == 1

    posts = await repository.read_x_posts(handles=["example"])
    assert len(posts) == 1
    assert posts[0].status_id == "123"
    assert posts[0].media[0].remote_url == "https://cdn.example/img.jpg"


async def test_read_x_posts_orders_by_recent_status_id_before_collection_time(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    older = RawItem(
        source_id="1988048592754589970",
        source_type="x_user_timeline",
        url="https://x.com/example/status/1988048592754589970",
        text="older",
        metadata={
            "entity_type": "x_post",
            "handle": "example",
            "posted_at_text": "Tue Nov 11 00:58:15 +0000 2025",
            "raw": {"rest_id": "1988048592754589970"},
        },
    )
    newer = RawItem(
        source_id="1989352983348589023",
        source_type="x_user_timeline",
        url="https://x.com/example/status/1989352983348589023",
        text="newer",
        metadata={
            "entity_type": "x_post",
            "handle": "example",
            "posted_at_text": "Fri Nov 14 15:21:26 +0000 2025",
            "raw": {"rest_id": "1989352983348589023"},
        },
    )

    await repository.save_raw_items([older, newer])

    posts = await repository.read_x_posts(handles=["example"])

    assert [post.status_id for post in posts] == ["1989352983348589023", "1988048592754589970"]


async def test_save_reddit_items_upserts_posts_comments_media_and_index(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    post = RawItem(
        source_id="abc",
        source_type="reddit_subreddit",
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
    comment = RawItem(
        source_id="abc:def",
        source_type="reddit_subreddit",
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

    result = await repository.save_raw_items([post, comment])

    assert result.collected_count == 2
    assert result.inserted_count == 2

    async with aiosqlite.connect(db_path) as db:
        post_count = await _count_rows(db, "raw_reddit_posts")
        comment_count = await _count_rows(db, "raw_reddit_comments")
        media_count = await _count_rows(db, "raw_reddit_post_media")
        index_count = await _count_rows(db, "raw_item_index")

    assert post_count == 1
    assert comment_count == 1
    assert media_count == 1
    assert index_count == 2

    posts = await repository.read_reddit_posts(subreddits=["wallstreetbets"])
    assert len(posts) == 1
    assert posts[0].post_id == "abc"
    assert posts[0].comments[0].comment_id == "def"
    assert posts[0].media[0].remote_url == "https://preview.example/img.jpg"


async def test_downloaded_media_upsert_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    remote_url = "https://cdn.example/img.jpg"
    media = StoredDownloadedMedia(
        remote_url_hash=remote_url_hash(remote_url),
        remote_url=remote_url,
        local_path="data/media/x/file.jpg",
        content_type="image/jpeg",
        byte_size=10,
        sha256="abc",
        downloaded_at="2026-06-27T00:00:00+00:00",
    )

    await repository.save_downloaded_media(media)
    await repository.save_downloaded_media(media)

    stored = await repository.get_downloaded_media(remote_url)
    assert stored == media

    async with aiosqlite.connect(db_path) as db:
        count = await _count_rows(db, "downloaded_media")

    assert count == 1


async def test_save_unsupported_source_type_does_not_create_generic_storage(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    item = RawItem(source_id="1", source_type="generic_api", url=None, text="data")

    with pytest.raises(UnsupportedSourceTypeError):
        await repository.save_raw_items([item])

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'raw_api_items'")
        try:
            assert await cursor.fetchone() is None
        finally:
            await cursor.close()


async def test_collection_run_lifecycle(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)

    await repository.start_collection_run(run_id="run-1", collector_id="api.test", source_type="test_source")
    await repository.finish_collection_run(
        run_id="run-1",
        status="succeeded",
        collected_count=2,
        inserted_count=1,
        updated_count=1,
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT status, collected_count, inserted_count, updated_count FROM collection_runs WHERE run_id = ?",
            ("run-1",),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()

    assert row == ("succeeded", 2, 1, 1)


async def _count_rows(db: aiosqlite.Connection, table: str) -> int:
    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row[0]
