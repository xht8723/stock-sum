"""Runtime data retention tests."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from stock_sum.config.loader import load_config
from stock_sum.core.models import RawItem
from stock_sum.media.downloader import remote_url_hash
from stock_sum.retention import DataRetentionService
from stock_sum.storage.models import StoredDownloadedMedia
from stock_sum.storage.sqlite import SQLiteStorageRepository


async def test_retention_status_counts_managed_paths(tmp_path) -> None:
    config = _retention_config(tmp_path, max_total_bytes=100)
    artifact = tmp_path / "jobs" / "job-1" / "report.txt"
    media = tmp_path / "media" / "x" / "image.jpg"
    artifact.parent.mkdir(parents=True)
    media.parent.mkdir(parents=True)
    artifact.write_bytes(b"a" * 10)
    media.write_bytes(b"b" * 15)

    summary = await DataRetentionService(config).status()

    assert summary.bytes_before == 25
    assert summary.bytes_after == 25
    assert summary.over_limit is False


async def test_retention_prunes_oldest_http_jobs_first(tmp_path) -> None:
    config = _retention_config(tmp_path, max_total_bytes=80)
    _write_job(tmp_path / "jobs" / "old", finished_at="2026-01-01T00:00:00+00:00", size=25)
    _write_job(tmp_path / "jobs" / "new", finished_at="2026-01-02T00:00:00+00:00", size=5)

    summary = await DataRetentionService(config).prune()

    assert summary.http_job_dirs_deleted == 1
    assert not (tmp_path / "jobs" / "old").exists()
    assert (tmp_path / "jobs" / "new").exists()
    assert summary.bytes_after <= config.retention.max_total_bytes


async def test_retention_prunes_downloaded_media_and_database_row(tmp_path) -> None:
    config = _retention_config(tmp_path, max_total_bytes=1)
    repository = SQLiteStorageRepository(tmp_path / "stock_sum.sqlite3")
    await repository.initialize()
    media_path = tmp_path / "media" / "x" / "old.jpg"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"image-bytes")
    remote_url = "https://cdn.example/old.jpg"
    await repository.save_downloaded_media(
        StoredDownloadedMedia(
            remote_url_hash=remote_url_hash(remote_url),
            remote_url=remote_url,
            local_path=media_path.as_posix(),
            content_type="image/jpeg",
            byte_size=11,
            sha256="abc",
            downloaded_at="2026-01-01T00:00:00+00:00",
        )
    )

    summary = await DataRetentionService(config).prune()

    assert summary.media_files_deleted >= 1
    assert not media_path.exists()
    async with aiosqlite.connect(tmp_path / "stock_sum.sqlite3") as db:
        assert await _count_rows(db, "downloaded_media") == 0


async def test_retention_prunes_sqlite_source_rows(tmp_path) -> None:
    config = _retention_config(tmp_path, max_total_bytes=1)
    repository = SQLiteStorageRepository(tmp_path / "stock_sum.sqlite3")
    await repository.save_raw_items(
        [
            RawItem(
                source_id="123",
                source_type="x_user_timeline",
                url="https://x.com/example/status/123",
                text="hello",
                metadata={"entity_type": "x_post", "handle": "example", "raw": {"id": "123"}},
            ),
            RawItem(
                source_id="abc",
                source_type="reddit_subreddit",
                url="https://www.reddit.com/r/wallstreetbets/comments/abc/post/",
                text="body",
                metadata={"entity_type": "reddit_post", "subreddit": "wallstreetbets", "title": "Post", "raw": {"id": "abc"}},
            ),
        ]
    )

    summary = await DataRetentionService(config).prune()

    assert summary.sqlite_rows_deleted > 0
    assert summary.sqlite_vacuumed is True
    async with aiosqlite.connect(tmp_path / "stock_sum.sqlite3") as db:
        assert await _count_rows(db, "raw_x_posts") == 0
        assert await _count_rows(db, "raw_reddit_posts") == 0
        assert await _count_rows(db, "raw_item_index") == 0


async def test_retention_dry_run_does_not_delete_files(tmp_path) -> None:
    config = _retention_config(tmp_path, max_total_bytes=1)
    _write_job(tmp_path / "jobs" / "old", finished_at="2026-01-01T00:00:00+00:00", size=25)

    summary = await DataRetentionService(config).prune(dry_run=True)

    assert summary.dry_run is True
    assert summary.http_job_dirs_deleted == 1
    assert (tmp_path / "jobs" / "old").exists()


async def test_retention_disabled_does_not_delete(tmp_path) -> None:
    config = _retention_config(tmp_path, max_total_bytes=1, enabled=False)
    _write_job(tmp_path / "jobs" / "old", finished_at="2026-01-01T00:00:00+00:00", size=25)

    summary = await DataRetentionService(config).prune()

    assert summary.enabled is False
    assert summary.http_job_dirs_deleted == 0
    assert (tmp_path / "jobs" / "old").exists()


def _retention_config(tmp_path: Path, *, max_total_bytes: int, enabled: bool = True):
    config = load_config(Path("stock_sum/config/example.toml"))
    return config.model_copy(
        update={
            "server": config.server.model_copy(update={"artifact_dir": str(tmp_path / "jobs")}),
            "media": config.media.model_copy(update={"root_dir": str(tmp_path / "media")}),
            "storage": config.storage.model_copy(update={"sqlite_path": str(tmp_path / "stock_sum.sqlite3")}),
            "retention": config.retention.model_copy(update={"enabled": enabled, "max_total_bytes": max_total_bytes}),
        }
    )


def _write_job(path: Path, *, finished_at: str, size: int) -> None:
    path.mkdir(parents=True)
    (path / "status.json").write_text(
        f'{{"job_id":"{path.name}","finished_at":"{finished_at}"}}',
        encoding="utf-8",
    )
    (path / "artifact.txt").write_bytes(b"x" * size)


async def _count_rows(db: aiosqlite.Connection, table: str) -> int:
    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row[0]
