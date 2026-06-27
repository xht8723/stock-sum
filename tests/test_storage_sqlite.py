"""SQLite storage repository tests."""

import aiosqlite
import pytest

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem
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
              AND name IN (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "collection_runs",
                "raw_item_index",
                "raw_scrape_creators_x_posts",
                "raw_scrape_creators_x_post_media",
                "raw_scrape_creators_reddit_posts",
                "raw_scrape_creators_reddit_comments",
                "raw_scrape_creators_reddit_post_media",
            ),
        )
        try:
            tables = {row[0] for row in await cursor.fetchall()}
        finally:
            await cursor.close()

    assert tables == {
        "collection_runs",
        "raw_item_index",
        "raw_scrape_creators_x_posts",
        "raw_scrape_creators_x_post_media",
        "raw_scrape_creators_reddit_posts",
        "raw_scrape_creators_reddit_comments",
        "raw_scrape_creators_reddit_post_media",
    }


async def test_save_x_items_upserts_posts_media_and_index(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
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

    first = await repository.save_raw_items([item])
    second = await repository.save_raw_items([item])

    assert first.inserted_count == 1
    assert first.updated_count == 0
    assert second.inserted_count == 0
    assert second.updated_count == 1

    async with aiosqlite.connect(db_path) as db:
        post_count = await _count_rows(db, "raw_scrape_creators_x_posts")
        media_count = await _count_rows(db, "raw_scrape_creators_x_post_media")
        index_count = await _count_rows(db, "raw_item_index")

    assert post_count == 1
    assert media_count == 1
    assert index_count == 1


async def test_save_reddit_items_upserts_posts_comments_media_and_index(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    post = RawItem(
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
    comment = RawItem(
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

    result = await repository.save_raw_items([post, comment])

    assert result.collected_count == 2
    assert result.inserted_count == 2

    async with aiosqlite.connect(db_path) as db:
        post_count = await _count_rows(db, "raw_scrape_creators_reddit_posts")
        comment_count = await _count_rows(db, "raw_scrape_creators_reddit_comments")
        media_count = await _count_rows(db, "raw_scrape_creators_reddit_post_media")
        index_count = await _count_rows(db, "raw_item_index")

    assert post_count == 1
    assert comment_count == 1
    assert media_count == 1
    assert index_count == 2


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
