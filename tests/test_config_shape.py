"""Configuration shape tests."""

from pathlib import Path

from stock_sum.config.loader import load_config


def test_example_config_is_valid() -> None:
    config = load_config(Path("stock_sum/config/example.toml"))

    assert config.service.name == "stock-sum"
    assert config.playwright.channel == ""
    assert config.models_dev.refresh_interval_hours == 24
    assert config.playwright.x.user_data_dir == "data/browser_profiles/x"
    assert config.playwright.x.max_posts == 10
    assert "morning" in config.reports
    assert config.delivery.email["primary"].password_env == "SMTP_PASSWORD"
