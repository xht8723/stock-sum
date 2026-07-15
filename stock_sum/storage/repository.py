"""Storage repository protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from stock_sum.core.models import ProviderApiResponse, RawItem, RawItemSaveResult
from stock_sum.storage.models import (
    StoredAdanosResponseCacheEntry,
    StoredAdanosTrendingSector,
    StoredAdanosTrendingStock,
    StoredCollectionRun,
    StoredDownloadedMedia,
    StoredHousePtrFiling,
    StoredHousePtrTradeRow,
    StoredRedditPost,
    StoredSec13FHolding,
    StoredSocialStatisticPoint,
    StoredStatisticFuzzyMatch,
    StoredTradingStatisticPoint,
    StoredXPost,
)


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

    async def read_llm_analysis_report(self, *, analysis_run_id: str | None = None) -> dict:
        """Read stored analysis rows as a renderer-ready summary object."""

    async def read_llm_social_posts_by_ticker(
        self,
        *,
        ticker: str,
        analysis_run_id: str | None = None,
    ) -> list[dict]:
        """Read analyzed X/Reddit post rows linked to a ticker."""

    async def read_social_statistic_points(
        self,
        *,
        ticker: str | None = None,
        fuzzy_tag: str | None = None,
        source: str | None = None,
        sentiment: str | None = None,
        posted_start: datetime | None = None,
        posted_end: datetime | None = None,
        analysis_run_id: str | None = None,
    ) -> list[StoredSocialStatisticPoint]:
        """Read analyzed social post rows for statistic charting."""

    async def search_social_statistic_tags(
        self,
        *,
        query: str,
        limit: int = 5,
    ) -> list[StoredStatisticFuzzyMatch]:
        """Return tag candidates for social statistic fuzzy search."""

    async def list_collection_runs(self, *, limit: int | None = None) -> list[StoredCollectionRun]:
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
        """Return terminally processed House PTR DocIDs safe to skip."""

    async def read_house_ptr_filings(
        self,
        *,
        name_contains: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        filing_start: datetime | None = None,
        filing_end: datetime | None = None,
        collected_start: datetime | None = None,
        collected_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        order_by_filing_date: bool = False,
        order_by_collected_at: bool = False,
    ) -> list[StoredHousePtrFiling]:
        """Read filing metadata, including filings with no transaction rows."""

    async def read_house_ptr_trades(
        self,
        *,
        name_contains: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        filing_start: datetime | None = None,
        filing_end: datetime | None = None,
        collected_start: datetime | None = None,
        collected_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        order_by_filing_date: bool = False,
    ) -> list[StoredHousePtrTradeRow]:
        """Read House PTR trade rows for deterministic report rendering."""

    async def read_trading_statistic_points(
        self,
        *,
        name_contains: str | None = None,
        asset_name: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        action: str | None = None,
    ) -> list[StoredTradingStatisticPoint]:
        """Read House PTR trade rows for statistic charting."""

    async def search_trading_statistic_assets(
        self,
        *,
        query: str,
        limit: int = 5,
    ) -> list[StoredStatisticFuzzyMatch]:
        """Return asset candidates for trading statistic fuzzy search."""

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

    async def save_adanos_trendings(
        self,
        *,
        job_id: str,
        from_date: str,
        to_date: str,
        responses: list,
        cache_keys_by_endpoint: dict[tuple[str, str], str] | None = None,
    ) -> None:
        """Persist raw and normalized Adanos trendings responses."""

    async def read_adanos_response_cache_entries(
        self,
        *,
        cache_keys: list[str],
        fresh_after: str,
    ) -> list[StoredAdanosResponseCacheEntry]:
        """Read fresh Adanos endpoint responses and prune expired entries."""

    async def delete_adanos_response_cache_entries(self, *, cache_keys: list[str]) -> None:
        """Delete unusable Adanos endpoint cache entries."""

    async def read_adanos_trending_stocks(
        self,
        *,
        job_id: str,
        limit: int | None = None,
    ) -> list[StoredAdanosTrendingStock]:
        """Read stored Adanos trending stock rows for one job."""

    async def read_adanos_trending_stocks_for_sources(
        self,
        *,
        sources: list[tuple[str, str]],
    ) -> list[StoredAdanosTrendingStock]:
        """Read stock rows from exact platform/source-job snapshots."""

    async def read_latest_prior_adanos_trending_stocks(
        self,
        *,
        exclude_job_id: str | None,
        tickers: list[str],
        since_fetched_at: str,
        exclude_sources: list[tuple[str, str]] | None = None,
    ) -> list[StoredAdanosTrendingStock]:
        """Read latest historical Adanos stock rows for each platform/ticker."""

    async def has_prior_adanos_trending_stock_history(
        self,
        *,
        exclude_job_id: str | None,
        since_fetched_at: str,
        exclude_sources: list[tuple[str, str]] | None = None,
    ) -> bool:
        """Return whether any prior Adanos stock history exists in the comparison window."""

    async def read_adanos_trending_sectors(
        self,
        *,
        job_id: str,
        limit: int | None = None,
    ) -> list[StoredAdanosTrendingSector]:
        """Read stored Adanos trending sector rows for one job."""

    async def read_adanos_trending_sectors_for_sources(
        self,
        *,
        sources: list[tuple[str, str]],
    ) -> list[StoredAdanosTrendingSector]:
        """Read sector rows from exact platform/source-job snapshots."""

    async def get_downloaded_media(self, remote_url: str) -> StoredDownloadedMedia | None:
        """Return downloaded media metadata by remote URL."""

    async def save_downloaded_media(self, media: StoredDownloadedMedia) -> None:
        """Persist downloaded media metadata."""
