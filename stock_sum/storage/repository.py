"""Storage repository protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from stock_sum.core.models import ProviderApiResponse, RawItem, RawItemSaveResult
from stock_sum.storage.models import StoredCollectionRun, StoredDownloadedMedia, StoredHousePtrTradeRow, StoredRedditPost, StoredSec13FHolding, StoredXPost


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

    async def save_provider_api_responses(
        self,
        *,
        collection_run_id: str,
        collector_id: str,
        responses: list[ProviderApiResponse],
    ) -> None:
        """Persist raw provider API/tool responses for one collection run."""

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
        """Record the start of a chunked LLM analysis run."""

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
        """Record the completion of a chunked LLM analysis run."""

    async def save_llm_x_post_analyses(self, rows: list[dict]) -> None:
        """Persist X post analysis rows."""

    async def save_llm_reddit_post_analyses(self, rows: list[dict]) -> None:
        """Persist Reddit post analysis rows."""

    async def save_llm_reddit_comment_analyses(self, rows: list[dict]) -> None:
        """Persist Reddit comment analysis rows."""

    async def read_llm_analysis_report(self, *, profile: str, analysis_run_id: str | None = None) -> dict:
        """Read stored analysis rows as a renderer-ready summary object."""

    async def list_collection_runs(self, *, profile: str | None = None, limit: int | None = None) -> list[StoredCollectionRun]:
        """Return stored collection runs."""

    async def read_x_posts(
        self,
        *,
        handles: list[str] | None = None,
        since_posted_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredXPost]:
        """Read stored X posts with media."""

    async def read_reddit_posts(
        self,
        *,
        subreddits: list[str] | None = None,
        since_posted_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredRedditPost]:
        """Read stored Reddit posts with media and comments."""

    async def existing_house_ptr_doc_ids(self, *, year: int | None = None) -> set[str]:
        """Return successfully extracted House PTR DocIDs safe to skip."""

    async def read_house_ptr_trades(
        self,
        *,
        name_contains: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
    ) -> list[StoredHousePtrTradeRow]:
        """Read House PTR trade rows for deterministic report rendering."""

    async def read_sec_13f_holdings(
        self,
        *,
        manager: str | None = None,
        cik: str | None = None,
        accession_number: str | None = None,
        issuer: str | None = None,
        cusip: str | None = None,
        figi: str | None = None,
        put_call: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        filing_start: datetime | None = None,
        filing_end: datetime | None = None,
        min_value: int | None = None,
        min_shares: int | None = None,
        limit: int | None = 20,
    ) -> list[StoredSec13FHolding]:
        """Read SEC 13F holdings for deterministic report rendering."""

    async def get_downloaded_media(self, remote_url: str) -> StoredDownloadedMedia | None:
        """Return downloaded media metadata by remote URL."""

    async def save_downloaded_media(self, media: StoredDownloadedMedia) -> None:
        """Persist downloaded media metadata."""
