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
              AND name IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "llm_x_post_tickers",
                "llm_reddit_post_tickers",
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
        "llm_x_post_tickers",
        "llm_reddit_post_tickers",
        "llm_reddit_comment_analyses",
        "raw_house_ptr_filings",
        "raw_house_ptr_trade_rows",
    }

    async with aiosqlite.connect(db_path) as db:
        x_columns = await _columns(db, "raw_x_posts")
        reddit_columns = await _columns(db, "raw_reddit_posts")
        comment_columns = await _columns(db, "raw_reddit_comments")
        llm_x_columns = await _columns(db, "llm_x_post_analyses")
        llm_reddit_columns = await _columns(db, "llm_reddit_post_analyses")
    assert "posted_at_utc" in x_columns
    assert "created_at_utc" in reddit_columns
    assert "created_at_utc" in comment_columns
    assert "importance" in llm_x_columns
    assert "importance" in llm_reddit_columns
    assert "tickers_json" in llm_x_columns
    assert "tickers_json" in llm_reddit_columns


async def test_initialize_adds_llm_importance_to_existing_database(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE llm_x_post_analyses (
                analysis_run_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                handle TEXT NOT NULL,
                status_id TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                url TEXT,
                posted_at_text TEXT,
                sentiment TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                summary TEXT NOT NULL,
                interpretation TEXT NOT NULL,
                confidence TEXT NOT NULL,
                raw_response_json TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                PRIMARY KEY (analysis_run_id, status_id)
            );
            CREATE TABLE llm_reddit_post_analyses (
                analysis_run_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                subreddit TEXT NOT NULL,
                post_id TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                created_at_text TEXT,
                sentiment TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                summary TEXT NOT NULL,
                interpretation TEXT NOT NULL,
                confidence TEXT NOT NULL,
                comment_sentiment_counts_json TEXT NOT NULL,
                raw_response_json TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                PRIMARY KEY (analysis_run_id, post_id)
            );
            INSERT INTO llm_x_post_analyses VALUES (
                'analysis-1', 'default', 'example', '123', 'x1', NULL, NULL,
                'bullish', '[]', 'summary', 'interpretation', 'high', '{}', '2026-06-30T00:00:00+00:00'
            );
            INSERT INTO llm_reddit_post_analyses VALUES (
                'analysis-1', 'default', 'wallstreetbets', 'abc', 'r1', 'title', NULL, NULL,
                'mixed', '[]', 'summary', 'interpretation', 'high',
                '{}', '{}', '2026-06-30T00:00:00+00:00'
            );
            """
        )
        await db.commit()

    await SQLiteStorageRepository(db_path).initialize()

    async with aiosqlite.connect(db_path) as db:
        x_columns = await _columns(db, "llm_x_post_analyses")
        reddit_columns = await _columns(db, "llm_reddit_post_analyses")
        assert "importance" in x_columns
        assert "importance" in reddit_columns
        assert "tickers_json" in x_columns
        assert "tickers_json" in reddit_columns
        cursor = await db.execute("SELECT importance FROM llm_x_post_analyses")
        x_importance = (await cursor.fetchone())[0]
        await cursor.close()
        cursor = await db.execute("SELECT importance FROM llm_reddit_post_analyses")
        reddit_importance = (await cursor.fetchone())[0]
        await cursor.close()

    assert x_importance == "medium"
    assert reddit_importance == "medium"


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
                "tickers_json": '["$NBIS","HOOD","bad value"]',
                "summary": "X post summary.",
                "interpretation": "Market relevance.",
                "importance": "high",
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
                "tickers_json": '["NVDA","BRK.B"]',
                "summary": "Reddit post summary.",
                "interpretation": "Comment thread is divided.",
                "importance": "low",
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
    assert report["x_reports"][0]["posts"][0]["importance"] == "high"
    assert report["x_reports"][0]["posts"][0]["tickers"] == ["NBIS", "HOOD"]
    reddit_post = report["reddit_report"]["posts"][0]
    assert reddit_post["importance"] == "low"
    assert reddit_post["tickers"] == ["NVDA", "BRK.B"]
    assert reddit_post["comment_sentiment_counts"]["bullish"] == 1
    assert reddit_post["comments_sentiment"] == "bullish: 1, bearish: 0, mixed: 1, neutral: 0, unclear: 0"

    matches = await repository.read_llm_social_posts_by_ticker(
        profile="default",
        ticker="nbis",
        analysis_run_id="analysis-1",
    )
    assert [(match["source"], match["ticker"], match["source_id"]) for match in matches] == [("x", "NBIS", "123")]
    reddit_matches = await repository.read_llm_social_posts_by_ticker(
        profile="default",
        ticker="brk.b",
        analysis_run_id="analysis-1",
    )
    assert [(match["source"], match["ticker"], match["source_id"]) for match in reddit_matches] == [
        ("reddit", "BRK.B", "abc")
    ]

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
                "sentiment": "bearish",
                "tags_json": '["ai","growth","cloud","risk","watch"]',
                "tickers_json": '["MSTR"]',
                "summary": "Updated X post summary.",
                "interpretation": "Updated market relevance.",
                "importance": "medium",
                "confidence": "low",
                "raw_response_json": "{}",
                "analyzed_at": "2026-06-30T00:01:00+00:00",
            }
        ]
    )
    assert await repository.read_llm_social_posts_by_ticker(
        profile="default",
        ticker="NBIS",
        analysis_run_id="analysis-1",
    ) == []
    replacement_matches = await repository.read_llm_social_posts_by_ticker(
        profile="default",
        ticker="MSTR",
        analysis_run_id="analysis-1",
    )
    assert [(match["source"], match["ticker"], match["source_id"]) for match in replacement_matches] == [
        ("x", "MSTR", "123")
    ]

    social_points = await repository.read_social_statistic_points(
        profile="default",
        ticker="MSTR",
        source="x",
        sentiment="bearish",
        analysis_run_id="analysis-1",
    )
    assert [(point.source, point.ticker, point.sentiment, point.posted_at) for point in social_points] == [
        ("x", "MSTR", "bearish", "2026-06-30T00:00:00+00:00")
    ]
    tag_matches = await repository.search_social_statistic_tags(profile="default", query="GROW", limit=5)
    assert [(match.label, match.row_count, match.x_count, match.reddit_count, match.statistic_filters) for match in tag_matches] == [
        ("growth", 1, 1, 0, {"fuzzy_tag": "growth"})
    ]
    tagged_points = await repository.read_social_statistic_points(
        profile="default",
        fuzzy_tag="GROWTH",
        source="all",
        analysis_run_id="analysis-1",
    )
    assert [(point.source, point.source_id, point.sentiment) for point in tagged_points] == [("x", "123", "bearish")]


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
    trading_points = await repository.read_trading_statistic_points(ticker="aapl", action="purchase")
    assert [(point.doc_id, point.stock_ticker, point.transaction_action, point.amount) for point in trading_points] == [
        ("20024228", "AAPL", "purchase", "$1,001 - $15,000")
    ]
    asset_points = await repository.read_trading_statistic_points(asset_name="apple inc")
    assert [(point.doc_id, point.asset) for point in asset_points] == [
        ("20024228", "Apple Inc. - Common Stock (AAPL) [ST]")
    ]
    asset_matches = await repository.search_trading_statistic_assets(query="apple", limit=5)
    assert [
        (match.label, match.row_count, match.ticker, match.asset_type_code, match.statistic_filters)
        for match in asset_matches
    ] == [
        (
            "Apple Inc. - Common Stock (AAPL) [ST]",
            1,
            "AAPL",
            "ST",
            {
                "asset_name": "Apple Inc. - Common Stock (AAPL) [ST]",
                "asset_type": "ST",
                "ticker": "AAPL",
            },
        )
    ]


async def test_save_and_read_sec_13f_holdings(tmp_path) -> None:
    db_path = tmp_path / "storage.sqlite3"
    repository = SQLiteStorageRepository(db_path)
    item = RawItem(
        source_id="2026-march-april-may-13f-test",
        source_type="sec_13f_dataset",
        url="https://www.sec.gov/files/test.zip",
        text="2026 March April May 13F",
        metadata={
            "entity_type": "sec_13f_dataset",
            "dataset_id": "2026-march-april-may-13f-test",
            "label": "2026 March April May 13F",
            "download_url": "https://www.sec.gov/files/test.zip",
            "sha256": "abc",
            "byte_size": 100,
            "row_counts": {"submissions": 1, "coverpages": 1, "info_tables": 1},
            "rows_by_table": {
                "submissions": [
                    {
                        "ACCESSION_NUMBER": "0001234567-26-000001",
                        "FILING_DATE": "31-MAY-2026",
                        "SUBMISSIONTYPE": "13F-HR",
                        "CIK": "0001067983",
                        "PERIODOFREPORT": "31-MAR-2026",
                    }
                ],
                "coverpages": [
                    {
                        "ACCESSION_NUMBER": "0001234567-26-000001",
                        "FILINGMANAGER_NAME": "Berkshire Hathaway Inc",
                        "REPORTTYPE": "13F HOLDINGS REPORT",
                    }
                ],
                "info_tables": [
                    {
                        "ACCESSION_NUMBER": "0001234567-26-000001",
                        "INFOTABLE_SK": "1",
                        "NAMEOFISSUER": "NVIDIA CORP",
                        "TITLEOFCLASS": "COM",
                        "CUSIP": "67066G104",
                        "FIGI": "BBG000BBJQV0",
                        "VALUE": "1000",
                        "SSHPRNAMT": "50",
                        "SSHPRNAMTTYPE": "SH",
                        "PUTCALL": "CALL",
                        "INVESTMENTDISCRETION": "SOLE",
                        "VOTING_AUTH_SOLE": "50",
                        "VOTING_AUTH_SHARED": "0",
                        "VOTING_AUTH_NONE": "0",
                    }
                ],
            },
        },
    )

    first = await repository.save_raw_items([item])
    second = await repository.save_raw_items([item])
    holdings = await repository.read_sec_13f_holdings(manager="berkshire", issuer="nvidia", cusip="67066g104", limit=20)

    assert first.inserted_count == 1
    assert second.updated_count == 1
    assert len(holdings) == 1
    assert holdings[0].manager_name == "Berkshire Hathaway Inc"
    assert holdings[0].issuer == "NVIDIA CORP"
    assert holdings[0].filing_date_utc == "2026-05-31"
    assert holdings[0].period_of_report_utc == "2026-03-31"
    assert holdings[0].value == 1000
    assert holdings[0].ssh_prn_amt == 50
    assert "Archives/edgar/data/1067983" in (holdings[0].filing_url or "")


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
