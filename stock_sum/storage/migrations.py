"""Versioned SQLite schema migrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import aiosqlite


Migration = Callable[[aiosqlite.Connection], Awaitable[None]]


async def apply_migrations(db: aiosqlite.Connection) -> None:
    """Apply pending migrations one at a time inside write transactions."""

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    await db.commit()

    for version, name, migration in MIGRATIONS:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,))
            try:
                applied = await cursor.fetchone()
            finally:
                await cursor.close()
            if applied is None:
                await migration(db)
                await db.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, datetime.now(timezone.utc).isoformat()),
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _house_ptr_extraction_outcomes(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "raw_house_ptr_filings")
    if "extraction_warnings_json" not in columns:
        await db.execute(
            "ALTER TABLE raw_house_ptr_filings "
            "ADD COLUMN extraction_warnings_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "extraction_metadata_json" not in columns:
        await db.execute(
            "ALTER TABLE raw_house_ptr_filings "
            "ADD COLUMN extraction_metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_house_ptr_filings_collected_at "
        "ON raw_house_ptr_filings (collected_at DESC)"
    )
    await db.execute(
        """
        UPDATE raw_house_ptr_filings
        SET extraction_status = 'unparsed',
            extraction_warnings_json = '[{"code":"house_ptr_unparsed","message":"No House PTR transactions could be extracted from this filing."}]',
            extraction_metadata_json = '{"classification":"legacy_backfill"}'
        WHERE extraction_status = 'succeeded'
          AND NOT EXISTS (
              SELECT 1
              FROM raw_house_ptr_trade_rows trade
              WHERE trade.doc_id = raw_house_ptr_filings.doc_id
          )
        """
    )


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    try:
        return {str(row[1]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()


MIGRATIONS: tuple[tuple[int, str, Migration], ...] = (
    (1, "house_ptr_extraction_outcomes", _house_ptr_extraction_outcomes),
)
