"""Typed TOML configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    """Top-level service process configuration."""

    name: str = "stock-sum"
    timezone: str = "UTC"


class StorageConfig(BaseModel):
    """SQLite storage configuration."""

    sqlite_path: str = "data/stock_sum.sqlite3"


class ModelsDevConfig(BaseModel):
    """models.dev catalog cache configuration."""

    api_url: str = "https://models.dev/api.json"
    catalog_url: str = "https://models.dev/catalog.json"
    cache_path: str = "data/cache/models_dev_api.json"
    refresh_interval_hours: int = Field(default=24, ge=1)


class PlaywrightConfig(BaseModel):
    """Browser automation defaults for Playwright collectors."""

    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    channel: Literal["", "chrome", "msedge", "chromium"] = ""
    headless: bool = True
    timeout_seconds: int = Field(default=30, ge=1)


class ScrapeCreatorsProviderConfig(BaseModel):
    """Scrape Creators API provider settings."""

    api_key_env: str = "SCRAPE_CREATORS_API_KEY"
    base_url: str = "https://api.scrapecreators.com"
    timeout_seconds: int = Field(default=30, ge=1)


class ProvidersConfig(BaseModel):
    """External API provider settings."""

    scrape_creators: ScrapeCreatorsProviderConfig = Field(default_factory=ScrapeCreatorsProviderConfig)


class LLMConfig(BaseModel):
    """Provider-neutral LLM selection."""

    provider: str
    model: str
    api_key_env: str


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
    limit: int = Field(default=10, ge=1)
    trim: bool = True
    include_comments: bool = False
    comments_per_post: int = Field(default=0, ge=0)


class XUserSourceConfig(BaseModel):
    """Configured X user source."""

    handle: str
    enabled: bool = True
    limit: int = Field(default=10, ge=1)
    trim: bool = True


class RedditSubredditSourceConfig(BaseModel):
    """Configured Reddit subreddit source."""

    subreddit: str
    enabled: bool = True
    sort: str = "new"
    timeframe: str = "day"
    limit: int = Field(default=10, ge=1)
    trim: bool = True
    include_comments: bool = False
    comments_per_post: int = Field(default=0, ge=0)


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
    storage: StorageConfig = Field(default_factory=StorageConfig)
    models_dev: ModelsDevConfig = Field(default_factory=ModelsDevConfig)
    playwright: PlaywrightConfig = Field(default_factory=PlaywrightConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    llm: LLMConfig
    reports: dict[str, ReportProfileConfig] = Field(default_factory=dict)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    collectors: dict[str, dict[str, CollectorConfig]] = Field(default_factory=dict)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
