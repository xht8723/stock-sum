"""SQLite storage repository tests."""

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import ProviderApiResponse, RawItem
from stock_sum.media.downloader import remote_url_hash
from stock_sum.storage.models import StoredDownloadedMedia
from stock_sum.storage.sqlite import SQLiteStorageRepository


def _iso(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


async def test_initialize_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)

    await repository.initialize()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "raw_provider_api_responses",
                "llm_analysis_runs",
                "llm_x_post_analyses",
                "llm_reddit_post_analyses",
                "llm_reddit_comment_analyses",
                "raw_house_ptr_filings",
                "raw_house_ptr_trade_rows",
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
        "raw_provider_api_responses",
        "llm_analysis_runs",
        "llm_x_post_analyses",
        "llm_reddit_post_analyses",
        "llm_reddit_comment_analyses",
        "raw_house_ptr_filings",
        "raw_house_ptr_trade_rows",
    }

    async with aiosqlite.connect(db_path) as db:
        x_columns = await _columns(db, "raw_x_posts")
        reddit_columns = await _columns(db, "raw_reddit_posts")
        comment_columns = await _columns(db, "raw_reddit_comments")
    assert "posted_at_utc" in x_columns
    assert "created_at_utc" in reddit_columns
    assert "created_at_utc" in comment_columns


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


