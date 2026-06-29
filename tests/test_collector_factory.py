"""Collector factory tests."""

from stock_sum.collectors.api.xpoz import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE
import pytest

from stock_sum.collectors.factory import build_collector, source_type_for_collector_id
from stock_sum.config.loader import load_config
from stock_sum.config.models import CollectorConfig
from stock_sum.core.errors import ConfigurationError


def test_source_list_x_user_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "x.aleabitoreddit") == X_SOURCE_TYPE


def test_source_list_subreddit_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "reddit.wallstreetbets") == REDDIT_SOURCE_TYPE


def test_removed_scrape_creators_kind_fails_clearly() -> None:
    config = load_config("stock_sum/config/example.toml")
    config.collectors["reddit"] = {
        "legacy": CollectorConfig(kind="scrape_creators_reddit_subreddit", subreddit="wallstreetbets")
    }

    with pytest.raises(ConfigurationError, match="No collector implementation registered"):
        build_collector(config, "reddit.legacy")
