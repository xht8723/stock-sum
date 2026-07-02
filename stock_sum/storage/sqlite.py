"""SQLite storage repository implementation."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import json
from typing import Any

import aiosqlite

from stock_sum.collectors.api.house import normalize_house_date, normalize_house_name, normalize_house_transaction_action
from stock_sum.core.models import ProviderApiResponse, RawItem, RawItemSaveResult
from stock_sum.storage.mappers import MappedRawItem, map_raw_item
from stock_sum.storage.models import (
    StoredCollectionRun,
    StoredDownloadedMedia,
    StoredHousePtrTradeRow,
    StoredMediaAsset,
    StoredRedditComment,
    StoredRedditPost,
    StoredXPost,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    profile TEXT,
    collector_id TEXT NOT NULL,
    source_type TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    collected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS raw_item_index (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    canonical_url TEXT,
    collected_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    latest_seen_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (source_type, source_id)
);

CREATE TABLE IF NOT EXISTS raw_x_posts (
    status_id TEXT NOT NULL,
    handle TEXT NOT NULL,
    author_handle TEXT,
    author_name TEXT,
    posted_at_text TEXT,
    url TEXT,
    text TEXT NOT NULL,
    reply_count INTEGER,
    repost_count INTEGER,
    like_count INTEGER,
    quote_count INTEGER,
    view_count INTEGER,
    raw_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    posted_at_utc TEXT,
    PRIMARY KEY (handle, status_id)
);

CREATE TABLE IF NOT EXISTS raw_x_post_media (
    status_id TEXT NOT NULL,
    media_key TEXT,
    media_type TEXT,
    media_url TEXT NOT NULL,
    alt_text TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (status_id, media_url)
);

CREATE TABLE IF NOT EXISTS raw_reddit_posts (
    post_id TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    fullname TEXT,
    title TEXT NOT NULL,
    author TEXT,
    url TEXT,
    permalink TEXT,
    selftext TEXT NOT NULL,
    score INTEGER,
    ups INTEGER,
    upvote_ratio REAL,
    num_comments INTEGER,
    thumbnail_url TEXT,
    created_at_text TEXT,
    raw_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    created_at_utc TEXT,
    PRIMARY KEY (subreddit, post_id)
);

CREATE TABLE IF NOT EXISTS raw_reddit_comments (
    comment_id TEXT NOT NULL,
    post_id TEXT NOT NULL,
    parent_id TEXT,
    author TEXT,
    body TEXT NOT NULL,
    score INTEGER,
    ups INTEGER,
    url TEXT,
    created_at_text TEXT,
    depth INTEGER,
    raw_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    created_at_utc TEXT,
    PRIMARY KEY (post_id, comment_id)
);

CREATE TABLE IF NOT EXISTS raw_reddit_post_media (
    post_id TEXT NOT NULL,
    media_type TEXT,
    media_url TEXT NOT NULL,
    source_field TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (post_id, media_url)
);

CREATE TABLE IF NOT EXISTS downloaded_media (
    remote_url_hash TEXT PRIMARY KEY,
    remote_url TEXT NOT NULL UNIQUE,
    local_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    downloaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_provider_api_responses (
    response_id TEXT PRIMARY KEY,
    collection_run_id TEXT NOT NULL,
    collector_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    request_arguments_json TEXT NOT NULL,
    raw_response_text TEXT NOT NULL,
    parsed_rows_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_api_responses_run
ON raw_provider_api_responses (collection_run_id);

CREATE TABLE IF NOT EXISTS raw_house_ptr_filings (
    doc_id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    name TEXT,
    prefix TEXT,
    first_name TEXT,
    last_name TEXT,
    suffix TEXT,
    display_name TEXT,
    name_normalized TEXT,
    status TEXT,
    state TEXT,
    filing_date TEXT,
    filing_date_utc TEXT,
    pdf_url TEXT,
    raw_xml_json TEXT NOT NULL,
    tables_json TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    extraction_error TEXT,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_house_ptr_trade_rows (
    doc_id TEXT NOT NULL,
    table_index INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    asset TEXT,
    transaction_type TEXT,
    transaction_date TEXT,
    transaction_date_utc TEXT,
    transaction_action TEXT,
    amount TEXT,
    raw_cells_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (doc_id, table_index, row_index)
);

CREATE INDEX IF NOT EXISTS idx_house_ptr_filings_recent
ON raw_house_ptr_filings (filing_date DESC, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_house_ptr_filings_name
ON raw_house_ptr_filings (name_normalized);

CREATE INDEX IF NOT EXISTS idx_house_ptr_trade_rows_transaction_date
ON raw_house_ptr_trade_rows (transaction_date_utc);

CREATE TABLE IF NOT EXISTS llm_analysis_runs (
    analysis_run_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    instructions TEXT,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS llm_x_post_analyses (
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

CREATE TABLE IF NOT EXISTS llm_reddit_post_analyses (
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

CREATE TABLE IF NOT EXISTS llm_reddit_comment_analyses (
    analysis_run_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    post_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    parent_id TEXT,
    sentiment TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence TEXT NOT NULL,
    raw_response_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_run_id, post_id, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_llm_analysis_runs_profile
ON llm_analysis_runs (profile, finished_at);

CREATE INDEX IF NOT EXISTS idx_llm_x_post_analyses_profile
ON llm_x_post_analyses (profile, handle);

CREATE INDEX IF NOT EXISTS idx_llm_reddit_post_analyses_profile
ON llm_reddit_post_analyses (profile, subreddit);
"""


