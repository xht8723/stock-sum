"""Collector factory tests."""

from stock_sum.collectors.api.house import HOUSE_PTR_SOURCE_TYPE
from stock_sum.collectors.api.sec_13f import SEC_13F_SOURCE_TYPE
from stock_sum.collectors.api.xpoz import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE
import pytest

from stock_sum.collectors.factory import build_collector, get_collector_config, source_type_for_collector_id
from stock_sum.config.loader import load_config
from stock_sum.config.models import CollectorConfig
from stock_sum.core.errors import ConfigurationError


def test_source_list_x_user_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "x.aleabitoreddit") == X_SOURCE_TYPE
    assert get_collector_config(config, "x.aleabitoreddit").lookback_hours == 24


def test_source_list_subreddit_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "reddit.wallstreetbets") == REDDIT_SOURCE_TYPE
    assert get_collector_config(config, "reddit.wallstreetbets").lookback_hours == 24


def test_house_ptr_source_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "house.ptr") == HOUSE_PTR_SOURCE_TYPE
    assert get_collector_config(config, "house.ptr").download_concurrency == 1
    assert get_collector_config(config, "house.ptr").parse_concurrency == 1


def test_sec_13f_source_resolves_to_collector_config() -> None:
    config = load_config("stock_sum/config/example.toml")

    assert source_type_for_collector_id(config, "sec.13f") == SEC_13F_SOURCE_TYPE
    assert get_collector_config(config, "sec.13f").kind == SEC_13F_SOURCE_TYPE


def test_removed_scrape_creators_kind_fails_clearly() -> None:
    config = load_config("stock_sum/config/example.toml")
    config.collectors["reddit"] = {
        "legacy": CollectorConfig(kind="scrape_creators_reddit_subreddit", subreddit="wallstreetbets")
    }

    with pytest.raises(ConfigurationError, match="No collector implementation registered"):
        build_collector(config, "reddit.legacy")
