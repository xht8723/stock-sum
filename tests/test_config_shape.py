"""Configuration shape tests."""

from pathlib import Path

from stock_sum.config.loader import load_config


def test_example_config_is_valid() -> None:
    config = load_config(Path("stock_sum/config/example.toml"))

    assert config.service.name == "stock-sum"
    assert config.playwright.channel == ""
    assert config.playwright.browser == "chromium"
    assert config.playwright.headless is True
    assert config.models_dev.refresh_interval_hours == 24
    assert config.providers.scrape_creators.api_key_env == "SCRAPE_CREATORS_API_KEY"
    assert "default" in config.reports
    assert config.reports["default"].collector_ids == []
    assert config.sources.x_users[0].handle == "aleabitoreddit"
    assert config.sources.x_users[0].enabled is False
    assert config.sources.subreddits[0].subreddit == "wallstreetbets"
    assert config.sources.subreddits[0].enabled is False
    assert config.collectors == {}
    assert config.delivery.email["primary"].password_env == "SMTP_PASSWORD"
