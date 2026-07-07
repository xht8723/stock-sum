"""SQLite helpers for downloaded media metadata."""

from __future__ import annotations

import hashlib

import aiosqlite

from stock_sum.storage.models import StoredDownloadedMedia


def remote_url_hash(remote_url: str) -> str:
    return hashlib.sha256(remote_url.encode("utf-8")).hexdigest()


async def get_downloaded_media(db: aiosqlite.Connection, remote_url: str) -> StoredDownloadedMedia | None:
    cursor = await db.execute(
        """
        SELECT remote_url_hash, remote_url, local_path, content_type, byte_size, sha256, downloaded_at
        FROM downloaded_media
        WHERE remote_url_hash = ?
        """,
        (remote_url_hash(remote_url),),
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


async def upsert_downloaded_media(db: aiosqlite.Connection, media: StoredDownloadedMedia) -> None:
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


_remote_url_hash = remote_url_hash
_get_downloaded_media = get_downloaded_media
_upsert_downloaded_media = upsert_downloaded_media
