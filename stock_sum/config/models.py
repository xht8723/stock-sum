"""Typed TOML configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    """Top-level service process configuration."""

    name: str = "stock-sum"
    timezone: str = "UTC"
    collector_concurrency: int = Field(default=1, ge=1)


class ServerConfig(BaseModel):
    """Local HTTP automation server configuration."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    blacklisted_ips: list[str] = Field(default_factory=list)
    management_allow_remote: bool = False
    artifact_dir: str = "data/http_jobs"
    job_retention_hours: int = Field(default=24, ge=1)
    max_in_memory_jobs: int = Field(default=200, ge=1)
    report_cache_ttl_seconds: int = Field(default=21600, ge=0)
    coalesce_inflight_reports: bool = True


class StorageConfig(BaseModel):
    """SQLite storage configuration."""

    sqlite_path: str = "data/stock_sum.sqlite3"


class MediaConfig(BaseModel):
    """Media download configuration."""

    download_enabled: bool = False
    root_dir: str = "data/media"
    max_bytes: int = Field(default=1_000_000, ge=1)
    timeout_seconds: int = Field(default=20, ge=1)
    allowed_content_types: list[str] = Field(default_factory=lambda: ["image/jpeg", "image/png", "image/gif", "image/webp"])


class RetentionConfig(BaseModel):
    """Runtime data retention and disk usage limits."""

    enabled: bool = True
    max_total_bytes: int = Field(default=268_435_456, ge=1)
    prune_after_pipeline: bool = True


class ReportInputConfig(BaseModel):
    """Limits for building in-memory report input payloads."""

    max_x_posts_per_source: int = Field(default=100, ge=1)
    max_reddit_posts_per_source: int = Field(default=100, ge=1)
    max_reddit_comments_per_post: int = Field(default=10, ge=0)


class ModelsDevConfig(BaseModel):
    """models.dev catalog cache configuration."""

    api_url: str = "https://models.dev/api.json"
    catalog_url: str = "https://models.dev/catalog.json"
    cache_path: str = "data/cache/models_dev_api.json"
    refresh_interval_hours: int = Field(default=24, ge=1)


class XpozProviderConfig(BaseModel):
    """Xpoz MCP-over-HTTP provider settings."""

    api_key_env: str = "XPOZ_API_KEY"
    server_url: str = "https://mcp.xpoz.ai/mcp"
    timeout_seconds: int = Field(default=60, ge=1)
    max_concurrent_requests: int = Field(default=1, ge=1)


class ProvidersConfig(BaseModel):
    """External API provider settings."""

    xpoz: XpozProviderConfig = Field(default_factory=XpozProviderConfig)


class LLMConfig(BaseModel):
    """Provider-neutral LLM selection."""

    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: int = Field(default=60, ge=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=5000, ge=1)
    thinking_enabled: bool = True
    analysis_x_posts_per_chunk: int = Field(default=10, ge=1)
    analysis_max_chars_per_chunk: int = Field(default=12000, ge=1000)
    analysis_max_concurrency: int = Field(default=1, ge=1)


class ReportProfileConfig(BaseModel):
    """A manually requested report profile."""

    collector_ids: list[str] = Field(default_factory=list)


class CollectorConfig(BaseModel):
    """Generic collector configuration block."""

    kind: str
    enabled: bool = True
    api_url: str | None = None
    handle: str | None = None
    subreddit: str | None = None
    sort: str = "new"
    timeframe: str = "day"
    limit: int = Field(default=100, ge=1)
    lookback_hours: int = Field(default=24, ge=1)
    trim: bool = True
    include_comments: bool = True
    comments_per_post: int = Field(default=10, ge=0)
    year: int | None = Field(default=None, ge=0)
    download_concurrency: int = Field(default=1, ge=1)
    parse_concurrency: int = Field(default=1, ge=1)
    zip_url_template: str = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
    pdf_url_template: str = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"


class XUserSourceConfig(BaseModel):
    """Configured X user source."""

    handle: str
    enabled: bool = True
    limit: int = Field(default=100, ge=1)
    lookback_hours: int = Field(default=24, ge=1)


class RedditSubredditSourceConfig(BaseModel):
    """Configured Reddit subreddit source."""

    subreddit: str
    enabled: bool = True
    sort: str = "new"
    timeframe: str = "day"
    limit: int = Field(default=100, ge=1)
    lookback_hours: int = Field(default=24, ge=1)
    trim: bool = True
    include_comments: bool = True
    comments_per_post: int = Field(default=10, ge=0)


class HousePtrSourceConfig(BaseModel):
    """Configured House PTR disclosure source."""

    enabled: bool = True
    year: int | None = Field(default=None, ge=0)
    refresh_ttl_seconds: int = Field(default=21600, ge=0)
    download_concurrency: int = Field(default=1, ge=1)
    parse_concurrency: int = Field(default=1, ge=1)
    zip_url_template: str = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
    pdf_url_template: str = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"


class Sec13FSourceConfig(BaseModel):
    """Configured SEC Form 13F dataset source."""

    enabled: bool = True
    page_url: str = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
    refresh_ttl_seconds: int = Field(default=21600, ge=0)
    download_timeout_seconds: int = Field(default=120, ge=1)
    user_agent: str = "stock-sum/0.1 contact@example.com"


class SourcesConfig(BaseModel):
    """Long-list source configuration."""

    x_users: list[XUserSourceConfig] = Field(default_factory=list)
    subreddits: list[RedditSubredditSourceConfig] = Field(default_factory=list)
    house_ptr: HousePtrSourceConfig = Field(default_factory=HousePtrSourceConfig)
    sec_13f: Sec13FSourceConfig = Field(default_factory=Sec13FSourceConfig)


class AppConfig(BaseModel):
    """Complete application configuration."""

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    report_input: ReportInputConfig = Field(default_factory=ReportInputConfig)
    models_dev: ModelsDevConfig = Field(default_factory=ModelsDevConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    llm: LLMConfig
    reports: dict[str, ReportProfileConfig] = Field(default_factory=dict)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    collectors: dict[str, dict[str, CollectorConfig]] = Field(default_factory=dict)
