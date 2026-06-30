"""Xpoz MCP-over-HTTP collectors for X and Reddit."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
import csv
import html
import json
import os
import re

import httpx

from stock_sum.config.models import CollectorConfig, XpozProviderConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.models import PipelineSectionWarning, ProviderApiResponse, RawItem

X_SOURCE_TYPE = "x_user_timeline"
REDDIT_SOURCE_TYPE = "reddit_subreddit"

X_AUTHOR_FIELDS = [
    "id",
    "text",
    "authorUsername",
    "createdAt",
    "createdAtDate",
]
X_DETAIL_FIELDS = [
    "id",
    "text",
    "authorUsername",
    "createdAt",
    "createdAtDate",
    "mediaUrls",
    "urls",
    "likeCount",
    "retweetCount",
    "replyCount",
    "quoteCount",
    "impressionCount",
    "possiblySensitive",
    "isRetweet",
    "source",
    "status",
]
REDDIT_POST_FIELDS = [
    "id",
    "title",
    "url",
    "permalink",
    "postUrl",
    "thumbnail",
    "authorUsername",
    "subredditName",
    "score",
    "upvotes",
    "downvotes",
    "upvoteRatio",
    "commentsCount",
    "createdAt",
    "createdAtDate",
    "isSelf",
    "isVideo",
    "over18",
]
REDDIT_COMMENT_FIELDS = [
    "id",
    "body",
    "authorUsername",
    "parentPostId",
    "parentId",
    "score",
    "upvotes",
    "depth",
    "createdAt",
    "createdAtDate",
    "isSubmitter",
    "stickied",
]


class XpozError(StockSumError):
    """Base error for Xpoz provider failures."""


class XpozAuthError(XpozError):
    """Raised when Xpoz credentials are missing or invalid."""


class XpozCreditsError(XpozError):
    """Raised when Xpoz quota or credits are exhausted."""


class XpozRetryableError(XpozError):
    """Raised for retryable Xpoz failures."""


class XpozResponseError(XpozError):
    """Raised when Xpoz returns an unexpected response shape."""


class XpozClient:
    """Small async client for Xpoz Streamable HTTP MCP tools."""

    def __init__(
        self,
        *,
        api_key_env: str,
        server_url: str,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key_env = api_key_env
        self.server_url = server_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._session_id: str | None = None
        self._initialized = False
        self.provider_responses: list[ProviderApiResponse] = []

    @classmethod
    def from_provider_config(cls, config: XpozProviderConfig) -> XpozClient:
        """Build a client from provider config."""

        return cls(
            api_key_env=config.api_key_env,
            server_url=config.server_url,
            timeout_seconds=config.timeout_seconds,
        )

    async def twitter_posts_by_author(
        self,
        *,
        username: str,
        limit: int,
        fields: list[str] | None = None,
        force_latest: bool = True,
    ) -> list[dict[str, Any]]:
        """Return X posts by author username."""

        return await self.call_tool_rows(
            "getTwitterPostsByAuthor",
            {
                "username": username.lstrip("@"),
                "limit": limit,
                "forceLatest": force_latest,
                "responseType": "fast",
                "fields": fields or X_AUTHOR_FIELDS,
            },
        )

    async def twitter_posts_by_ids(
        self,
        *,
        post_ids: list[str],
        fields: list[str] | None = None,
        force_latest: bool = True,
    ) -> list[dict[str, Any]]:
        """Return X posts by numeric status IDs."""

        if not post_ids:
            return []
        return await self.call_tool_rows(
            "getTwitterPostsByIds",
            {
                "postIds": post_ids,
                "forceLatest": force_latest,
                "fields": fields or X_DETAIL_FIELDS,
            },
        )

    async def reddit_subreddit_posts(
        self,
        *,
        subreddit: str,
        limit: int,
        fields: list[str] | None = None,
        force_latest: bool = True,
    ) -> list[dict[str, Any]]:
        """Return posts for a subreddit."""

        return await self.call_tool_rows(
            "getRedditSubredditWithPostsByName",
            {
                "subredditName": normalize_subreddit(subreddit),
                "limit": limit,
                "forceLatest": force_latest,
                "responseType": "fast",
                "postFields": fields or REDDIT_POST_FIELDS,
            },
        )

    async def reddit_post_with_comments(
        self,
        *,
        post_id: str,
        limit: int,
        post_fields: list[str] | None = None,
        comment_fields: list[str] | None = None,
        force_latest: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return one Reddit post and comments by post ID."""

        tool_name = "getRedditPostWithCommentsById"
        arguments = {
                "postId": post_id,
                "limit": limit,
                "forceLatest": force_latest,
                "responseType": "fast",
                "postFields": post_fields or REDDIT_POST_FIELDS,
                "commentFields": comment_fields or REDDIT_COMMENT_FIELDS,
        }
        text = await self.call_tool_text(tool_name, arguments)
        posts = parse_xpoz_rows(text, preferred_prefix="posts")
        comments = parse_xpoz_rows(text, preferred_prefix="comments")
        archive_rows = [
            {"section": "posts", **row}
            for row in posts
        ] + [
            {"section": "comments", **row}
            for row in comments
        ]
        self._record_provider_response(tool_name, arguments, text, archive_rows)
        return {"posts": posts, "comments": comments}

    async def call_tool_rows(self, tool_name: str, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Call one Xpoz MCP tool and parse row-like text content."""

        text = await self.call_tool_text(tool_name, arguments)
        rows = parse_xpoz_rows(text)
        self._record_provider_response(tool_name, arguments, text, rows)
        return rows

    async def call_tool_text(self, tool_name: str, arguments: Mapping[str, Any]) -> str:
        """Call one Xpoz MCP tool and return joined text content."""

        await self._ensure_initialized()
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": dict(arguments)},
        }
        response = await self._post(payload)
        if isinstance(response, dict) and response.get("error"):
            raise XpozResponseError(f"Xpoz tool {tool_name} failed: {response['error']}")
        try:
            content = response["result"]["content"]
        except (KeyError, TypeError) as exc:
            raise XpozResponseError(f"Xpoz tool {tool_name} returned no content.") from exc
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if not texts:
            raise XpozResponseError(f"Xpoz tool {tool_name} returned no text content.")
        return "\n".join(texts)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        response = await self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "stock-sum", "version": "0.1.0"},
                },
            }
        )
        if isinstance(response, dict) and response.get("error"):
            raise XpozResponseError(f"Xpoz initialize failed: {response['error']}")
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self._initialized = True

    async def _post(self, payload: Mapping[str, Any]) -> Any:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise XpozAuthError(f"Missing Xpoz API key. Set environment variable {self.api_key_env}.")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "stock-sum/0.1",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.post(self.server_url, json=dict(payload), headers=headers)
        except httpx.TimeoutException as exc:
            raise XpozRetryableError("Xpoz request timed out.") from exc
        except httpx.TransportError as exc:
            raise XpozRetryableError(f"Xpoz transport failed: {exc}") from exc

        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        if response.status_code in {401, 403}:
            raise XpozAuthError("Xpoz rejected the configured API key.")
        if response.status_code in {402, 429}:
            raise XpozCreditsError(f"Xpoz quota or rate limit failure: HTTP {response.status_code}.")
        if response.status_code >= 500:
            raise XpozRetryableError(f"Xpoz returned retryable HTTP {response.status_code}.")
        if response.status_code == 202:
            return {}
        if response.status_code >= 400:
            raise XpozError(f"Xpoz request failed: HTTP {response.status_code}.")
        return parse_mcp_response_text(response.text)

    def take_provider_responses(self) -> list[ProviderApiResponse]:
        """Return and clear captured provider responses."""

        responses = self.provider_responses
        self.provider_responses = []
        return responses

    def _record_provider_response(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        raw_response_text: str,
        parsed_rows: list[dict[str, Any]],
    ) -> None:
        self.provider_responses.append(
            ProviderApiResponse(
                provider="xpoz",
                tool_name=tool_name,
                request_arguments=dict(arguments),
                raw_response_text=raw_response_text,
                parsed_rows=parsed_rows,
                row_count=len(parsed_rows),
            )
        )


class XpozXUserTimelineCollector:
    """Collect recent X user timeline posts through Xpoz."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        provider_config: XpozProviderConfig,
        client: XpozClient | None = None,
    ) -> None:
        if not collector_config.handle:
            raise ConfigurationError(f"Collector {collector_id} requires handle.")
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.provider_config = provider_config
        self.client = client or XpozClient.from_provider_config(provider_config)
        self.warnings: list[PipelineSectionWarning] = []
        self.api_responses: list[ProviderApiResponse] = []

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        self.warnings = []
        self.api_responses = []
        _clear_provider_responses(self.client)
        handle = normalize_x_handle(self.collector_config.handle or "")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.collector_config.lookback_hours)
        try:
            author_posts = await self.client.twitter_posts_by_author(
                username=handle,
                limit=max(self.collector_config.limit, 1),
                fields=X_AUTHOR_FIELDS,
                force_latest=True,
            )
            author_posts = sorted(author_posts, key=x_post_sort_key, reverse=True)
            self.warnings.extend(
                _cap_warnings(
                    posts=author_posts,
                    limit=self.collector_config.limit,
                    cutoff=cutoff,
                    collector_id=self.collector_id,
                    source_label=f"X user @{handle}",
                    phase="collecting",
                )
            )
            status_ids = [_string_value(post, "id") for post in author_posts if _string_value(post, "id")]
            status_ids = _dedupe(status_ids)[: self.collector_config.limit]
            detail_posts = await self.client.twitter_posts_by_ids(
                post_ids=status_ids,
                fields=X_DETAIL_FIELDS,
                force_latest=True,
            )
            by_id = {_string_value(post, "id"): post for post in detail_posts if _string_value(post, "id")}
            author_by_id = {_string_value(post, "id"): post for post in author_posts if _string_value(post, "id")}
            merged = [
                {**author_by_id.get(status_id, {}), **by_id.get(status_id, {})}
                for status_id in status_ids
                if status_id in author_by_id or status_id in by_id
            ]
            merged = sorted(merged, key=x_post_sort_key, reverse=True)
            return [_raw_x_post_item(post, handle) for post in merged]
        finally:
            self.api_responses = _take_provider_responses(self.client)


