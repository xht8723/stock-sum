"""SQLite storage repository tests."""

import aiosqlite
import pytest

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem
from stock_sum.storage.sqlite import SQLiteStorageRepository


async def _count_rows(path, table: str) -> int:
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
        try:
            row = await cursor.fetchone()
            return int(row[0])
        finally:
            await cursor.close()


async def test_initialize_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)

    await repository.initialize()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?, ?)",
            ("collection_runs", "raw_item_index", "raw_x_posts", "raw_reddit_posts"),
        )
        try:
            tables = {row[0] for row in await cursor.fetchall()}
        finally:
            await cursor.close()

    assert tables == {"collection_runs", "raw_item_index", "raw_x_posts", "raw_reddit_posts"}


async def test_save_x_items_upserts_and_indexes(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    item = RawItem(
        source_id="123",
        source_type="x_user_timeline",
        url="https://x.com/user/status/123",
        text="first text",
        metadata={"handle": "user", "author": "User @user", "timestamp": "Jan 1, 2026"},
    )

    first = await repository.save_raw_items([item])
    second = await repository.save_raw_items([item])

    assert first.inserted_count == 1
    assert first.updated_count == 0
    assert second.inserted_count == 0
    assert second.updated_count == 1
    assert await _count_rows(db_path, "raw_x_posts") == 1
    assert await _count_rows(db_path, "raw_item_index") == 1


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

    await repository.start_collection_run(run_id="run-1", collector_id="x.test", source_type="x_user_timeline")
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
