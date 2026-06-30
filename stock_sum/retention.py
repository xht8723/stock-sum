"""Bounded retention for generated artifacts and downloaded media."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import shutil

import aiosqlite

from stock_sum.config.models import AppConfig


@dataclass
class RetentionSummary:
    """Result of one retention status or prune pass."""

    enabled: bool
    dry_run: bool
    max_total_bytes: int
    bytes_before: int
    bytes_after: int
    bytes_deleted: int = 0
    http_job_dirs_deleted: int = 0
    media_files_deleted: int = 0
    sqlite_rows_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def over_limit(self) -> bool:
        return self.bytes_after > self.max_total_bytes

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["over_limit"] = self.over_limit
        return data


class DataRetentionService:
    """Measures and prunes stock-sum managed runtime data."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.artifact_dir = Path(config.server.artifact_dir)
        self.media_root = Path(config.media.root_dir)
        self.sqlite_path = Path(config.storage.sqlite_path)

    async def status(self) -> RetentionSummary:
        """Return current managed storage usage without deleting data."""

        size = self.total_size_bytes()
        return RetentionSummary(
            enabled=self.config.retention.enabled,
            dry_run=True,
            max_total_bytes=self.config.retention.max_total_bytes,
            bytes_before=size,
            bytes_after=size,
        )

    async def prune(
        self,
        *,
        dry_run: bool = False,
        protected_paths: list[Path] | None = None,
    ) -> RetentionSummary:
        """Prune oldest managed data until total usage is under the configured cap."""

        before = self.total_size_bytes()
        summary = RetentionSummary(
            enabled=self.config.retention.enabled,
            dry_run=dry_run,
            max_total_bytes=self.config.retention.max_total_bytes,
            bytes_before=before,
            bytes_after=before,
        )
        if not self.config.retention.enabled:
            return summary
        if before <= self.config.retention.max_total_bytes:
            return summary

        protected = {_resolve(path) for path in protected_paths or []}
        self._prune_http_jobs(summary, protected)
        if self._under_limit(summary):
            return summary

        await self._prune_downloaded_media(summary, protected)
        if self._under_limit(summary):
            return summary

        summary.bytes_after = self.total_size_bytes()
        summary.bytes_deleted = max(0, summary.bytes_before - summary.bytes_after)
        return summary

    def total_size_bytes(self) -> int:
        """Return total bytes for bounded artifact and media files.

        SQLite source history is intentionally excluded from this cap.
        """

        seen: set[Path] = set()
        total = 0
        for path in (self.artifact_dir, self.media_root):
            total += _path_size(path, seen)
        return total

    def _under_limit(self, summary: RetentionSummary) -> bool:
        summary.bytes_after = self.total_size_bytes()
        summary.bytes_deleted = max(0, summary.bytes_before - summary.bytes_after)
        return summary.bytes_after <= summary.max_total_bytes

    def _prune_http_jobs(self, summary: RetentionSummary, protected: set[Path]) -> None:
        for job_dir in self._http_job_candidates():
            if self._under_limit(summary):
                return
            if _is_protected(job_dir, protected):
                continue
            size = _path_size(job_dir, set())
            if not summary.dry_run:
                try:
                    shutil.rmtree(job_dir)
                except OSError as exc:
                    summary.errors.append(f"Failed to delete HTTP job {job_dir}: {exc}")
                    continue
            summary.http_job_dirs_deleted += 1
            summary.bytes_deleted += size

    async def _prune_downloaded_media(self, summary: RetentionSummary, protected: set[Path]) -> None:
        handled_media_paths: set[Path] = set()
        if self.sqlite_path.exists():
            async with aiosqlite.connect(self.sqlite_path) as db:
                for row in await _downloaded_media_rows(db):
                    if self._under_limit(summary):
                        return
                    local_path = Path(row["local_path"])
                    handled_media_paths.add(_resolve(local_path))
                    if _is_protected(local_path, protected):
                        continue
                    size = _path_size(local_path, set())
                    if not summary.dry_run:
                        try:
                            local_path.unlink(missing_ok=True)
                            await db.execute(
                                "DELETE FROM downloaded_media WHERE remote_url_hash = ?",
                                (row["remote_url_hash"],),
                            )
                            await db.commit()
                        except OSError as exc:
                            summary.errors.append(f"Failed to delete media {local_path}: {exc}")
                            continue
                    summary.media_files_deleted += 1
                    summary.sqlite_rows_deleted += 1
                    summary.bytes_deleted += size

        for media_file in self._orphan_media_candidates():
            if self._under_limit(summary):
                return
            if _resolve(media_file) in handled_media_paths:
                continue
            if _is_protected(media_file, protected):
                continue
            size = _path_size(media_file, set())
            if not summary.dry_run:
                try:
                    media_file.unlink(missing_ok=True)
                    _remove_empty_parents(media_file.parent, stop_at=self.media_root)
                except OSError as exc:
                    summary.errors.append(f"Failed to delete media {media_file}: {exc}")
                    continue
            summary.media_files_deleted += 1
            summary.bytes_deleted += size

    def _http_job_candidates(self) -> list[Path]:
        if not self.artifact_dir.exists():
            return []
        candidates = [path for path in self.artifact_dir.iterdir() if path.is_dir()]
        return sorted(candidates, key=_http_job_sort_key)

    def _orphan_media_candidates(self) -> list[Path]:
        if not self.media_root.exists():
            return []
        return sorted((path for path in self.media_root.rglob("*") if path.is_file()), key=lambda path: _mtime(path))

def _path_size(path: Path, seen: set[Path]) -> int:
    if not path.exists():
        return 0
    resolved = _resolve(path)
    if resolved in seen:
        return 0
    seen.add(resolved)
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += _path_size(child, seen)
    return total


def _http_job_sort_key(job_dir: Path) -> tuple[float, str]:
    status_path = job_dir / "status.json"
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            timestamp = _parse_time(data.get("finished_at") or data.get("updated_at") or data.get("created_at"))
            if timestamp is not None:
                return (timestamp.timestamp(), job_dir.name)
        except (OSError, TypeError, ValueError):
            pass
    return (_mtime(job_dir), job_dir.name)


async def _downloaded_media_rows(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    try:
        cursor = await db.execute(
            """
            SELECT remote_url_hash, local_path, downloaded_at
            FROM downloaded_media
            ORDER BY downloaded_at ASC
            """
        )
    except aiosqlite.OperationalError:
        return []
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        {"remote_url_hash": row[0], "local_path": row[1], "downloaded_at": row[2]}
        for row in rows
    ]


def _is_protected(path: Path, protected: set[Path]) -> bool:
    resolved = _resolve(path)
    return any(resolved == item or item in resolved.parents for item in protected)


def _resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _remove_empty_parents(path: Path, *, stop_at: Path) -> None:
    stop = _resolve(stop_at)
    current = _resolve(path)
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