class XpozRedditSubredditCollector:
    """Collect subreddit posts and optional comments through Xpoz."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        provider_config: XpozProviderConfig,
        client: XpozClient | None = None,
    ) -> None:
        if not collector_config.subreddit:
            raise ConfigurationError(f"Collector {collector_id} requires subreddit.")
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.provider_config = provider_config
        self.client = client or XpozClient.from_provider_config(provider_config)
        self.warnings: list[PipelineSectionWarning] = []
        self.api_responses: list[ProviderApiResponse] = []

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        self.warnings = []
        self.api_responses = []
        _clear_provider_responses(self.client)
        subreddit = normalize_subreddit(self.collector_config.subreddit or "")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.collector_config.lookback_hours)
        try:
            posts = await self.client.reddit_subreddit_posts(
                subreddit=subreddit,
                limit=self.collector_config.limit,
                fields=REDDIT_POST_FIELDS,
                force_latest=True,
            )
            posts = sorted(posts, key=reddit_post_sort_key, reverse=True)[: self.collector_config.limit]
            self.warnings.extend(
                _cap_warnings(
                    posts=posts,
                    limit=self.collector_config.limit,
                    cutoff=cutoff,
                    collector_id=self.collector_id,
                    source_label=f"r/{subreddit}",
                    phase="collecting",
                )
            )
            items: list[RawItem] = []
            for post in posts:
                post_item = _raw_reddit_post_item(post, subreddit)
                if post_item is None:
                    continue
                items.append(post_item)
                if self.collector_config.include_comments and self.collector_config.comments_per_post > 0:
                    items.extend(await self._collect_comments(post_item))
            return items
        finally:
            self.api_responses = _take_provider_responses(self.client)

    async def _collect_comments(self, post_item: RawItem) -> list[RawItem]:
        payload = await self.client.reddit_post_with_comments(
            post_id=post_item.source_id,
            limit=self.collector_config.comments_per_post,
            comment_fields=REDDIT_COMMENT_FIELDS,
            post_fields=REDDIT_POST_FIELDS,
            force_latest=True,
        )
        items: list[RawItem] = []
        for comment in payload["comments"][: self.collector_config.comments_per_post]:
            item = _raw_reddit_comment_item(comment, post_item)
            if item is not None:
                items.append(item)
        return items


def parse_mcp_response_text(text: str) -> Any:
    """Parse JSON or Server-Sent Event JSON from an MCP response body."""

    value = text.strip()
    if not value:
        return {}
    if value.startswith("data:") or "\ndata:" in value:
        payloads = []
        for line in value.splitlines():
            if line.startswith("data:"):
                payloads.append(line[5:].strip())
        value = "\n".join(payloads).strip()
    try:
        return json.loads(value)
    except ValueError as exc:
        raise XpozResponseError("Xpoz returned invalid MCP JSON.") from exc


def parse_xpoz_rows(text: str, *, preferred_prefix: str | None = None) -> list[dict[str, Any]]:
    """Parse Xpoz flattened table or YAML-like tool text into rows."""

    if preferred_prefix:
        rows = _parse_xpoz_table(text, preferred_prefix)
        if rows:
            return rows
    for prefix in ("results", "posts", "comments"):
        rows = _parse_xpoz_table(text, prefix)
        if rows:
            return rows
    return _parse_xpoz_list(text)


def _parse_xpoz_table(text: str, prefix: str) -> list[dict[str, Any]]:
    match = re.search(rf"{re.escape(prefix)}\[(\d+)\]\{{([^}}]+)\}}:", text)
    if not match:
        return []
    fields = [field.strip() for field in match.group(2).split(",")]
    rows: list[dict[str, Any]] = []
    for line in text[match.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[A-Za-z_]+:", stripped):
            break
        try:
            values = next(csv.reader([stripped]))
        except csv.Error:
            continue
        if len(values) == len(fields):
            rows.append(dict(zip(fields, values, strict=True)))
    return rows


def _parse_xpoz_list(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_key: str | None = None
    for line in text.splitlines():
        if re.match(r"\s*-\s+[A-Za-z0-9_]+:", line):
            if current:
                rows.append(current)
            current = {}
            line = re.sub(r"^\s*-\s+", "", line)
        if current is None:
            continue
        indexed = re.match(r"\s*([A-Za-z0-9_]+)\[(\d+)\]:\s*(.*)$", line)
        if indexed:
            key = indexed.group(1)
            current.setdefault(key, [])
            if isinstance(current[key], list):
                current[key].append(_parse_scalar(indexed.group(3)))
            last_key = key
            continue
        field = re.match(r"\s*([A-Za-z0-9_]+):\s*(.*)$", line)
        if field:
            key = field.group(1)
            current[key] = _parse_scalar(field.group(2))
            last_key = key
            continue
        array_item = re.match(r"\s*-\s*(.*)$", line)
        if array_item and last_key:
            current.setdefault(last_key, [])
            if isinstance(current[last_key], list):
                current[last_key].append(_parse_scalar(array_item.group(1)))
    if current:
        rows.append(current)
    return rows


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value.strip('"')


def _raw_x_post_item(post: Mapping[str, Any], handle: str) -> RawItem:
    status_id = _string_value(post, "id") or ""
    author = _string_value(post, "authorUsername") or handle
    url = f"https://x.com/{author}/status/{status_id}" if status_id else None
    return RawItem(
        source_id=status_id,
        source_type=X_SOURCE_TYPE,
        url=url,
        text=_string_value(post, "text") or "",
        metadata={
            "entity_type": "x_post",
            "handle": handle,
            "author_handle": author,
            "author_name": None,
            "posted_at_text": _timestamp_text(post),
            "reply_count": _int_value(post, "replyCount"),
            "repost_count": _int_value(post, "retweetCount"),
            "like_count": _int_value(post, "likeCount"),
            "quote_count": _int_value(post, "quoteCount"),
            "view_count": _int_value(post, "impressionCount"),
            "media": _extract_x_media(post),
            "raw": dict(post),
        },
    )


def _extract_x_media(post: Mapping[str, Any]) -> list[dict[str, Any]]:
    media_urls = _list_value(post.get("mediaUrls"))
    media: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in media_urls:
        clean = _usable_url(str(url))
        if not clean or clean in seen:
            continue
        seen.add(clean)
        media.append(
            {
                "media_key": media_key_from_url(clean),
                "media_type": _media_type_from_url(clean),
                "url": clean,
                "source_field": "mediaUrls",
                "raw": {"url": clean},
            }
        )
    return media


def _raw_reddit_post_item(post: Mapping[str, Any], subreddit: str) -> RawItem | None:
    post_id = _string_value(post, "id")
    if not post_id:
        return None
    subreddit_name = normalize_subreddit(_string_value(post, "subredditName", "subreddit") or subreddit)
    post_url = _string_value(post, "postUrl", "permalink")
    if post_url and post_url.startswith("/"):
        post_url = f"https://www.reddit.com{post_url}"
    if not post_url:
        post_url = f"https://reddit.com/r/{subreddit_name}/comments/{post_id}/"
    url = _string_value(post, "url") or post_url
    media = _extract_reddit_media(post)
    return RawItem(
        source_id=post_id,
        source_type=REDDIT_SOURCE_TYPE,
        url=post_url,
        text=_string_value(post, "selftext", "body", "text") or "",
        metadata={
            "entity_type": "reddit_post",
            "subreddit": subreddit_name,
            "fullname": _string_value(post, "name"),
            "title": _string_value(post, "title") or "",
            "author": _string_value(post, "authorUsername", "author"),
            "permalink": post_url,
            "score": _int_value(post, "score"),
            "ups": _int_value(post, "upvotes", "ups"),
            "upvote_ratio": _float_value(post, "upvoteRatio", "upvote_ratio"),
            "num_comments": _int_value(post, "commentsCount", "num_comments"),
            "thumbnail_url": _usable_url(_string_value(post, "thumbnail")),
            "created_at_text": _timestamp_text(post),
            "media": media,
            "raw": dict(post),
            "source_url": _usable_url(url),
        },
    )


def _raw_reddit_comment_item(comment: Mapping[str, Any], post_item: RawItem) -> RawItem | None:
    comment_id = _string_value(comment, "id")
    if not comment_id:
        return None
    body = _string_value(comment, "body", "text", "content") or ""
    return RawItem(
        source_id=f"{post_item.source_id}:{comment_id}",
        source_type=REDDIT_SOURCE_TYPE,
        url=post_item.url,
        text=body,
        metadata={
            "entity_type": "reddit_comment",
            "comment_id": comment_id,
            "post_id": _string_value(comment, "parentPostId") or post_item.source_id,
            "parent_id": _string_value(comment, "parentId"),
            "author": _string_value(comment, "authorUsername", "author"),
            "body": body,
            "score": _int_value(comment, "score"),
            "ups": _int_value(comment, "upvotes", "ups"),
            "created_at_text": _timestamp_text(comment),
            "depth": _int_value(comment, "depth"),
            "raw": dict(comment),
        },
    )


def _extract_reddit_media(post: Mapping[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, media_type in (("url", "image"), ("thumbnail", "thumbnail")):
        url = _usable_url(_string_value(post, key))
        if not url or url in seen:
            continue
        if key == "url" and not _looks_like_media_url(url):
            continue
        seen.add(url)
        media.append(
            {
                "media_type": media_type if media_type == "thumbnail" else _media_type_from_url(url),
                "url": url,
                "source_field": key,
                "raw": {"url": url},
            }
        )
    return media


def normalize_x_handle(handle: str) -> str:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        raise ConfigurationError("X handle cannot be empty.")
    return normalized


def normalize_subreddit(subreddit: str) -> str:
    normalized = subreddit.strip().strip("/").removeprefix("r/")
    if not normalized:
        raise ConfigurationError("Subreddit cannot be empty.")
    return normalized


def x_post_sort_key(post: Mapping[str, Any]) -> tuple[str, str]:
    timestamp = _timestamp_text(post) or snowflake_timestamp(_string_value(post, "id") or "") or ""
    return (timestamp, _string_value(post, "id") or "")


def reddit_post_sort_key(post: Mapping[str, Any]) -> tuple[str, str]:
    return (_timestamp_text(post) or "", _string_value(post, "id") or "")


def _cap_warnings(
    *,
    posts: list[Mapping[str, Any]],
    limit: int,
    cutoff: datetime,
    collector_id: str,
    source_label: str,
    phase: str,
) -> list[PipelineSectionWarning]:
    if len(posts) != limit or not posts:
        return []
    parsed = [_posted_at(post) for post in posts]
    timestamps = [timestamp for timestamp in parsed if timestamp is not None]
    oldest = min(timestamps, default=None)
    if oldest is None or oldest < cutoff:
        return []
    return [
        PipelineSectionWarning(
            section="collector",
            source_id=collector_id,
            phase=phase,
            message=(
                f"{source_label} returned {limit} posts and the oldest fetched post is still within "
                "the configured lookback window; increase the source fetch cap to reduce truncation risk."
            ),
        )
    ]


def snowflake_timestamp(status_id: str) -> str | None:
    try:
        milliseconds = (int(status_id) >> 22) + 1288834974657
    except ValueError:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _posted_at(payload: Mapping[str, Any]) -> datetime | None:
    timestamp = _timestamp_text(payload) or snowflake_timestamp(_string_value(payload, "id") or "")
    return parse_timestamp(timestamp)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", normalized):
        try:
            return datetime.fromtimestamp(float(normalized), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_text(payload: Mapping[str, Any]) -> str | None:
    created = _string_value(payload, "createdAt")
    if created:
        if re.fullmatch(r"\d+(\.\d+)?", created):
            try:
                return datetime.fromtimestamp(float(created), timezone.utc).isoformat().replace("+00:00", "Z")
            except (OverflowError, OSError, ValueError):
                return created
        return created
    return _string_value(payload, "createdAtDate", "created_at", "date")


def media_key_from_url(url: str) -> str | None:
    match = re.search(r"/media/([^.?/]+)", url)
    return match.group(1) if match else None


def _string_value(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "null":
            continue
        if isinstance(value, str):
            return html.unescape(value).replace("\\n", "\n")
        if isinstance(value, int | float | bool):
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


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", "null"):
        return []
    return [value]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _usable_url(value: str | None) -> str | None:
    if not value:
        return None
    url = html.unescape(value).replace("&amp;", "&")
    if url in {"self", "default", "nsfw", "spoiler", "None", "null"}:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


def _looks_like_media_url(url: str) -> bool:
    return bool(re.search(r"\.(jpg|jpeg|png|gif|webp|mp4|m3u8|mpd)(\?|$)", url, re.IGNORECASE))


def _media_type_from_url(url: str) -> str:
    if re.search(r"\.(mp4|m3u8|mpd)(\?|$)", url, re.IGNORECASE):
        return "video"
    if re.search(r"\.gif(\?|$)", url, re.IGNORECASE):
        return "gif"
    return "image"


def _clear_provider_responses(client: Any) -> None:
    if hasattr(client, "provider_responses"):
        client.provider_responses = []


def _take_provider_responses(client: Any) -> list[ProviderApiResponse]:
    take_responses = getattr(client, "take_provider_responses", None)
    if callable(take_responses):
        return take_responses()
    return []