HOUSE_PTR_FILING_COLUMNS = {
    "prefix": "TEXT",
    "first_name": "TEXT",
    "last_name": "TEXT",
    "suffix": "TEXT",
    "display_name": "TEXT",
    "name_normalized": "TEXT",
    "filing_date_utc": "TEXT",
}

HOUSE_PTR_TRADE_COLUMNS = {
    "transaction_date_utc": "TEXT",
    "transaction_action": "TEXT",
}


async def _ensure_schema_updates(db: aiosqlite.Connection) -> None:
    """Apply additive schema updates for existing SQLite databases."""

    await _ensure_columns(db, "raw_house_ptr_filings", HOUSE_PTR_FILING_COLUMNS)
    await _ensure_columns(db, "raw_house_ptr_trade_rows", HOUSE_PTR_TRADE_COLUMNS)


async def _ensure_columns(db: aiosqlite.Connection, table: str, columns: dict[str, str]) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    try:
        existing = {str(row[1]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    for name, definition in columns.items():
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


async def _backfill_house_ptr_normalized_columns(db: aiosqlite.Connection) -> None:
    """Best-effort backfill for House PTR normalized query columns."""

    cursor = await db.execute(
        """
        SELECT doc_id, name, raw_xml_json, filing_date
        FROM raw_house_ptr_filings
        WHERE name_normalized IS NULL OR display_name IS NULL OR filing_date_utc IS NULL
        """
    )
    try:
        filing_rows = await cursor.fetchall()
    finally:
        await cursor.close()
    for doc_id, name, raw_xml_json, filing_date in filing_rows:
        raw = _json_obj(raw_xml_json)
        prefix = raw.get("prefix")
        first_name = raw.get("first")
        last_name = raw.get("last")
        suffix = raw.get("suffix")
        display_name = name or " ".join(part for part in (prefix, first_name, last_name, suffix) if part) or None
        await db.execute(
            """
            UPDATE raw_house_ptr_filings
            SET prefix = COALESCE(prefix, ?),
                first_name = COALESCE(first_name, ?),
                last_name = COALESCE(last_name, ?),
                suffix = COALESCE(suffix, ?),
                display_name = COALESCE(display_name, ?),
                name_normalized = COALESCE(name_normalized, ?),
                filing_date_utc = COALESCE(filing_date_utc, ?)
            WHERE doc_id = ?
            """,
            (
                prefix,
                first_name,
                last_name,
                suffix,
                display_name,
                normalize_house_name(display_name),
                normalize_house_date(filing_date),
                doc_id,
            ),
        )

    cursor = await db.execute(
        """
        SELECT doc_id, table_index, row_index, transaction_type, transaction_date
        FROM raw_house_ptr_trade_rows
        WHERE transaction_date_utc IS NULL OR transaction_action IS NULL
        """
    )
    try:
        trade_rows = await cursor.fetchall()
    finally:
        await cursor.close()
    for doc_id, table_index, row_index, transaction_type, transaction_date in trade_rows:
        await db.execute(
            """
            UPDATE raw_house_ptr_trade_rows
            SET transaction_date_utc = COALESCE(transaction_date_utc, ?),
                transaction_action = COALESCE(transaction_action, ?)
            WHERE doc_id = ? AND table_index = ? AND row_index = ?
            """,
            (
                normalize_house_date(transaction_date),
                normalize_house_transaction_action(transaction_type),
                doc_id,
                table_index,
                row_index,
            ),
        )


class SQLiteStorageRepository:
    """SQLite repository for collection runs and source-specific raw items."""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)

    async def initialize(self) -> None:
        """Create database tables if they do not exist."""

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.executescript(SCHEMA_SQL)
            await _ensure_schema_updates(db)
            await _backfill_house_ptr_normalized_columns(db)
            await db.commit()

    async def start_collection_run(
        self,
        *,
        run_id: str,
        collector_id: str,
        profile: str | None = None,
        source_type: str | None = None,
    ) -> None:
        """Insert an in-progress collection run row."""

        await self.initialize()
        now = _utc_now()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO collection_runs (
                    run_id, profile, collector_id, source_type, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, profile, collector_id, source_type, "running", now),
            )
            await db.commit()

    async def finish_collection_run(
        self,
        *,
        run_id: str,
        status: str,
        collected_count: int = 0,
        inserted_count: int = 0,
        updated_count: int = 0,
        source_type: str | None = None,
        error_text: str | None = None,
    ) -> None:
        """Mark a collection run as complete or failed."""

        now = _utc_now()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                UPDATE collection_runs
                SET status = ?,
                    finished_at = ?,
                    collected_count = ?,
                    inserted_count = ?,
                    updated_count = ?,
                    source_type = COALESCE(?, source_type),
                    error_text = ?
                WHERE run_id = ?
                """,
                (status, now, collected_count, inserted_count, updated_count, source_type, error_text, run_id),
            )
            await db.commit()

    async def save_raw_items(self, items: list[RawItem]) -> RawItemSaveResult:
        """Persist raw items into source-specific tables and the shared index."""

        await self.initialize()
        if not items:
            return RawItemSaveResult(source_type="", collected_count=0, inserted_count=0, updated_count=0)

        inserted = 0
        updated = 0
        async with aiosqlite.connect(self.sqlite_path) as db:
            for item in items:
                mapped_item = map_raw_item(item)
                row_existed = await _mapped_row_exists(db, mapped_item)
                await _upsert_mapped_item(db, mapped_item)
                await _upsert_index_row(db, item)
                if row_existed:
                    updated += 1
                else:
                    inserted += 1
            await db.commit()

        source_types = {item.source_type for item in items}
        return RawItemSaveResult(
            source_type=items[0].source_type if len(source_types) == 1 else "mixed",
            collected_count=len(items),
            inserted_count=inserted,
            updated_count=updated,
        )

    async def save_provider_api_responses(
        self,
        *,
        collection_run_id: str,
        collector_id: str,
        responses: list[ProviderApiResponse],
    ) -> None:
        """Persist raw provider API/tool responses for one collection run."""

        await self.initialize()
        if not responses:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for response in responses:
                await db.execute(
                    """
                    INSERT INTO raw_provider_api_responses (
                        response_id, collection_run_id, collector_id, provider, tool_name,
                        request_arguments_json, raw_response_text, parsed_rows_json,
                        row_count, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        collection_run_id,
                        collector_id,
                        response.provider,
                        response.tool_name,
                        json.dumps(response.request_arguments, ensure_ascii=False, sort_keys=True, default=str),
                        response.raw_response_text,
                        json.dumps(response.parsed_rows, ensure_ascii=False, sort_keys=True, default=str),
                        response.row_count,
                        response.fetched_at.isoformat(),
                    ),
                )
            await db.commit()

    async def start_llm_analysis_run(
        self,
        *,
        analysis_run_id: str,
        profile: str,
        provider: str,
        model: str,
        prompt_version: str,
        instructions: str | None = None,
    ) -> None:
        """Insert an in-progress LLM analysis run."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO llm_analysis_runs (
                    analysis_run_id, profile, provider, model, prompt_version,
                    status, started_at, instructions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (analysis_run_id, profile, provider, model, prompt_version, "running", _utc_now(), instructions),
            )
            await db.commit()

    async def finish_llm_analysis_run(
        self,
        *,
        analysis_run_id: str,
        status: str,
        chunk_count: int = 0,
        succeeded_count: int = 0,
        failed_count: int = 0,
        error_text: str | None = None,
    ) -> None:
        """Mark a chunked LLM analysis run complete or failed."""

        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                UPDATE llm_analysis_runs
                SET status = ?,
                    finished_at = ?,
                    chunk_count = ?,
                    succeeded_count = ?,
                    failed_count = ?,
                    error_text = ?
                WHERE analysis_run_id = ?
                """,
                (status, _utc_now(), chunk_count, succeeded_count, failed_count, error_text, analysis_run_id),
            )
            await db.commit()

    async def save_llm_x_post_analyses(self, rows: list[dict]) -> None:
        """Persist X post analysis rows."""

        await self.initialize()
        if not rows:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO llm_x_post_analyses (
                        analysis_run_id, profile, handle, status_id, source_ref, url,
                        posted_at_text, sentiment, tags_json, summary, interpretation,
                        confidence, raw_response_json, analyzed_at
                    ) VALUES (
                        :analysis_run_id, :profile, :handle, :status_id, :source_ref, :url,
                        :posted_at_text, :sentiment, :tags_json, :summary, :interpretation,
                        :confidence, :raw_response_json, :analyzed_at
                    )
                    ON CONFLICT (analysis_run_id, status_id) DO UPDATE SET
                        source_ref = excluded.source_ref,
                        url = excluded.url,
                        posted_at_text = excluded.posted_at_text,
                        sentiment = excluded.sentiment,
                        tags_json = excluded.tags_json,
                        summary = excluded.summary,
                        interpretation = excluded.interpretation,
                        confidence = excluded.confidence,
                        raw_response_json = excluded.raw_response_json,
                        analyzed_at = excluded.analyzed_at
                    """,
                    row,
                )
            await db.commit()

    async def save_llm_reddit_post_analyses(self, rows: list[dict]) -> None:
        """Persist Reddit post analysis rows."""

        await self.initialize()
        if not rows:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO llm_reddit_post_analyses (
                        analysis_run_id, profile, subreddit, post_id, source_ref, title,
                        url, created_at_text, sentiment, tags_json, summary, interpretation,
                        confidence, comment_sentiment_counts_json, raw_response_json, analyzed_at
                    ) VALUES (
                        :analysis_run_id, :profile, :subreddit, :post_id, :source_ref, :title,
                        :url, :created_at_text, :sentiment, :tags_json, :summary, :interpretation,
                        :confidence, :comment_sentiment_counts_json, :raw_response_json, :analyzed_at
                    )
                    ON CONFLICT (analysis_run_id, post_id) DO UPDATE SET
                        source_ref = excluded.source_ref,
                        title = excluded.title,
                        url = excluded.url,
                        created_at_text = excluded.created_at_text,
                        sentiment = excluded.sentiment,
                        tags_json = excluded.tags_json,
                        summary = excluded.summary,
                        interpretation = excluded.interpretation,
                        confidence = excluded.confidence,
                        comment_sentiment_counts_json = excluded.comment_sentiment_counts_json,
                        raw_response_json = excluded.raw_response_json,
                        analyzed_at = excluded.analyzed_at
                    """,
                    row,
                )
            await db.commit()

    async def save_llm_reddit_comment_analyses(self, rows: list[dict]) -> None:
        """Persist Reddit comment analysis rows."""

        await self.initialize()
        if not rows:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO llm_reddit_comment_analyses (
                        analysis_run_id, profile, subreddit, post_id, comment_id, source_ref,
                        parent_id, sentiment, summary, confidence, raw_response_json, analyzed_at
                    ) VALUES (
                        :analysis_run_id, :profile, :subreddit, :post_id, :comment_id, :source_ref,
                        :parent_id, :sentiment, :summary, :confidence, :raw_response_json, :analyzed_at
                    )
                    ON CONFLICT (analysis_run_id, post_id, comment_id) DO UPDATE SET
                        source_ref = excluded.source_ref,
                        parent_id = excluded.parent_id,
                        sentiment = excluded.sentiment,
                        summary = excluded.summary,
                        confidence = excluded.confidence,
                        raw_response_json = excluded.raw_response_json,
                        analyzed_at = excluded.analyzed_at
                    """,
                    row,
                )
            await db.commit()

    async def read_llm_analysis_report(self, *, profile: str, analysis_run_id: str | None = None) -> dict:
        """Read stored analysis rows as renderer-ready summary data."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            run_id = analysis_run_id or await _latest_analysis_run_id(db, profile)
            if run_id is None:
                return {"x_reports": [], "reddit_report": {"overall_summary": [], "posts": []}}
            x_reports = await _read_analysis_x_reports(db, profile, run_id)
            reddit_report = await _read_analysis_reddit_report(db, profile, run_id)
        return {"x_reports": x_reports, "reddit_report": reddit_report}

    async def list_collection_runs(
        self,
        *,
        profile: str | None = None,
        limit: int | None = None,
    ) -> list[StoredCollectionRun]:
        """Return stored collection runs."""

        await self.initialize()
        query = """
            SELECT run_id, profile, collector_id, source_type, status, started_at, finished_at,
                   collected_count, inserted_count, updated_count, error_text
            FROM collection_runs
        """
        params: list[Any] = []
        if profile is not None:
            query += " WHERE profile = ?"
            params.append(profile)
        query += " ORDER BY started_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        return [
            StoredCollectionRun(
                run_id=row[0],
                profile=row[1],
                collector_id=row[2],
                source_type=row[3],
                status=row[4],
                started_at=row[5],
                finished_at=row[6],
                collected_count=row[7],
                inserted_count=row[8],
                updated_count=row[9],
                error_text=row[10],
            )
            for row in rows
        ]

    async def read_x_posts(
        self,
        *,
        handles: list[str] | None = None,
        since_posted_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredXPost]:
        """Read stored X posts with media."""

        await self.initialize()
        query = """
            SELECT status_id, handle, author_handle, author_name, posted_at_text, url, text,
                   reply_count, repost_count, like_count, quote_count, view_count,
                   raw_json, collected_at
            FROM raw_x_posts
        """
        params: list[Any] = []
        conditions: list[str] = []
        if handles:
            placeholders = ",".join("?" for _ in handles)
            conditions.append(f"handle IN ({placeholders})")
            params.extend(handles)
        if since_posted_at is not None:
            conditions.append("posted_at_utc >= ?")
            params.append(_utc_datetime(since_posted_at).isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY posted_at_utc DESC, CAST(status_id AS INTEGER) DESC, collected_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

            posts: list[StoredXPost] = []
            for row in rows:
                posts.append(
                    StoredXPost(
                        status_id=row[0],
                        handle=row[1],
                        author_handle=row[2],
                        author_name=row[3],
                        posted_at_text=row[4],
                        url=row[5],
                        text=row[6],
                        reply_count=row[7],
                        repost_count=row[8],
                        like_count=row[9],
                        quote_count=row[10],
                        view_count=row[11],
                        raw_metadata=_json_obj(row[12]),
                        collected_at=row[13],
                        media=await _read_x_media(db, row[0]),
                    )
                )
        return posts

    async def read_reddit_posts(
        self,
        *,
        subreddits: list[str] | None = None,
        since_posted_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredRedditPost]:
        """Read stored Reddit posts with media and comments."""

        await self.initialize()
        query = """
            SELECT post_id, subreddit, fullname, title, author, url, permalink, selftext,
                   score, ups, upvote_ratio, num_comments, thumbnail_url, created_at_text,
                   raw_json, collected_at
            FROM raw_reddit_posts
        """
        params: list[Any] = []
        conditions: list[str] = []
        if subreddits:
            placeholders = ",".join("?" for _ in subreddits)
            conditions.append(f"subreddit IN ({placeholders})")
            params.extend(subreddits)
        if since_posted_at is not None:
            conditions.append("created_at_utc >= ?")
            params.append(_utc_datetime(since_posted_at).isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at_utc DESC, collected_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

            posts: list[StoredRedditPost] = []
            for row in rows:
                post_id = row[0]
                posts.append(
                    StoredRedditPost(
                        post_id=post_id,
                        subreddit=row[1],
                        fullname=row[2],
                        title=row[3],
                        author=row[4],
                        url=row[5],
                        permalink=row[6],
                        selftext=row[7],
                        score=row[8],
                        ups=row[9],
                        upvote_ratio=row[10],
                        num_comments=row[11],
                        thumbnail_url=row[12],
                        created_at_text=row[13],
                        raw_metadata=_json_obj(row[14]),
                        collected_at=row[15],
                        media=await _read_reddit_media(db, post_id),
                        comments=await _read_reddit_comments(db, post_id),
                    )
                )
        return posts

    async def existing_house_ptr_doc_ids(self, *, year: int | None = None) -> set[str]:
        """Return successfully extracted House PTR DocIDs safe to skip."""

        await self.initialize()
        query = "SELECT doc_id FROM raw_house_ptr_filings WHERE extraction_status = ?"
        params: list[Any] = ["succeeded"]
        if year is not None:
            query += " AND year = ?"
            params.append(year)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return {str(row[0]) for row in rows}

    async def read_house_ptr_trades(
        self,
        *,
        name_contains: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredHousePtrTradeRow]:
        """Read House PTR trade rows joined with filing metadata."""

        await self.initialize()
        query = """
            SELECT f.doc_id, f.year, COALESCE(f.display_name, f.name), f.status, f.state,
                   f.filing_date, f.filing_date_utc, f.pdf_url,
                   r.table_index, r.row_index, r.asset, r.transaction_type, r.transaction_date,
                   r.transaction_date_utc, r.transaction_action, r.amount, r.raw_cells_json,
                   r.raw_json, f.collected_at
            FROM raw_house_ptr_trade_rows r
            JOIN raw_house_ptr_filings f ON f.doc_id = r.doc_id
        """
        conditions: list[str] = []
        params: list[Any] = []
        normalized_name = normalize_house_name(name_contains)
        if normalized_name:
            conditions.append("f.name_normalized LIKE ?")
            params.append(f"%{normalized_name}%")
        if transaction_start is not None:
            conditions.append("r.transaction_date_utc >= ?")
            params.append(_datetime_param(transaction_start))
        if transaction_end is not None:
            conditions.append("r.transaction_date_utc <= ?")
            params.append(_datetime_param(transaction_end, end_of_day=True))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += """
            ORDER BY COALESCE(r.transaction_date_utc, f.filing_date_utc, f.collected_at) DESC,
                     f.collected_at DESC,
                     f.doc_id DESC, r.table_index ASC, r.row_index ASC
        """
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [
            StoredHousePtrTradeRow(
                doc_id=row[0],
                year=row[1],
                name=row[2],
                status=row[3],
                state=row[4],
                filing_date=row[5],
                filing_date_utc=row[6],
                pdf_url=row[7],
                table_index=row[8],
                row_index=row[9],
                asset=row[10],
                transaction_type=row[11],
                transaction_date=row[12],
                transaction_date_utc=row[13],
                transaction_action=row[14],
                amount=row[15],
                raw_cells=_json_list(row[16]),
                raw_metadata=_json_obj(row[17]),
                collected_at=row[18],
            )
            for row in rows
        ]

    async def get_downloaded_media(self, remote_url: str) -> StoredDownloadedMedia | None:
        """Return downloaded media metadata by remote URL."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            return await _get_downloaded_media(db, remote_url)

    async def save_downloaded_media(self, media: StoredDownloadedMedia) -> None:
        """Persist downloaded media metadata."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await _upsert_downloaded_media(db, media)
            await db.commit()

    async def save_summaries(self, summaries: list[Any]) -> None:
        """Persist generated summaries."""

        raise NotImplementedError("Summary storage is not implemented yet.")

    async def save_report(self, report: Any) -> None:
        """Persist a rendered report."""

        raise NotImplementedError("Report storage is not implemented yet.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_param(value: datetime | date, *, end_of_day: bool = False) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.combine(value, time.max if end_of_day else time.min)
    return _utc_datetime(parsed).isoformat()


def _content_hash(item: RawItem) -> str:
    payload = json.dumps(
        {
            "url": item.url,
            "text": item.text,
            "metadata": item.metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _remote_url_hash(remote_url: str) -> str:
    return hashlib.sha256(remote_url.encode("utf-8")).hexdigest()


def _json_obj(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


async def _latest_analysis_run_id(db: aiosqlite.Connection, profile: str) -> str | None:
    cursor = await db.execute(
        """
        SELECT analysis_run_id
        FROM llm_analysis_runs
        WHERE profile = ? AND status = 'succeeded'
        ORDER BY finished_at DESC, started_at DESC
        LIMIT 1
        """,
        (profile,),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row[0] if row else None


async def _read_analysis_x_reports(db: aiosqlite.Connection, profile: str, analysis_run_id: str) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT handle, status_id, source_ref, url, posted_at_text, sentiment, tags_json,
               summary, interpretation, confidence
        FROM llm_x_post_analyses
        WHERE profile = ? AND analysis_run_id = ?
        ORDER BY handle, posted_at_text DESC, status_id DESC
        """,
        (profile, analysis_run_id),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(
            {
                "source_ref": row[2],
                "source_id": row[1],
                "title": _analysis_title(row[7]),
                "post_summary": row[7],
                "sentiment": row[5],
                "tags": _json_list(row[6]),
                "interpretation": row[8],
                "confidence": row[9],
                "urls": [row[3]] if row[3] else [],
            }
        )
    return [
        {
            "handle": handle,
            "overall_summary": [f"{len(posts)} analyzed X post{'s' if len(posts) != 1 else ''}."],
            "posts": posts,
        }
        for handle, posts in grouped.items()
    ]


async def _read_analysis_reddit_report(db: aiosqlite.Connection, profile: str, analysis_run_id: str) -> dict[str, Any]:
    cursor = await db.execute(
        """
        SELECT subreddit, post_id, source_ref, title, url, created_at_text, sentiment, tags_json,
               summary, interpretation, confidence, comment_sentiment_counts_json
        FROM llm_reddit_post_analyses
        WHERE profile = ? AND analysis_run_id = ?
        ORDER BY created_at_text DESC, post_id DESC
        """,
        (profile, analysis_run_id),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()

    posts = []
    total_comments = 0
    totals = {"bullish": 0, "bearish": 0, "mixed": 0, "neutral": 0, "unclear": 0}
    for row in rows:
        counts = _json_obj(row[11])
        for sentiment in totals:
            value = counts.get(sentiment)
            if isinstance(value, int):
                totals[sentiment] += value
                total_comments += value
        posts.append(
            {
                "source_ref": row[2],
                "source_id": row[1],
                "subreddit": row[0],
                "title": row[3],
                "post_summary": row[8],
                "comments_sentiment": _comment_counts_text(counts),
                "comment_sentiment_counts": counts,
                "sentiment": row[6],
                "tags": _json_list(row[7]),
                "interpretation": row[9],
                "confidence": row[10],
                "urls": [row[4]] if row[4] else [],
            }
        )
    return {
        "overall_summary": [
            f"{len(posts)} analyzed Reddit post{'s' if len(posts) != 1 else ''} with {total_comments} analyzed comments."
        ],
        "comment_sentiment_counts": totals,
        "posts": posts,
    }


def _analysis_title(summary: str | None) -> str:
    text = (summary or "Signal").strip()
    if len(text) <= 80:
        return text or "Signal"
    return text[:77].rstrip() + "..."


def _comment_counts_text(counts: dict[str, Any]) -> str:
    parts = [f"{key}: {counts.get(key, 0)}" for key in ("bullish", "bearish", "mixed", "neutral", "unclear")]
    return ", ".join(parts)


async def _read_x_media(db: aiosqlite.Connection, status_id: str) -> list[StoredMediaAsset]:
    cursor = await db.execute(
        """
        SELECT media_type, media_url, raw_json
        FROM raw_x_post_media
        WHERE status_id = ?
        ORDER BY media_url
        """,
        (status_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [await _stored_media_asset(db, row[1], row[0], row[2]) for row in rows]


async def _read_reddit_media(db: aiosqlite.Connection, post_id: str) -> list[StoredMediaAsset]:
    cursor = await db.execute(
        """
        SELECT media_type, media_url, raw_json
        FROM raw_reddit_post_media
        WHERE post_id = ?
        ORDER BY media_url
        """,
        (post_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [await _stored_media_asset(db, row[1], row[0], row[2]) for row in rows]


async def _read_reddit_comments(db: aiosqlite.Connection, post_id: str) -> list[StoredRedditComment]:
    cursor = await db.execute(
        """
        SELECT comment_id, post_id, parent_id, author, body, score, ups, url,
               created_at_text, depth, raw_json, collected_at
        FROM raw_reddit_comments
        WHERE post_id = ?
        ORDER BY COALESCE(depth, 0), created_at_text, comment_id
        """,
        (post_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        StoredRedditComment(
            comment_id=row[0],
            post_id=row[1],
            parent_id=row[2],
            author=row[3],
            body=row[4],
            score=row[5],
            ups=row[6],
            url=row[7],
            created_at_text=row[8],
            depth=row[9],
            raw_metadata=_json_obj(row[10]),
            collected_at=row[11],
        )
        for row in rows
    ]


async def _stored_media_asset(
    db: aiosqlite.Connection,
    remote_url: str,
    media_type: str | None,
    raw_json_value: str | None,
) -> StoredMediaAsset:
    downloaded = await _get_downloaded_media(db, remote_url)
    return StoredMediaAsset(
        remote_url=remote_url,
        media_type=media_type,
        raw_metadata=_json_obj(raw_json_value),
        local_path=downloaded.local_path if downloaded else None,
        content_type=downloaded.content_type if downloaded else None,
        byte_size=downloaded.byte_size if downloaded else None,
        sha256=downloaded.sha256 if downloaded else None,
    )

async def _get_downloaded_media(db: aiosqlite.Connection, remote_url: str) -> StoredDownloadedMedia | None:
    cursor = await db.execute(
        """
        SELECT remote_url_hash, remote_url, local_path, content_type, byte_size, sha256, downloaded_at
        FROM downloaded_media
        WHERE remote_url_hash = ?
        """,
        (_remote_url_hash(remote_url),),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        return None
    return StoredDownloadedMedia(
        remote_url_hash=row[0],
        remote_url=row[1],
        local_path=row[2],
        content_type=row[3],
        byte_size=row[4],
        sha256=row[5],
        downloaded_at=row[6],
    )


async def _upsert_downloaded_media(db: aiosqlite.Connection, media: StoredDownloadedMedia) -> None:
    await db.execute(
        """
        INSERT INTO downloaded_media (
            remote_url_hash, remote_url, local_path, content_type, byte_size, sha256, downloaded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (remote_url_hash) DO UPDATE SET
            remote_url = excluded.remote_url,
            local_path = excluded.local_path,
            content_type = excluded.content_type,
            byte_size = excluded.byte_size,
            sha256 = excluded.sha256,
            downloaded_at = excluded.downloaded_at
        """,
        (
            media.remote_url_hash,
            media.remote_url,
            media.local_path,
            media.content_type,
            media.byte_size,
            media.sha256,
            media.downloaded_at,
        ),
    )


async def _mapped_row_exists(db: aiosqlite.Connection, item: MappedRawItem) -> bool:
    if item.table == "raw_x_posts":
        query = "SELECT 1 FROM raw_x_posts WHERE handle = ? AND status_id = ?"
    elif item.table == "raw_reddit_posts":
        query = "SELECT 1 FROM raw_reddit_posts WHERE subreddit = ? AND post_id = ?"
    elif item.table == "raw_reddit_comments":
        query = "SELECT 1 FROM raw_reddit_comments WHERE post_id = ? AND comment_id = ?"
    elif item.table == "raw_house_ptr_filings":
        query = "SELECT 1 FROM raw_house_ptr_filings WHERE doc_id = ?"
    else:
        raise ValueError(f"Unsupported mapped table: {item.table}")
    cursor = await db.execute(query, item.key)
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


async def _upsert_mapped_item(db: aiosqlite.Connection, item: MappedRawItem) -> None:
    if item.table == "raw_x_posts":
        await _upsert_x_post(db, item.row)
        for media_row in item.media_rows:
            await _upsert_x_media(db, media_row)
        return
    if item.table == "raw_reddit_posts":
        await _upsert_reddit_post(db, item.row)
        for media_row in item.media_rows:
            await _upsert_reddit_media(db, media_row)
        return
    if item.table == "raw_reddit_comments":
        await _upsert_reddit_comment(db, item.row)
        return
    if item.table == "raw_house_ptr_filings":
        await _upsert_house_ptr_filing(db, item.row)
        await _delete_house_ptr_trade_rows(db, item.row["doc_id"])
        for trade_row in item.media_rows:
            await _upsert_house_ptr_trade_row(db, trade_row)
        return
    raise ValueError(f"Unsupported mapped table: {item.table}")


async def _upsert_x_post(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_x_posts (
            status_id, handle, author_handle, author_name, posted_at_text, url, text,
            reply_count, repost_count, like_count, quote_count, view_count, raw_json, collected_at,
            posted_at_utc
        ) VALUES (
            :status_id, :handle, :author_handle, :author_name, :posted_at_text, :url, :text,
            :reply_count, :repost_count, :like_count, :quote_count, :view_count, :raw_json, :collected_at,
            :posted_at_utc
        )
        ON CONFLICT (handle, status_id) DO UPDATE SET
            author_handle = excluded.author_handle,
            author_name = excluded.author_name,
            posted_at_text = excluded.posted_at_text,
            url = excluded.url,
            text = excluded.text,
            reply_count = excluded.reply_count,
            repost_count = excluded.repost_count,
            like_count = excluded.like_count,
            quote_count = excluded.quote_count,
            view_count = excluded.view_count,
            raw_json = excluded.raw_json,
            collected_at = excluded.collected_at,
            posted_at_utc = excluded.posted_at_utc
        """,
        row,
    )


async def _upsert_x_media(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_x_post_media (
            status_id, media_key, media_type, media_url, alt_text, raw_json
        ) VALUES (
            :status_id, :media_key, :media_type, :media_url, :alt_text, :raw_json
        )
        ON CONFLICT (status_id, media_url) DO UPDATE SET
            media_key = excluded.media_key,
            media_type = excluded.media_type,
            alt_text = excluded.alt_text,
            raw_json = excluded.raw_json
        """,
        row,
    )


async def _upsert_reddit_post(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_reddit_posts (
            post_id, subreddit, fullname, title, author, url, permalink, selftext,
            score, ups, upvote_ratio, num_comments, thumbnail_url, created_at_text,
            raw_json, collected_at, created_at_utc
        ) VALUES (
            :post_id, :subreddit, :fullname, :title, :author, :url, :permalink, :selftext,
            :score, :ups, :upvote_ratio, :num_comments, :thumbnail_url, :created_at_text,
            :raw_json, :collected_at, :created_at_utc
        )
        ON CONFLICT (subreddit, post_id) DO UPDATE SET
            fullname = excluded.fullname,
            title = excluded.title,
            author = excluded.author,
            url = excluded.url,
            permalink = excluded.permalink,
            selftext = excluded.selftext,
            score = excluded.score,
            ups = excluded.ups,
            upvote_ratio = excluded.upvote_ratio,
            num_comments = excluded.num_comments,
            thumbnail_url = excluded.thumbnail_url,
            created_at_text = excluded.created_at_text,
            raw_json = excluded.raw_json,
            collected_at = excluded.collected_at,
            created_at_utc = excluded.created_at_utc
        """,
        row,
    )


async def _upsert_reddit_comment(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_reddit_comments (
            comment_id, post_id, parent_id, author, body, score, ups, url,
            created_at_text, depth, raw_json, collected_at, created_at_utc
        ) VALUES (
            :comment_id, :post_id, :parent_id, :author, :body, :score, :ups, :url,
            :created_at_text, :depth, :raw_json, :collected_at, :created_at_utc
        )
        ON CONFLICT (post_id, comment_id) DO UPDATE SET
            parent_id = excluded.parent_id,
            author = excluded.author,
            body = excluded.body,
            score = excluded.score,
            ups = excluded.ups,
            url = excluded.url,
            created_at_text = excluded.created_at_text,
            depth = excluded.depth,
            raw_json = excluded.raw_json,
            collected_at = excluded.collected_at,
            created_at_utc = excluded.created_at_utc
        """,
        row,
    )


async def _upsert_reddit_media(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_reddit_post_media (
            post_id, media_type, media_url, source_field, raw_json
        ) VALUES (
            :post_id, :media_type, :media_url, :source_field, :raw_json
        )
        ON CONFLICT (post_id, media_url) DO UPDATE SET
            media_type = excluded.media_type,
            source_field = excluded.source_field,
            raw_json = excluded.raw_json
        """,
        row,
    )


async def _upsert_house_ptr_filing(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_house_ptr_filings (
            doc_id, year, name, prefix, first_name, last_name, suffix, display_name,
            name_normalized, status, state, filing_date, filing_date_utc, pdf_url,
            raw_xml_json, tables_json, extraction_status, extraction_error, collected_at
        ) VALUES (
            :doc_id, :year, :name, :prefix, :first_name, :last_name, :suffix, :display_name,
            :name_normalized, :status, :state, :filing_date, :filing_date_utc, :pdf_url,
            :raw_xml_json, :tables_json, :extraction_status, :extraction_error, :collected_at
        )
        ON CONFLICT (doc_id) DO UPDATE SET
            year = excluded.year,
            name = excluded.name,
            prefix = excluded.prefix,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            suffix = excluded.suffix,
            display_name = excluded.display_name,
            name_normalized = excluded.name_normalized,
            status = excluded.status,
            state = excluded.state,
            filing_date = excluded.filing_date,
            filing_date_utc = excluded.filing_date_utc,
            pdf_url = excluded.pdf_url,
            raw_xml_json = excluded.raw_xml_json,
            tables_json = excluded.tables_json,
            extraction_status = excluded.extraction_status,
            extraction_error = excluded.extraction_error,
            collected_at = excluded.collected_at
        """,
        row,
    )


async def _delete_house_ptr_trade_rows(db: aiosqlite.Connection, doc_id: str) -> None:
    await db.execute("DELETE FROM raw_house_ptr_trade_rows WHERE doc_id = ?", (doc_id,))


async def _upsert_house_ptr_trade_row(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_house_ptr_trade_rows (
            doc_id, table_index, row_index, asset, transaction_type, transaction_date,
            transaction_date_utc, transaction_action, amount, raw_cells_json, raw_json
        ) VALUES (
            :doc_id, :table_index, :row_index, :asset, :transaction_type, :transaction_date,
            :transaction_date_utc, :transaction_action, :amount, :raw_cells_json, :raw_json
        )
        ON CONFLICT (doc_id, table_index, row_index) DO UPDATE SET
            asset = excluded.asset,
            transaction_type = excluded.transaction_type,
            transaction_date = excluded.transaction_date,
            transaction_date_utc = excluded.transaction_date_utc,
            transaction_action = excluded.transaction_action,
            amount = excluded.amount,
            raw_cells_json = excluded.raw_cells_json,
            raw_json = excluded.raw_json
        """,
        row,
    )


async def _upsert_index_row(db: aiosqlite.Connection, item: RawItem) -> None:
    now = _utc_now()
    await db.execute(
        """
        INSERT INTO raw_item_index (
            source_type, source_id, canonical_url, collected_at, first_seen_at, latest_seen_at, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_type, source_id) DO UPDATE SET
            canonical_url = excluded.canonical_url,
            collected_at = excluded.collected_at,
            latest_seen_at = excluded.latest_seen_at,
            content_hash = excluded.content_hash
        """,
        (
            item.source_type,
            item.source_id,
            item.url,
            item.collected_at.isoformat(),
            now,
            now,
            _content_hash(item),
        ),
    )