async def test_read_x_posts_filters_by_posted_cutoff(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    old = RawItem(
        source_id="111",
        source_type="x_user_timeline",
        url="https://x.com/example/status/111",
        text="old",
        metadata={"entity_type": "x_post", "handle": "example", "posted_at_text": _iso(30)},
    )
    recent = RawItem(
        source_id="222",
        source_type="x_user_timeline",
        url="https://x.com/example/status/222",
        text="recent",
        metadata={"entity_type": "x_post", "handle": "example", "posted_at_text": _iso(1)},
    )

    await repository.save_raw_items([old, recent])

    posts = await repository.read_x_posts(
        handles=["example"],
        since_posted_at=datetime.now(timezone.utc) - timedelta(hours=24),
    )

    assert [post.status_id for post in posts] == ["222"]
    async with aiosqlite.connect(db_path) as db:
        assert await _count_rows(db, "raw_x_posts") == 2


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


async def test_read_reddit_posts_filters_by_posted_cutoff(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    old = RawItem(
        source_id="old",
        source_type="reddit_subreddit",
        url="https://www.reddit.com/r/wallstreetbets/comments/old/post/",
        text="old",
        metadata={
            "entity_type": "reddit_post",
            "subreddit": "wallstreetbets",
            "title": "Old post",
            "created_at_text": _iso(30),
        },
    )
    recent = RawItem(
        source_id="recent",
        source_type="reddit_subreddit",
        url="https://www.reddit.com/r/wallstreetbets/comments/recent/post/",
        text="recent",
        metadata={
            "entity_type": "reddit_post",
            "subreddit": "wallstreetbets",
            "title": "Recent post",
            "created_at_text": _iso(1),
        },
    )

    await repository.save_raw_items([old, recent])

    posts = await repository.read_reddit_posts(
        subreddits=["wallstreetbets"],
        since_posted_at=datetime.now(timezone.utc) - timedelta(hours=24),
    )

    assert [post.post_id for post in posts] == ["recent"]
    async with aiosqlite.connect(db_path) as db:
        assert await _count_rows(db, "raw_reddit_posts") == 2


async def test_save_provider_api_responses_archives_raw_and_parsed_payloads(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    response = ProviderApiResponse(
        provider="xpoz",
        tool_name="getTwitterPostsByAuthor",
        request_arguments={"username": "aleabitoreddit", "limit": 100},
        raw_response_text="status: success\ndata:\n  results[0]{id,text}:",
        parsed_rows=[{"id": "123", "text": "hello"}],
        row_count=1,
    )

    await repository.save_provider_api_responses(
        collection_run_id="run-1",
        collector_id="x.aleabitoreddit",
        responses=[response],
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT provider, tool_name, request_arguments_json, raw_response_text,
                   parsed_rows_json, row_count
            FROM raw_provider_api_responses
            """
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()

    assert row[0] == "xpoz"
    assert row[1] == "getTwitterPostsByAuthor"
    assert "aleabitoreddit" in row[2]
    assert "Authorization" not in row[2]
    assert row[3].startswith("status: success")
    assert '"text": "hello"' in row[4]
    assert row[5] == 1


async def test_save_and_read_llm_analysis_report(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    await repository.start_llm_analysis_run(
        analysis_run_id="analysis-1",
        profile="default",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="llm-analysis-v1",
    )
    await repository.save_llm_x_post_analyses(
        [
            {
                "analysis_run_id": "analysis-1",
                "profile": "default",
                "handle": "aleabitoreddit",
                "status_id": "123",
                "source_ref": "x1",
                "url": "https://x.com/aleabitoreddit/status/123",
                "posted_at_text": "2026-06-30T00:00:00+00:00",
                "sentiment": "bullish",
                "tags_json": '["ai","growth","cloud","risk","watch"]',
                "summary": "X post summary.",
                "interpretation": "Market relevance.",
                "confidence": "medium",
                "raw_response_json": "{}",
                "analyzed_at": "2026-06-30T00:00:00+00:00",
            }
        ]
    )
    await repository.save_llm_reddit_post_analyses(
        [
            {
                "analysis_run_id": "analysis-1",
                "profile": "default",
                "subreddit": "wallstreetbets",
                "post_id": "abc",
                "source_ref": "r1",
                "title": "Reddit post",
                "url": "https://reddit.com/r/wallstreetbets/comments/abc/",
                "created_at_text": "2026-06-30T00:00:00+00:00",
                "sentiment": "mixed",
                "tags_json": '["semis","memory","earnings","risk","watch"]',
                "summary": "Reddit post summary.",
                "interpretation": "Comment thread is divided.",
                "confidence": "high",
                "comment_sentiment_counts_json": '{"bullish":1,"bearish":0,"mixed":1,"neutral":0,"unclear":0}',
                "raw_response_json": "{}",
                "analyzed_at": "2026-06-30T00:00:00+00:00",
            }
        ]
    )
    await repository.save_llm_reddit_comment_analyses(
        [
            {
                "analysis_run_id": "analysis-1",
                "profile": "default",
                "subreddit": "wallstreetbets",
                "post_id": "abc",
                "comment_id": "c1",
                "source_ref": "r1.c1",
                "parent_id": "abc",
                "sentiment": "bullish",
                "summary": "Comment likes the setup.",
                "confidence": "medium",
                "raw_response_json": "{}",
                "analyzed_at": "2026-06-30T00:00:00+00:00",
            }
        ]
    )
    await repository.finish_llm_analysis_run(
        analysis_run_id="analysis-1",
        status="succeeded",
        chunk_count=2,
        succeeded_count=2,
    )

    report = await repository.read_llm_analysis_report(profile="default", analysis_run_id="analysis-1")

    assert report["x_reports"][0]["posts"][0]["tags"] == ["ai", "growth", "cloud", "risk", "watch"]
    reddit_post = report["reddit_report"]["posts"][0]
    assert reddit_post["comment_sentiment_counts"]["bullish"] == 1
    assert reddit_post["comments_sentiment"] == "bullish: 1, bearish: 0, mixed: 1, neutral: 0, unclear: 0"


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


async def test_save_and_read_house_ptr_disclosures(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    item = RawItem(
        source_id="20024228",
        source_type="house_ptr_disclosures",
        url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
        text="Jane Doe House PTR disclosure",
        metadata={
            "entity_type": "house_ptr_filing",
            "doc_id": "20024228",
            "year": 2026,
            "name": "Jane Doe",
            "status": "Member",
            "state": "CA",
            "filing_date": "2026-06-30",
            "pdf_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
            "raw_xml": {"DocID": "20024228"},
            "tables": [[["Asset", "Type", "Date", "Amount"], ["Apple Inc. - Common Stock (AAPL) [ST]", "Purchase", "2026-06-20", "$1,001 - $15,000"]]],
            "trade_rows": [
                {
                    "table_index": 0,
                    "row_index": 0,
                    "cells": ["Apple Inc. - Common Stock (AAPL) [ST]", "Purchase", "2026-06-20", "$1,001 - $15,000"],
                    "fields": {
                        "asset": "Apple Inc. - Common Stock (AAPL) [ST]",
                        "transaction_type": "Purchase",
                        "transaction_date": "2026-06-20",
                        "amount": "$1,001 - $15,000",
                    },
                }
            ],
            "extraction_status": "succeeded",
            "extraction_error": None,
        },
    )

    first = await repository.save_raw_items([item])
    second = await repository.save_raw_items([item])

    assert first.inserted_count == 1
    assert second.updated_count == 1
    assert await repository.existing_house_ptr_doc_ids(year=2026) == {"20024228"}
    trades = await repository.read_house_ptr_trades(limit=20)
    assert len(trades) == 1
    assert trades[0].name == "Jane Doe"
    assert trades[0].asset == "Apple Inc. - Common Stock (AAPL) [ST]"
    assert trades[0].asset_type_code == "ST"
    assert trades[0].asset_type_label == "Stocks, including ADRs"
    assert trades[0].stock_ticker == "AAPL"
    assert trades[0].transaction_type == "Purchase"
    assert trades[0].raw_cells[0] == "Apple Inc. - Common Stock (AAPL) [ST]"
    assert [trade.doc_id for trade in await repository.read_house_ptr_trades(asset_type="st")] == ["20024228"]
    assert [trade.doc_id for trade in await repository.read_house_ptr_trades(ticker="aapl")] == ["20024228"]
    assert await repository.read_house_ptr_trades(asset_type="GS") == []


async def test_failed_house_ptr_extractions_are_not_skipped(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    failed_item = RawItem(
        source_id="20024229",
        source_type="house_ptr_disclosures",
        url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024229.pdf",
        text="Jane Doe failed House PTR disclosure",
        metadata={
            "entity_type": "house_ptr_filing",
            "doc_id": "20024229",
            "year": 2026,
            "name": "Jane Doe",
            "status": "Member",
            "state": "CA",
            "filing_date": "2026-06-30",
            "pdf_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024229.pdf",
            "raw_xml": {"DocID": "20024229"},
            "tables": [],
            "trade_rows": [],
            "extraction_status": "failed",
            "extraction_error": "temporary PDF parse failure",
        },
    )

    await repository.save_raw_items([failed_item])

    assert await repository.existing_house_ptr_doc_ids(year=2026) == set()


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


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    try:
        return {row[1] for row in await cursor.fetchall()}
    finally:
        await cursor.close()
