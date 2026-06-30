"""Configuration shape tests."""

from pathlib import Path

from stock_sum.config.loader import load_config


def test_example_config_is_valid() -> None:
    config = load_config(Path("stock_sum/config/example.toml"))

    assert config.service.name == "stock-sum"
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8000
    assert config.server.blacklisted_ips == []
    assert config.server.artifact_dir == "data/http_jobs"
    assert config.server.report_cache_ttl_seconds == 3600
    assert config.playwright.channel == "chromium"
    assert config.playwright.browser == "chromium"
    assert config.playwright.headless is True
    assert config.playwright.x.base_url == "https://x.com"
    assert config.playwright.x.max_scrolls == 12
    assert config.media.root_dir == "data/media"
    assert config.media.download_enabled is False
    assert config.retention.enabled is True
    assert config.retention.max_total_bytes == 2_147_483_648
    assert config.retention.prune_after_pipeline is True
    assert config.models_dev.refresh_interval_hours == 24
    assert config.providers.xpoz.api_key_env == "XPOZ_API_KEY"
    assert config.providers.xpoz.server_url == "https://mcp.xpoz.ai/mcp"
    assert config.llm.provider == "deepseek"
    assert config.llm.model == "deepseek-v4-flash"
    assert config.llm.api_key_env == "DEEPSEEK_API_KEY"
    assert config.llm.base_url == "https://api.deepseek.com"
    assert config.llm.max_tokens == 5000
    assert config.llm.thinking_enabled is False
    assert config.llm.analysis_x_posts_per_chunk == 10
    assert config.llm.analysis_max_chars_per_chunk == 12000
    assert "default" in config.reports
    assert config.reports["default"].collector_ids == ["x.aleabitoreddit", "reddit.wallstreetbets"]
    assert config.sources.x_users[0].handle == "aleabitoreddit"
    assert config.sources.x_users[0].enabled is True
    assert config.sources.x_users[0].limit == 100
    assert config.sources.x_users[0].lookback_hours == 24
    assert config.sources.subreddits[0].subreddit == "wallstreetbets"
    assert config.sources.subreddits[0].enabled is True
    assert config.sources.subreddits[0].limit == 100
    assert config.sources.subreddits[0].lookback_hours == 24
    assert config.collectors == {}
    assert config.delivery.email["primary"].password_env == "SMTP_PASSWORD"
