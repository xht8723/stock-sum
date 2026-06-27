"""Collector factory tests."""

from stock_sum.collectors.api.scrape_creators import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE
from stock_sum.collectors.factory import source_type_for_collector_id
from stock_sum.config.loader import load_config


def test_source_list_x_user_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "x.aleabitoreddit") == X_SOURCE_TYPE


def test_source_list_subreddit_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "reddit.wallstreetbets") == REDDIT_SOURCE_TYPE
