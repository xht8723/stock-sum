"""Stored source-specific row models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StoredDownloadedMedia:
    """Metadata for a downloaded media asset."""

    remote_url_hash: str
    remote_url: str
    local_path: str
    content_type: str
    byte_size: int
    sha256: str
    downloaded_at: str


@dataclass(frozen=True)
class StoredMediaAsset:
    """Stored remote media row with optional local download metadata."""

    remote_url: str
    media_type: str | None
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    local_path: str | None = None
    content_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None

    @property
    def width(self) -> int | None:
        value = self.raw_metadata.get("width")
        return value if isinstance(value, int) else None

    @property
    def height(self) -> int | None:
        value = self.raw_metadata.get("height")
        return value if isinstance(value, int) else None


@dataclass(frozen=True)
class StoredCollectionRun:
    """Stored collection run metadata."""

    run_id: str
    collector_id: str
    source_type: str | None
    status: str
    started_at: str
    finished_at: str | None
    collected_count: int
    inserted_count: int
    updated_count: int
    error_text: str | None


@dataclass(frozen=True)
class StoredXPost:
    """Stored X post with media rows."""

    status_id: str
    handle: str
    author_handle: str | None
    author_name: str | None
    posted_at_text: str | None
    url: str | None
    text: str
    reply_count: int | None
    repost_count: int | None
    like_count: int | None
    quote_count: int | None
    view_count: int | None
    raw_metadata: dict[str, Any]
    collected_at: str
    media: list[StoredMediaAsset] = field(default_factory=list)


@dataclass(frozen=True)
class StoredRedditComment:
    """Stored Reddit comment linked to a post."""

    comment_id: str
    post_id: str
    parent_id: str | None
    author: str | None
    body: str
    score: int | None
    ups: int | None
    url: str | None
    created_at_text: str | None
    depth: int | None
    raw_metadata: dict[str, Any]
    collected_at: str


@dataclass(frozen=True)
class StoredRedditPost:
    """Stored Reddit post with media and linked comments."""

    post_id: str
    subreddit: str
    fullname: str | None
    title: str
    author: str | None
    url: str | None
    permalink: str | None
    selftext: str
    score: int | None
    ups: int | None
    upvote_ratio: float | None
    num_comments: int | None
    thumbnail_url: str | None
    created_at_text: str | None
    raw_metadata: dict[str, Any]
    collected_at: str
    media: list[StoredMediaAsset] = field(default_factory=list)
    comments: list[StoredRedditComment] = field(default_factory=list)


@dataclass(frozen=True)
class StoredHousePtrFiling:
    """Stored House PTR filing metadata independent of transaction rows."""

    doc_id: str
    year: int
    name: str | None
    status: str | None
    state: str | None
    filing_date: str | None
    filing_date_utc: str | None
    pdf_url: str | None
    extraction_status: str
    extraction_error: str | None
    extraction_warnings: list[dict[str, Any]]
    extraction_metadata: dict[str, Any]
    transaction_count: int
    collected_at: str


@dataclass(frozen=True)
class StoredHousePtrTradeRow:
    """Stored House PTR trade row joined with filing metadata."""

    doc_id: str
    year: int
    name: str | None
    status: str | None
    state: str | None
    filing_date: str | None
    filing_date_utc: str | None
    pdf_url: str | None
    table_index: int
    row_index: int
    asset: str | None
    asset_type_code: str | None
    asset_type_label: str | None
    stock_ticker: str | None
    transaction_type: str | None
    transaction_date: str | None
    transaction_date_utc: str | None
    transaction_action: str | None
    amount: str | None
    raw_cells: list[str]
    raw_metadata: dict[str, Any]
    collected_at: str


@dataclass(frozen=True)
class StoredSec13FHolding:
    """Stored SEC 13F holding row joined with filer metadata."""

    dataset_id: str
    dataset_label: str | None
    accession_number: str
    cik: str | None
    manager_name: str | None
    filing_date: str | None
    filing_date_utc: str | None
    period_of_report: str | None
    period_of_report_utc: str | None
    info_table_sk: str
    issuer: str | None
    title_of_class: str | None
    cusip: str | None
    figi: str | None
    value: int | None
    ssh_prn_amt: int | None
    ssh_prn_type: str | None
    put_call: str | None
    investment_discretion: str | None
    other_manager: str | None
    voting_auth_sole: int | None
    voting_auth_shared: int | None
    voting_auth_none: int | None
    filing_url: str | None
    raw_metadata: dict[str, Any]


@dataclass(frozen=True)
class StoredAdanosResponseCacheEntry:
    """One reusable successful Adanos endpoint response."""

    cache_key: str
    source_job_id: str
    platform: str
    category: str
    endpoint: str
    request_args: dict[str, Any]
    raw_response_text: str
    row_count: int
    fetched_at: str


@dataclass(frozen=True)
class StoredAdanosTrendingStock:
    """Stored Adanos trending stock row."""

    job_id: str
    platform: str
    rank: int
    window_from: str
    window_to: str
    ticker: str
    company_name: str | None
    trend: str | None
    mentions: int | None
    bullish_pct: int | None
    bearish_pct: int | None
    sentiment_score: float | None
    buzz_score: float | None
    trend_history: list[Any]
    raw_metadata: dict[str, Any]
    fetched_at: str


@dataclass(frozen=True)
class StoredAdanosTrendingSector:
    """Stored Adanos trending sector row."""

    job_id: str
    platform: str
    rank: int
    window_from: str
    window_to: str
    sector: str
    top_tickers: list[str]
    trend: str | None
    mentions: int | None
    bullish_pct: int | None
    bearish_pct: int | None
    sentiment_score: float | None
    buzz_score: float | None
    trend_history: list[Any]
    raw_metadata: dict[str, Any]
    fetched_at: str


@dataclass(frozen=True)
class StoredSocialStatisticPoint:
    """Analyzed social post point for statistic charting."""

    source: str
    ticker: str | None
    source_id: str
    source_ref: str
    label: str | None
    sentiment: str
    importance: str
    posted_at: str | None
    analyzed_at: str | None


@dataclass(frozen=True)
class StoredTradingStatisticPoint:
    """House PTR transaction point for statistic charting."""

    doc_id: str
    name: str | None
    state: str | None
    asset: str | None
    asset_type_code: str | None
    stock_ticker: str | None
    transaction_action: str | None
    transaction_date: str | None
    transaction_date_utc: str | None
    amount: str | None


@dataclass(frozen=True)
class StoredStatisticFuzzyMatch:
    """Candidate returned by statistic fuzzy search."""

    mode: str
    label: str
    source: str
    match_value: str
    row_count: int
    x_count: int = 0
    reddit_count: int = 0
    ticker: str | None = None
    asset_name: str | None = None
    asset_type_code: str | None = None
    statistic_filters: dict[str, Any] = field(default_factory=dict)
