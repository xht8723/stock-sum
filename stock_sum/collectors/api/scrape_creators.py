"""Scrape Creators API collectors for X and Reddit."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
import os
import re

import httpx

from stock_sum.config.models import CollectorConfig, ScrapeCreatorsProviderConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.models import RawItem

X_SOURCE_TYPE = "scrape_creators_x_user_tweets"
REDDIT_SOURCE_TYPE = "scrape_creators_reddit_subreddit"


class ScrapeCreatorsError(StockSumError):
    """Base error for Scrape Creators failures."""


class ScrapeCreatorsAuthError(ScrapeCreatorsError):
    """Raised when Scrape Creators credentials are missing or invalid."""


class ScrapeCreatorsCreditsError(ScrapeCreatorsError):
    """Raised when the Scrape Creators account has insufficient credits."""


class ScrapeCreatorsNotFoundError(ScrapeCreatorsError):
    """Raised when a requested Scrape Creators source is not found."""


class ScrapeCreatorsRetryableError(ScrapeCreatorsError):
    """Raised when Scrape Creators returns a retryable server failure."""


class ScrapeCreatorsClient:
    """Small async client for Scrape Creators REST endpoints."""

    def __init__(
        self,
        *,
        api_key_env: str,
        base_url: str,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_provider_config(cls, config: ScrapeCreatorsProviderConfig) -> ScrapeCreatorsClient:
        """Build a client from provider config."""

        return cls(
            api_key_env=config.api_key_env,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
        )

    async def twitter_user_tweets(self, *, handle: str, trim: bool) -> Any:
        """Fetch X user tweets."""

        return await self.get_json(
            "/v1/twitter/user-tweets",
            params={"handle": handle.lstrip("@"), "trim": str(trim).lower()},
        )

    async def reddit_subreddit(self, *, subreddit: str, sort: str, timeframe: str, trim: bool) -> Any:
        """Fetch subreddit posts."""

        params = {
            "subreddit": subreddit.strip("/").removeprefix("r/"),
            "sort": sort,
            "trim": str(trim).lower(),
        }
        if timeframe and sort == "top":
            params["timeframe"] = timeframe
        return await self.get_json(
            "/v1/reddit/subreddit",
            params=params,
        )

    async def reddit_post_comments(self, *, url: str, trim: bool) -> Any:
        """Fetch comments for a Reddit post URL."""

        return await self.get_json(
            "/v1/reddit/post/comments",
            params={"url": url, "trim": str(trim).lower()},
        )

    async def get_json(self, path: str, *, params: Mapping[str, Any]) -> Any:
        """Return decoded JSON from a Scrape Creators endpoint."""

        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ScrapeCreatorsAuthError(
                f"Missing Scrape Creators API key. Set environment variable {self.api_key_env}."
            )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.get(path, params=params, headers={"x-api-key": api_key})

        if response.status_code == 401:
            raise ScrapeCreatorsAuthError("Scrape Creators rejected the configured API key.")
        if response.status_code == 402:
            raise ScrapeCreatorsCreditsError("Scrape Creators account has insufficient credits.")
        if response.status_code == 404:
            raise ScrapeCreatorsNotFoundError(f"Scrape Creators source not found for {path}.")
        if response.status_code >= 500:
            raise ScrapeCreatorsRetryableError(
                f"Scrape Creators returned retryable HTTP {response.status_code} for {path}."
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScrapeCreatorsError(f"Scrape Creators request failed: HTTP {response.status_code}") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise ScrapeCreatorsError("Scrape Creators returned invalid JSON.") from exc


class ScrapeCreatorsXUserTweetsCollector:
    """Collect X user tweets through Scrape Creators."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        provider_config: ScrapeCreatorsProviderConfig,
        client: ScrapeCreatorsClient | None = None,
    ) -> None:
        if not collector_config.handle:
            raise ConfigurationError(f"Collector {collector_id} requires handle.")
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.provider_config = provider_config
        self.client = client or ScrapeCreatorsClient.from_provider_config(provider_config)

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Fetch and normalize configured X user tweets."""

        handle = self.collector_config.handle or ""
        payload = await self.client.twitter_user_tweets(handle=handle, trim=self.collector_config.trim)
        tweets = _extract_items(payload, ("tweets", "data", "results", "posts"))
        items: list[RawItem] = []
        for tweet in tweets[: self.collector_config.limit]:
            item = _raw_x_item(tweet, handle)
            if item is not None:
                items.append(item)
        return items


class ScrapeCreatorsRedditSubredditCollector:
    """Collect Reddit subreddit posts and optional comments through Scrape Creators."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        provider_config: ScrapeCreatorsProviderConfig,
        client: ScrapeCreatorsClient | None = None,
    ) -> None:
        if not collector_config.subreddit:
            raise ConfigurationError(f"Collector {collector_id} requires subreddit.")
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.provider_config = provider_config
        self.client = client or ScrapeCreatorsClient.from_provider_config(provider_config)

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Fetch and normalize configured subreddit posts and optional comments."""

        subreddit = self.collector_config.subreddit or ""
        payload = await self.client.reddit_subreddit(
            subreddit=subreddit,
            sort=self.collector_config.sort,
            timeframe=self.collector_config.timeframe,
            trim=self.collector_config.trim,
        )
        posts = _extract_items(payload, ("posts", "data", "results", "children"))
        items: list[RawItem] = []
        for post in posts[: self.collector_config.limit]:
            post_item = _raw_reddit_post_item(post, subreddit)
            if post_item is None:
                continue
            items.append(post_item)
            if self.collector_config.include_comments and self.collector_config.comments_per_post > 0:
                items.extend(await self._collect_comments(post_item))
        return items

    async def _collect_comments(self, post_item: RawItem) -> list[RawItem]:
        post_url = post_item.url or post_item.metadata.get("permalink")
        if not post_url:
            return []
        if isinstance(post_url, str) and post_url.startswith("/"):
            post_url = f"https://www.reddit.com{post_url}"
        payload = await self.client.reddit_post_comments(url=str(post_url), trim=self.collector_config.trim)
        comments = _extract_items(payload, ("comments", "data", "results", "children"))
        items: list[RawItem] = []
        for comment in comments[: self.collector_config.comments_per_post]:
            item = _raw_reddit_comment_item(comment, post_item)
            if item is not None:
                items.append(item)
        return items


def _extract_items(payload: Any, keys: Iterable[str]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_unwrap_reddit_child(item) for item in payload if isinstance(_unwrap_reddit_child(item), dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [_unwrap_reddit_child(item) for item in value if isinstance(_unwrap_reddit_child(item), dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        children = data.get("children")
        if isinstance(children, list):
            return [_unwrap_reddit_child(item) for item in children if isinstance(_unwrap_reddit_child(item), dict)]
    return []


def _unwrap_reddit_child(item: Any) -> Any:
    if isinstance(item, dict) and isinstance(item.get("data"), dict):
        return item["data"]
    return item


def _raw_x_item(tweet: dict[str, Any], handle: str) -> RawItem | None:
    status_id = _string_value(tweet, "id", "id_str", "tweet_id", "status_id", "rest_id")
    url = _string_value(tweet, "url", "tweet_url", "tweetUrl", "link")
    if not status_id and url:
        status_id = _status_id_from_url(url)
    if not status_id:
        return None

    normalized_handle = handle.lstrip("@")
    if not url:
        url = f"https://x.com/{normalized_handle}/status/{status_id}"

    text = _string_value(tweet, "text", "full_text", "content", "body") or ""
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    user = tweet.get("user") if isinstance(tweet.get("user"), dict) else {}
    author_map = author or user

    return RawItem(
        source_id=status_id,
        source_type=X_SOURCE_TYPE,
        url=url,
        text=text,
        metadata={
            "entity_type": "x_post",
            "handle": normalized_handle,
            "author_handle": _string_value(author_map, "userName", "username", "screen_name", "handle"),
            "author_name": _string_value(author_map, "name", "display_name"),
            "posted_at_text": _string_value(tweet, "created_at", "createdAt", "timestamp", "date"),
            "reply_count": _int_value(tweet, "reply_count", "replyCount", "replies"),
            "repost_count": _int_value(tweet, "retweet_count", "retweetCount", "reposts"),
            "like_count": _int_value(tweet, "favorite_count", "favoriteCount", "like_count", "likes"),
            "quote_count": _int_value(tweet, "quote_count", "quoteCount", "quotes"),
            "view_count": _int_value(tweet, "view_count", "viewCount", "views"),
            "media": _extract_x_media(tweet),
            "raw": tweet,
        },
    )


def _raw_reddit_post_item(post: dict[str, Any], subreddit: str) -> RawItem | None:
    post_id = _string_value(post, "id")
    if not post_id:
        name = _string_value(post, "name")
        if name and "_" in name:
            post_id = name.split("_", 1)[1]
    if not post_id:
        return None

    permalink = _string_value(post, "permalink")
    url = _string_value(post, "url", "link")
    if permalink and permalink.startswith("/"):
        permalink_url = f"https://www.reddit.com{permalink}"
    else:
        permalink_url = permalink
    canonical_url = permalink_url or url

    return RawItem(
        source_id=post_id,
        source_type=REDDIT_SOURCE_TYPE,
        url=canonical_url,
        text=_string_value(post, "selftext", "body", "text") or "",
        metadata={
            "entity_type": "reddit_post",
            "subreddit": (_string_value(post, "subreddit") or subreddit).strip("/").removeprefix("r/"),
            "fullname": _string_value(post, "name"),
            "title": _string_value(post, "title") or "",
            "author": _string_value(post, "author"),
            "permalink": permalink_url,
            "score": _int_value(post, "score"),
            "ups": _int_value(post, "ups"),
            "upvote_ratio": _float_value(post, "upvote_ratio", "upvoteRatio"),
            "num_comments": _int_value(post, "num_comments", "numComments", "comments"),
            "thumbnail_url": _usable_url(_string_value(post, "thumbnail")),
            "created_at_text": _string_value(post, "created_utc", "createdUtc", "created", "date"),
            "media": _extract_reddit_media(post),
            "raw": post,
        },
    )


def _raw_reddit_comment_item(comment: dict[str, Any], post_item: RawItem) -> RawItem | None:
    comment_id = _string_value(comment, "id")
    if not comment_id:
        name = _string_value(comment, "name")
        if name and "_" in name:
            comment_id = name.split("_", 1)[1]
    if not comment_id:
        return None

    permalink = _string_value(comment, "permalink")
    if permalink and permalink.startswith("/"):
        url = f"https://www.reddit.com{permalink}"
    elif permalink:
        url = permalink
    else:
        url = post_item.url

    body = _string_value(comment, "body", "text", "content") or ""
    return RawItem(
        source_id=f"{post_item.source_id}:{comment_id}",
        source_type=REDDIT_SOURCE_TYPE,
        url=url,
        text=body,
        metadata={
            "entity_type": "reddit_comment",
            "comment_id": comment_id,
            "post_id": post_item.source_id,
            "parent_id": _string_value(comment, "parent_id", "parentId"),
            "author": _string_value(comment, "author"),
            "body": body,
            "score": _int_value(comment, "score"),
            "ups": _int_value(comment, "ups"),
            "created_at_text": _string_value(comment, "created_utc", "createdUtc", "created", "date"),
            "depth": _int_value(comment, "depth"),
            "raw": comment,
        },
    )


def _extract_x_media(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for candidate in _media_candidates(tweet):
        media_url = _string_value(candidate, "media_url_https", "media_url", "url", "preview_image_url")
        media_type = _string_value(candidate, "type") or "unknown"
        if media_url:
            media.append(
                {
                    "media_key": _string_value(candidate, "media_key", "id_str", "id"),
                    "media_type": media_type,
                    "url": media_url,
                    "alt_text": _string_value(candidate, "ext_alt_text", "alt_text"),
                    "raw": candidate,
                }
            )
        video_info = candidate.get("video_info")
        if isinstance(video_info, dict):
            for variant in video_info.get("variants", []):
                if isinstance(variant, dict) and variant.get("url"):
                    media.append(
                        {
                            "media_key": _string_value(candidate, "media_key", "id_str", "id"),
                            "media_type": "video",
                            "url": str(variant["url"]),
                            "alt_text": _string_value(candidate, "ext_alt_text", "alt_text"),
                            "raw": variant,
                        }
                    )
    return media


def _extract_reddit_media(post: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    thumbnail = _usable_url(_string_value(post, "thumbnail"))
    if thumbnail:
        media.append({"media_type": "thumbnail", "url": thumbnail, "source_field": "thumbnail", "raw": {"url": thumbnail}})

    preview = post.get("preview")
    if isinstance(preview, dict):
        for image in preview.get("images", []):
            if not isinstance(image, dict):
                continue
            source = image.get("source")
            if isinstance(source, dict) and source.get("url"):
                media.append(
                    {
                        "media_type": "image",
                        "url": str(source["url"]).replace("&amp;", "&"),
                        "source_field": "preview.images.source",
                        "raw": source,
                    }
                )

    media_obj = post.get("media")
    if isinstance(media_obj, dict):
        reddit_video = media_obj.get("reddit_video")
        if isinstance(reddit_video, dict):
            fallback_url = _string_value(reddit_video, "fallback_url", "hls_url", "dash_url")
            if fallback_url:
                media.append(
                    {
                        "media_type": "video",
                        "url": fallback_url,
                        "source_field": "media.reddit_video",
                        "raw": reddit_video,
                    }
                )
    return media


def _media_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("media",):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    for key in ("entities", "extended_entities"):
        value = payload.get(key)
        if isinstance(value, dict) and isinstance(value.get("media"), list):
            candidates.extend(item for item in value["media"] if isinstance(item, dict))
    return candidates


def _string_value(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, int | float):
            return str(value)
    return None


def _int_value(payload: Mapping[str, Any], *keys: str) -> int | None:
    value = _string_value(payload, *keys)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _float_value(payload: Mapping[str, Any], *keys: str) -> float | None:
    value = _string_value(payload, *keys)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _status_id_from_url(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", url)
    if not match:
        return None
    return match.group(1)


def _usable_url(value: str | None) -> str | None:
    if not value or value in {"self", "default", "nsfw", "spoiler"}:
        return None
    if not value.startswith(("http://", "https://")):
        return None
    return value
