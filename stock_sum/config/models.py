"""Typed TOML configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    """Top-level service process configuration."""

    name: str = "stock-sum"
    timezone: str = "UTC"
    collector_concurrency: int = Field(default=3, ge=1)


class ServerConfig(BaseModel):
    """Local HTTP automation server configuration."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    blacklisted_ips: list[str] = Field(default_factory=list)
    management_allow_remote: bool = False
    artifact_dir: str = "data/http_jobs"
    job_retention_hours: int = Field(default=24, ge=1)
    report_cache_ttl_seconds: int = Field(default=3600, ge=0)


class StorageConfig(BaseModel):
    """SQLite storage configuration."""

    sqlite_path: str = "data/stock_sum.sqlite3"


class MediaConfig(BaseModel):
    """Media download configuration."""

    download_enabled: bool = False
    root_dir: str = "data/media"
    max_bytes: int = Field(default=5_000_000, ge=1)
    timeout_seconds: int = Field(default=20, ge=1)
    allowed_content_types: list[str] = Field(default_factory=lambda: ["image/jpeg", "image/png", "image/gif", "image/webp"])


class RetentionConfig(BaseModel):
    """Runtime data retention and disk usage limits."""

    enabled: bool = True
    max_total_bytes: int = Field(default=2_147_483_648, ge=1)
    prune_after_pipeline: bool = True


class ModelsDevConfig(BaseModel):
    """models.dev catalog cache configuration."""

    api_url: str = "https://models.dev/api.json"
    catalog_url: str = "https://models.dev/catalog.json"
    cache_path: str = "data/cache/models_dev_api.json"
    refresh_interval_hours: int = Field(default=24, ge=1)


class PlaywrightXConfig(BaseModel):
    """X timeline scraping settings for public Playwright access."""

    base_url: str = "https://x.com"
    max_scrolls: int = Field(default=12, ge=0)
    selector_timeout_seconds: int = Field(default=10, ge=1)
    page_settle_ms: int = Field(default=1500, ge=0)
    scroll_pause_ms: int = Field(default=1200, ge=0)


class PlaywrightConfig(BaseModel):
    """Browser automation defaults for Playwright collectors."""

    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    channel: Literal["", "chrome", "msedge", "chromium"] = "chromium"
    headless: bool = True
    timeout_seconds: int = Field(default=30, ge=1)
    x: PlaywrightXConfig = Field(default_factory=PlaywrightXConfig)


class XpozProviderConfig(BaseModel):
    """Xpoz MCP-over-HTTP provider settings."""

    api_key_env: str = "XPOZ_API_KEY"
    server_url: str = "https://mcp.xpoz.ai/mcp"
    timeout_seconds: int = Field(default=60, ge=1)
    max_concurrent_requests: int = Field(default=2, ge=1)


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
    thinking_enabled: bool = False
    analysis_x_posts_per_chunk: int = Field(default=10, ge=1)
    analysis_max_chars_per_chunk: int = Field(default=12000, ge=1000)
    analysis_max_concurrency: int = Field(default=5, ge=1)


class ReportProfileConfig(BaseModel):
    """A scheduled or manually requested report profile."""

    timezone: str = "UTC"
    schedule: str
    collector_ids: list[str] = Field(default_factory=list)
    delivery_ids: list[str] = Field(default_factory=list)


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


class SourcesConfig(BaseModel):
    """Long-list source configuration."""

    x_users: list[XUserSourceConfig] = Field(default_factory=list)
    subreddits: list[RedditSubredditSourceConfig] = Field(default_factory=list)


class EmailDeliveryConfig(BaseModel):
    """Email delivery configuration with secret env-var names only."""

    kind: Literal["email"] = "email"
    enabled: bool = False
    smtp_host: str
    smtp_port: int = 587
    username_env: str
    password_env: str
    from_address: str
    to_addresses: list[str] = Field(default_factory=list)


class WhatsAppDeliveryConfig(BaseModel):
    """WhatsApp delivery configuration with secret env-var names only."""

    kind: Literal["whatsapp"] = "whatsapp"
    enabled: bool = False
    provider: str = "twilio"
    account_sid_env: str
    auth_token_env: str
    from_number_env: str
    to_numbers: list[str] = Field(default_factory=list)


class DeliveryConfig(BaseModel):
    """Delivery channel groups."""

    email: dict[str, EmailDeliveryConfig] = Field(default_factory=dict)
    whatsapp: dict[str, WhatsAppDeliveryConfig] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Complete application configuration."""

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    models_dev: ModelsDevConfig = Field(default_factory=ModelsDevConfig)
    playwright: PlaywrightConfig = Field(default_factory=PlaywrightConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    llm: LLMConfig
    reports: dict[str, ReportProfileConfig] = Field(default_factory=dict)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    collectors: dict[str, dict[str, CollectorConfig]] = Field(default_factory=dict)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
