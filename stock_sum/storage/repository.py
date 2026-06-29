"""Storage repository protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_sum.core.models import RawItem, RawItemSaveResult, Report, Summary
from stock_sum.storage.models import StoredCollectionRun, StoredDownloadedMedia, StoredRedditPost, StoredXPost


@runtime_checkable
class StorageRepository(Protocol):
    """Persists collected items, summaries, reports, deliveries, and run metadata."""

    async def initialize(self) -> None:
        """Prepare storage for use."""

    async def start_collection_run(
        self,
        *,
        run_id: str,
        collector_id: str,
        profile: str | None = None,
        source_type: str | None = None,
    ) -> None:
        """Record a collection run start."""

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
        """Record a collection run finish."""

    async def save_raw_items(self, items: list[RawItem]) -> RawItemSaveResult:
        """Persist raw collected items."""

    async def list_collection_runs(self, *, profile: str | None = None, limit: int | None = None) -> list[StoredCollectionRun]:
        """Return stored collection runs."""

    async def read_x_posts(self, *, handles: list[str] | None = None, limit: int | None = None) -> list[StoredXPost]:
        """Read stored X posts with media."""

    async def read_reddit_posts(
        self,
        *,
        subreddits: list[str] | None = None,
        limit: int | None = None,
    ) -> list[StoredRedditPost]:
        """Read stored Reddit posts with media and comments."""

    async def get_downloaded_media(self, remote_url: str) -> StoredDownloadedMedia | None:
        """Return downloaded media metadata by remote URL."""

    async def save_downloaded_media(self, media: StoredDownloadedMedia) -> None:
        """Persist downloaded media metadata."""

    async def save_summaries(self, summaries: list[Summary]) -> None:
        """Persist generated summaries."""

    async def save_report(self, report: Report) -> None:
        """Persist a rendered report."""
