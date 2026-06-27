"""SQLite storage repository implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from typing import Any

import aiosqlite

from stock_sum.core.models import RawItem, RawItemSaveResult
from stock_sum.storage.mappers import SourceRow, map_raw_item


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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status_id TEXT NOT NULL,
    handle TEXT NOT NULL,
    author TEXT,
    posted_at_text TEXT,
    url TEXT,
    text TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (handle, status_id)
);

CREATE TABLE IF NOT EXISTS raw_reddit_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subreddit TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT,
    author TEXT,
    url TEXT,
    text TEXT,
    score INTEGER,
    comment_count INTEGER,
    metadata_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (subreddit, post_id)
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

        source_types = {item.source_type for item in items}
        if len(source_types) != 1:
            raise ValueError("save_raw_items expects one source type per batch.")

        inserted = 0
        updated = 0
        async with aiosqlite.connect(self.sqlite_path) as db:
            for item in items:
                source_row = map_raw_item(item)
                exists = await _source_row_exists(db, source_row)
                await _upsert_source_row(db, source_row)
                await _upsert_index_row(db, item)
                if exists:
                    updated += 1
                else:
                    inserted += 1
            await db.commit()

        return RawItemSaveResult(
            source_type=items[0].source_type,
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


async def _source_row_exists(db: aiosqlite.Connection, source_row: SourceRow) -> bool:
    where_clause = " AND ".join(f"{column} = ?" for column in source_row.unique_columns)
    values = tuple(source_row.values[column] for column in source_row.unique_columns)
    cursor = await db.execute(f"SELECT 1 FROM {source_row.table_name} WHERE {where_clause} LIMIT 1", values)
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


async def _upsert_source_row(db: aiosqlite.Connection, source_row: SourceRow) -> None:
    columns = tuple(source_row.values.keys())
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    conflict_sql = ", ".join(source_row.unique_columns)
    update_columns = [column for column in columns if column not in source_row.unique_columns]
    update_sql = ", ".join([f"{column} = excluded.{column}" for column in update_columns] + ["updated_at = CURRENT_TIMESTAMP"])
    await db.execute(
        f"""
        INSERT INTO {source_row.table_name} ({column_sql})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}
        """,
        tuple(source_row.values[column] for column in columns),
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
