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
from stock_sum.storage.models import (
    StoredCollectionRun,
    StoredDownloadedMedia,
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

    async def read_x_posts(self, *, handles: list[str] | None = None, limit: int | None = None) -> list[StoredXPost]:
        """Read stored X posts with media."""

        await self.initialize()
        query = """
            SELECT status_id, handle, author_handle, author_name, posted_at_text, url, text,
                   reply_count, repost_count, like_count, quote_count, view_count,
                   raw_json, collected_at
            FROM raw_x_posts
        """
        params: list[Any] = []
        if handles:
            placeholders = ",".join("?" for _ in handles)
            query += f" WHERE handle IN ({placeholders})"
            params.extend(handles)
        query += " ORDER BY CAST(status_id AS INTEGER) DESC, posted_at_text DESC, collected_at DESC"
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
        if subreddits:
            placeholders = ",".join("?" for _ in subreddits)
            query += f" WHERE subreddit IN ({placeholders})"
            params.extend(subreddits)
        query += " ORDER BY collected_at DESC, created_at_text DESC"
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
    raise ValueError(f"Unsupported mapped table: {item.table}")


async def _upsert_x_post(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_x_posts (
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
        INSERT INTO raw_reddit_comments (
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
