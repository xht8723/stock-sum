"""HTTP job option and status models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
import asyncio


JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobKind = Literal["social_report", "trading_report", "13f_report", "trendings_report", "statistic", "collect"]
ReportMode = Literal["html", "markdown", "discord", "text", "json"]
StatisticMode = Literal["social", "trading"]
StatisticBucket = Literal["auto", "day", "week", "month"]
WorkerOperation = Literal[
    "http_social_report",
    "http_trading_report",
    "http_13f_report",
    "http_trendings_report",
    "http_statistic",
    "http_collect",
    "http_render_cached_artifact_job",
    "http_render_coalesced_artifact_job",
]


@dataclass(frozen=True)
class SocialReportJobOptions:
    """Options for a full report job."""

    mode: ReportMode = "html"
    detail: Literal["minimum", "medium", "full"] = "minimum"
    x_method: Literal["xpoz", "rss"] = "xpoz"
    reddit_method: Literal["xpoz", "rss"] = "xpoz"
    download_images: bool = False
    instructions: str | None = None
    title: str = "Market Social Digest"
    max_images_per_post: int = 3
    max_images_total: int = 20


@dataclass(frozen=True)
class TradingReportJobOptions:
    """Options for a House PTR trading disclosure report job."""

    mode: ReportMode = "html"
    name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    filing_start_date: str | None = None
    filing_end_date: str | None = None
    filing_days: int | None = None
    asset_type: str | None = None
    ticker: str | None = None
    limit: int = 100
    title: str = "Official Trading Disclosures"
    force_refresh: bool = False

    def __post_init__(self) -> None:
        if self.limit is None:
            object.__setattr__(self, "limit", 100)


@dataclass(frozen=True)
class Sec13FReportJobOptions:
    """Options for an SEC 13F holdings report job."""

    mode: ReportMode = "html"
    manager: str | None = None
    cik: str | None = None
    accession_number: str | None = None
    issuer: str | None = None
    cusip: str | None = None
    figi: str | None = None
    put_call: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    filing_start: str | None = None
    filing_end: str | None = None
    min_value: int | None = None
    min_shares: int | None = None
    limit: int = 20
    title: str = "SEC 13F Holdings"
    force_refresh: bool = False

    def __post_init__(self) -> None:
        if self.limit is None:
            object.__setattr__(self, "limit", 20)


@dataclass(frozen=True)
class TrendingsReportJobOptions:
    """Options for an Adanos trendings report job."""

    mode: ReportMode = "html"
    from_date: str | None = None
    to_date: str | None = None
    limit: int = 5
    days: int = 1
    comparison_days: int = 7
    mentions_change_pct: float = 30.0
    sentiment_change_pct: float = 30.0
    minimum_mentions: int = 50
    title: str = "Trending Market Sentiment"

    def __post_init__(self) -> None:
        if self.limit is None:
            object.__setattr__(self, "limit", 5)
        if self.days is None:
            object.__setattr__(self, "days", 1)
        if self.comparison_days is None:
            object.__setattr__(self, "comparison_days", 7)
        if self.mentions_change_pct is None:
            object.__setattr__(self, "mentions_change_pct", 30.0)
        if self.sentiment_change_pct is None:
            object.__setattr__(self, "sentiment_change_pct", 30.0)
        if self.minimum_mentions is None:
            object.__setattr__(self, "minimum_mentions", 50)


@dataclass(frozen=True)
class StatisticJobOptions:
    """Options for a read-only statistic PNG job."""

    mode: StatisticMode = "social"
    ticker: str | None = None
    fuzzy_tag: str | None = None
    name: str | None = None
    asset_name: str | None = None
    asset_type: str | None = None
    action: Literal["purchase", "sell", "sell_partial", "all"] = "all"
    source: Literal["x", "reddit", "all"] = "all"
    sentiment: Literal["bullish", "bearish", "mixed", "neutral", "unclear", "all"] = "all"
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    bucket: StatisticBucket = "auto"
    title: str = "Stock-Sum Statistic"


@dataclass
class HttpJobRecord:
    """Persisted local HTTP job metadata."""

    job_id: str
    kind: JobKind
    scope: str
    status: JobStatus
    phase: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    mode: str | None = None
    artifact_path: str | None = None
    artifact_media_type: str | None = None
    summary_path: str | None = None
    collection_result: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    cache_key: str | None = None
    cache_hit: bool = False
    cached_from_job_id: str | None = None
    cache_age_seconds: int | None = None
    coalesced_from_job_id: str | None = None
    coalesced_wait_seconds: int | None = None
    cleanup_result: dict[str, Any] | None = None
    in_memory_jobs: int | None = None
    inflight_reports: int | None = None
    max_in_memory_jobs: int | None = None
    evicted_in_memory_jobs: int | None = None
    worker_pid: int | None = None
    worker_started_at: str | None = None
    worker_finished_at: str | None = None
    worker_exit_code: int | None = None
    worker_runtime_seconds: float | None = None
    worker_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return asdict(self)


@dataclass
class InFlightReport:
    """An artifact job currently producing a summary for a cache key."""

    cache_key: str
    leader_job_id: str
    started_at: datetime
    done: asyncio.Event


_InFlightReport = InFlightReport
