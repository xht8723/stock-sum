"""SQLite storage repository implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from typing import Any

import aiosqlite

from stock_sum.core.models import RawItem, RawItemSaveResult
from stock_sum.storage.mappers import MappedRawItem, map_raw_item


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

CREATE TABLE IF NOT EXISTS raw_scrape_creators_x_posts (
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
    PRIMARY KEY (handle, status_id)
);

CREATE TABLE IF NOT EXISTS raw_scrape_creators_x_post_media (
    status_id TEXT NOT NULL,
    media_key TEXT,
    media_type TEXT,
    media_url TEXT NOT NULL,
    alt_text TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (status_id, media_url)
);

CREATE TABLE IF NOT EXISTS raw_scrape_creators_reddit_posts (
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
    PRIMARY KEY (subreddit, post_id)
);

CREATE TABLE IF NOT EXISTS raw_scrape_creators_reddit_comments (
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
    PRIMARY KEY (post_id, comment_id)
);

CREATE TABLE IF NOT EXISTS raw_scrape_creators_reddit_post_media (
    post_id TEXT NOT NULL,
    media_type TEXT,
    media_url TEXT NOT NULL,
    source_field TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (post_id, media_url)
);
"""


class SQLiteStorageRepository:
    """SQLite repository for collection runs and source-specific raw items."""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)

    async def initialize(self) -> None:
        """Create database tables if they do not exist."""

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.executescript(SCHEMA_SQL)
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

    async def save_summaries(self, summaries: list[Any]) -> None:
        """Persist generated summaries."""

        raise NotImplementedError("Summary storage is not implemented yet.")

    async def save_report(self, report: Any) -> None:
        """Persist a rendered report."""

        raise NotImplementedError("Report storage is not implemented yet.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


async def _mapped_row_exists(db: aiosqlite.Connection, item: MappedRawItem) -> bool:
    if item.table == "raw_scrape_creators_x_posts":
        query = "SELECT 1 FROM raw_scrape_creators_x_posts WHERE handle = ? AND status_id = ?"
    elif item.table == "raw_scrape_creators_reddit_posts":
        query = "SELECT 1 FROM raw_scrape_creators_reddit_posts WHERE subreddit = ? AND post_id = ?"
    elif item.table == "raw_scrape_creators_reddit_comments":
        query = "SELECT 1 FROM raw_scrape_creators_reddit_comments WHERE post_id = ? AND comment_id = ?"
    else:
        raise ValueError(f"Unsupported mapped table: {item.table}")
    cursor = await db.execute(query, item.key)
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


async def _upsert_mapped_item(db: aiosqlite.Connection, item: MappedRawItem) -> None:
    if item.table == "raw_scrape_creators_x_posts":
        await _upsert_x_post(db, item.row)
        for media_row in item.media_rows:
            await _upsert_x_media(db, media_row)
        return
    if item.table == "raw_scrape_creators_reddit_posts":
        await _upsert_reddit_post(db, item.row)
        for media_row in item.media_rows:
            await _upsert_reddit_media(db, media_row)
        return
    if item.table == "raw_scrape_creators_reddit_comments":
        await _upsert_reddit_comment(db, item.row)
        return
    raise ValueError(f"Unsupported mapped table: {item.table}")


async def _upsert_x_post(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_scrape_creators_x_posts (
            status_id, handle, author_handle, author_name, posted_at_text, url, text,
            reply_count, repost_count, like_count, quote_count, view_count, raw_json, collected_at
        ) VALUES (
            :status_id, :handle, :author_handle, :author_name, :posted_at_text, :url, :text,
            :reply_count, :repost_count, :like_count, :quote_count, :view_count, :raw_json, :collected_at
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
            collected_at = excluded.collected_at
        """,
        row,
    )


async def _upsert_x_media(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_scrape_creators_x_post_media (
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
        INSERT INTO raw_scrape_creators_reddit_posts (
            post_id, subreddit, fullname, title, author, url, permalink, selftext,
            score, ups, upvote_ratio, num_comments, thumbnail_url, created_at_text,
            raw_json, collected_at
        ) VALUES (
            :post_id, :subreddit, :fullname, :title, :author, :url, :permalink, :selftext,
            :score, :ups, :upvote_ratio, :num_comments, :thumbnail_url, :created_at_text,
            :raw_json, :collected_at
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
            collected_at = excluded.collected_at
        """,
        row,
    )


async def _upsert_reddit_comment(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_scrape_creators_reddit_comments (
            comment_id, post_id, parent_id, author, body, score, ups, url,
            created_at_text, depth, raw_json, collected_at
        ) VALUES (
            :comment_id, :post_id, :parent_id, :author, :body, :score, :ups, :url,
            :created_at_text, :depth, :raw_json, :collected_at
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
            collected_at = excluded.collected_at
        """,
        row,
    )


async def _upsert_reddit_media(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_scrape_creators_reddit_post_media (
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
