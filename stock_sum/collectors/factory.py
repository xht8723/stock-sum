"""Config-driven collector factory."""

from __future__ import annotations

from stock_sum.collectors.api.xpoz import (
    REDDIT_SOURCE_TYPE,
    X_SOURCE_TYPE,
    XpozRedditSubredditCollector,
    XpozXUserTimelineCollector,
)
from stock_sum.collectors.rss.x import X_RSS_SOURCE_TYPE, NitterRssXUserTimelineCollector
from stock_sum.collectors.rss.reddit import REDDIT_RSS_SOURCE_TYPE, RedditRssSubredditCollector
from stock_sum.collectors.api.house import HOUSE_PTR_SOURCE_TYPE, HousePtrDisclosureCollector
from stock_sum.collectors.api.sec_13f import SEC_13F_COLLECTOR_ID, SEC_13F_SOURCE_TYPE, Sec13FDatasetCollector, sec_13f_source_to_collector_config
from stock_sum.collectors.base import Collector
from stock_sum.config.models import AppConfig, CollectorConfig, HousePtrSourceConfig, RedditSubredditSourceConfig, XUserSourceConfig
from stock_sum.core.errors import ConfigurationError


def get_collector_config(config: AppConfig, collector_id: str) -> CollectorConfig:
    """Return a configured collector by dotted collector id."""

    try:
        group, name = collector_id.split(".", 1)
    except ValueError as exc:
        raise ConfigurationError(f"Collector id must be dotted, got: {collector_id}") from exc

    try:
        return config.collectors[group][name]
    except KeyError as exc:
        source_config = _get_source_list_collector_config(config, group, name)
        if source_config is not None:
            return source_config
        raise ConfigurationError(f"Unknown collector id: {collector_id}") from exc


def source_type_for_collector_id(config: AppConfig, collector_id: str, *, x_method: str = "xpoz", reddit_method: str = "xpoz") -> str:
    """Resolve the raw item source type for a configured collector."""

    return source_type_for_collector_config(get_collector_config(config, collector_id), x_method=x_method, reddit_method=reddit_method)


def source_type_for_collector_config(collector_config: CollectorConfig, *, x_method: str = "xpoz", reddit_method: str = "xpoz") -> str:
    """Resolve the raw item source type for a collector config."""

    if collector_config.kind == X_SOURCE_TYPE and x_method == "rss":
        return X_RSS_SOURCE_TYPE
    if collector_config.kind == REDDIT_SOURCE_TYPE and reddit_method == "rss":
        return REDDIT_RSS_SOURCE_TYPE
    return collector_config.kind


def build_collector(config: AppConfig, collector_id: str, *, x_method: str = "xpoz", reddit_method: str = "xpoz") -> Collector:
    """Build a concrete collector from config."""

    if x_method not in {"xpoz", "rss"}:
        raise ConfigurationError(f"Unsupported X collection method: {x_method}")
    if reddit_method not in {"xpoz", "rss"}:
        raise ConfigurationError(f"Unsupported Reddit collection method: {reddit_method}")

    collector_config = get_collector_config(config, collector_id)
    if not collector_config.enabled:
        raise ConfigurationError(f"Collector is disabled: {collector_id}")

    if collector_config.kind == X_SOURCE_TYPE:
        if x_method == "rss":
            return NitterRssXUserTimelineCollector(
                collector_id=collector_id,
                collector_config=collector_config,
                provider_config=config.providers.nitter_rss,
            )
        return XpozXUserTimelineCollector(
            collector_id=collector_id,
            collector_config=collector_config,
            provider_config=config.providers.xpoz,
        )
    if collector_config.kind == REDDIT_SOURCE_TYPE:
        if reddit_method == "rss":
            return RedditRssSubredditCollector(
                collector_id=collector_id,
                collector_config=collector_config,
                provider_config=config.providers.reddit_rss,
            )
        return XpozRedditSubredditCollector(
            collector_id=collector_id,
            collector_config=collector_config,
            provider_config=config.providers.xpoz,
        )
    if collector_config.kind == HOUSE_PTR_SOURCE_TYPE:
        return HousePtrDisclosureCollector(
            collector_id=collector_id,
            collector_config=collector_config,
        )
    if collector_config.kind == SEC_13F_SOURCE_TYPE:
        return Sec13FDatasetCollector(
            collector_id=collector_id,
            collector_config=collector_config,
            source_config=config.sources.sec_13f,
        )

    raise ConfigurationError(f"No collector implementation registered for kind: {collector_config.kind}")


def social_collector_ids(config: AppConfig) -> list[str]:
    """Return enabled X and Reddit collector IDs for the unified social report."""

    collector_ids: list[str] = []
    for source in config.sources.x_users:
        if source.enabled:
            collector_ids.append(f"x.{_source_id(source.handle)}")
    for source in config.sources.subreddits:
        if source.enabled:
            collector_ids.append(f"reddit.{_source_id(source.subreddit)}")
    return collector_ids


def _get_source_list_collector_config(config: AppConfig, group: str, name: str) -> CollectorConfig | None:
    if group == "x":
        for source in config.sources.x_users:
            if _source_id(source.handle) == name:
                return _x_source_to_collector_config(source)
    if group == "reddit":
        for source in config.sources.subreddits:
            if _source_id(source.subreddit) == name:
                return _reddit_source_to_collector_config(source)
    if group == "house" and name == "ptr":
        return _house_ptr_source_to_collector_config(config.sources.house_ptr)
    if f"{group}.{name}" == SEC_13F_COLLECTOR_ID:
        return sec_13f_source_to_collector_config(config.sources.sec_13f)
    return None


def _x_source_to_collector_config(source: XUserSourceConfig) -> CollectorConfig:
    return CollectorConfig(
        kind=X_SOURCE_TYPE,
        enabled=source.enabled,
        handle=source.handle.lstrip("@"),
        limit=source.limit,
        lookback_hours=source.lookback_hours,
    )


def _reddit_source_to_collector_config(source: RedditSubredditSourceConfig) -> CollectorConfig:
    return CollectorConfig(
        kind=REDDIT_SOURCE_TYPE,
        enabled=source.enabled,
        subreddit=source.subreddit.strip("/").removeprefix("r/"),
        sort=source.sort,
        timeframe=source.timeframe,
        limit=source.limit,
        lookback_hours=source.lookback_hours,
        trim=source.trim,
        include_comments=source.include_comments,
        comments_per_post=source.comments_per_post,
    )


def _house_ptr_source_to_collector_config(source: HousePtrSourceConfig) -> CollectorConfig:
    return CollectorConfig(
        kind=HOUSE_PTR_SOURCE_TYPE,
        enabled=source.enabled,
        year=source.year,
        download_concurrency=source.download_concurrency,
        parse_concurrency=source.parse_concurrency,
        zip_url_template=source.zip_url_template,
        pdf_url_template=source.pdf_url_template,
    )


def _source_id(value: str) -> str:
    return value.strip().strip("/").removeprefix("@").removeprefix("r/")
