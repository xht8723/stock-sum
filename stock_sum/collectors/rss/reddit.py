"""Reddit RSS collector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from urllib.parse import quote, urlparse
import asyncio
import html
import re
import xml.etree.ElementTree as ET

import httpx

from stock_sum.config.models import CollectorConfig, RedditRssProviderConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.models import PipelineSectionWarning, ProviderApiResponse, RawItem
from stock_sum.collectors.api.xpoz import REDDIT_SOURCE_TYPE, normalize_subreddit

REDDIT_RSS_SOURCE_TYPE = "reddit_rss_subreddit"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
HTML_TAG_RE = re.compile(r"<[^>]+>")


class RedditRssError(StockSumError):
    """Base error for Reddit RSS failures."""


class RedditRssRetryableError(RedditRssError):
    """Raised for retryable Reddit RSS failures."""


@dataclass(frozen=True)
class RedditRssEntry:
    """Parsed Atom entry."""

    entry_id: str
    title: str
    link: str
    author: str | None
    published: str | None
    updated: str | None
    content_html: str
    raw: dict[str, Any]


class RedditRssSubredditCollector:
    """Collect subreddit posts and comments through Reddit RSS feeds."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        provider_config: RedditRssProviderConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not collector_config.subreddit:
            raise ConfigurationError(f"Collector {collector_id} requires subreddit.")
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.provider_config = provider_config
        self.transport = transport
        self.warnings: list[PipelineSectionWarning] = []
        self.api_responses: list[ProviderApiResponse] = []
        self._deadline: float | None = None

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        self.warnings = []
        self.api_responses = []
        self._deadline = monotonic() + self.provider_config.total_timeout_seconds
        subreddit = normalize_subreddit(self.collector_config.subreddit or "")
        entries = await self._fetch_listing_entries(subreddit)

        items: list[RawItem] = []
        post_items: list[RawItem] = []
        for entry in entries[: self.provider_config.listing_limit]:
            post_item = _raw_post_item(entry, subreddit)
            if post_item is None:
                self._warning("parsing", f"Skipped malformed Reddit RSS post entry: {entry.link or entry.entry_id}")
                continue
            items.append(post_item)
            post_items.append(post_item)

        if self.collector_config.include_comments:
            for post_item in post_items:
                if self._deadline_reached():
                    self._warning("collecting", "Reddit RSS total timeout reached; stopped fetching comment feeds.")
                    break
                comments = await self._fetch_comment_items(post_item, subreddit)
                items.extend(comments)
        return items

    async def _fetch_listing_entries(self, subreddit: str) -> list[RedditRssEntry]:
        url = self._listing_url(subreddit)
        text = await self._fetch_text(url, tool_name="subreddit_listing_rss", request_arguments={"subreddit": subreddit, "limit": self.provider_config.listing_limit})
        entries = parse_reddit_rss_entries(text)
        self._record_response(
            tool_name="subreddit_listing_rss",
            request_arguments={"subreddit": subreddit, "limit": self.provider_config.listing_limit, "url": url},
            raw_response_text=text,
            entries=entries,
        )
        return entries

    async def _fetch_comment_items(self, post_item: RawItem, subreddit: str) -> list[RawItem]:
        post_id = post_item.source_id
        url = _comment_feed_url(post_item.url or "", limit=self.provider_config.comment_feed_limit)
        if not url:
            self._warning("collecting", f"Could not build Reddit RSS comment feed URL for post {post_id}.")
            return []
        try:
            text = await self._fetch_text(
                url,
                tool_name="post_comments_rss",
                request_arguments={"subreddit": subreddit, "post_id": post_id, "limit": self.provider_config.comment_feed_limit},
            )
        except RedditRssError as exc:
            self._warning("collecting", f"Reddit RSS comments failed for post {post_id}: {exc}")
            return []
        entries = parse_reddit_rss_entries(text)
        self._record_response(
            tool_name="post_comments_rss",
            request_arguments={"subreddit": subreddit, "post_id": post_id, "limit": self.provider_config.comment_feed_limit, "url": url},
            raw_response_text=text,
            entries=entries,
        )
        comments: list[RawItem] = []
        for entry in entries:
            if _entry_kind(entry) != "comment":
                continue
            item = _raw_comment_item(entry, post_item)
            if item is not None:
                comments.append(item)
        return comments

    async def _fetch_text(self, url: str, *, tool_name: str, request_arguments: dict[str, Any]) -> str:
        attempt = 0
        while True:
            if self._deadline_reached():
                raise RedditRssRetryableError("Reddit RSS total timeout reached.")
            try:
                async with httpx.AsyncClient(timeout=self.provider_config.timeout_seconds, transport=self.transport) as client:
                    response = await client.get(url, headers={"User-Agent": self.provider_config.user_agent, "Accept": "application/atom+xml, application/xml, text/xml"})
            except httpx.TransportError as exc:
                error: Exception = RedditRssRetryableError(f"transport failed: {exc}")
            else:
                if response.status_code == 429:
                    error = RedditRssRetryableError("HTTP 429 Too Many Requests")
                elif response.status_code >= 500:
                    error = RedditRssRetryableError(f"HTTP {response.status_code}")
                elif response.status_code >= 400:
                    raise RedditRssError(f"HTTP {response.status_code}")
                else:
                    return response.text

            if attempt >= self.provider_config.max_retries:
                raise error
            attempt += 1
            if self.provider_config.retry_delay_seconds > 0:
                await asyncio.sleep(min(self.provider_config.retry_delay_seconds, self._remaining_timeout()))

    def _listing_url(self, subreddit: str) -> str:
        return f"{self.provider_config.base_url.rstrip('/')}/r/{quote(subreddit, safe='')}.rss?limit={self.provider_config.listing_limit}"

    def _deadline_reached(self) -> bool:
        return self._deadline is not None and monotonic() >= self._deadline

    def _remaining_timeout(self) -> float:
        if self._deadline is None:
            return float(self.provider_config.retry_delay_seconds)
        return max(0.0, self._deadline - monotonic())

    def _warning(self, phase: str, message: str) -> None:
        self.warnings.append(
            PipelineSectionWarning(
                section="collector",
                source_id=self.collector_id,
                phase=phase,
                message=message,
            )
        )

    def _record_response(
        self,
        *,
        tool_name: str,
        request_arguments: dict[str, Any],
        raw_response_text: str,
        entries: list[RedditRssEntry],
    ) -> None:
        self.api_responses.append(
            ProviderApiResponse(
                provider="reddit_rss",
                tool_name=tool_name,
                request_arguments=request_arguments,
                raw_response_text=raw_response_text,
                parsed_rows=[entry.raw for entry in entries],
                row_count=len(entries),
            )
        )


def parse_reddit_rss_entries(text: str) -> list[RedditRssEntry]:
    """Parse Reddit Atom XML into entries."""

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RedditRssError("Reddit RSS returned malformed XML.") from exc
    entries: list[RedditRssEntry] = []
    for element in root.findall("atom:entry", ATOM_NS):
        entry_id = _element_text(element, "id")
        title = _element_text(element, "title") or ""
        link = _entry_link(element)
        author = _entry_author(element)
        published = _element_text(element, "published")
        updated = _element_text(element, "updated")
        content_html = _element_text(element, "content") or ""
        raw = {
            "id": entry_id,
            "title": title,
            "link": link,
            "author": author,
            "published": published,
            "updated": updated,
            "content": content_html,
        }
        entries.append(
            RedditRssEntry(
                entry_id=entry_id or link,
                title=title,
                link=link,
                author=author,
                published=published,
                updated=updated,
                content_html=content_html,
                raw=raw,
            )
        )
    return entries


def _raw_post_item(entry: RedditRssEntry, subreddit: str) -> RawItem | None:
    post_id = _post_id_from_entry(entry)
    if not post_id:
        return None
    permalink = _canonical_reddit_url(entry.link) or f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    return RawItem(
        source_id=post_id,
        source_type=REDDIT_RSS_SOURCE_TYPE,
        url=permalink,
        text=_entry_text(entry),
        metadata={
            "entity_type": "reddit_post",
            "subreddit": subreddit,
            "fullname": f"t3_{post_id}",
            "title": entry.title,
            "author": _normalize_author(entry.author),
            "permalink": permalink,
            "score": None,
            "ups": None,
            "upvote_ratio": None,
            "num_comments": None,
            "thumbnail_url": None,
            "created_at_text": _entry_timestamp(entry),
            "media": [],
            "raw": entry.raw,
            "source_url": permalink,
            "provider": "reddit_rss",
        },
    )


def _raw_comment_item(entry: RedditRssEntry, post_item: RawItem) -> RawItem | None:
    comment_id = _comment_id_from_entry(entry)
    if not comment_id:
        return None
    body = _entry_text(entry)
    return RawItem(
        source_id=f"{post_item.source_id}:{comment_id}",
        source_type=REDDIT_RSS_SOURCE_TYPE,
        url=_canonical_reddit_url(entry.link) or post_item.url,
        text=body,
        metadata={
            "entity_type": "reddit_comment",
            "comment_id": comment_id,
            "post_id": post_item.source_id,
            "parent_id": None,
            "author": _normalize_author(entry.author),
            "body": body,
            "score": None,
            "ups": None,
            "created_at_text": _entry_timestamp(entry),
            "depth": None,
            "raw": entry.raw,
            "provider": "reddit_rss",
        },
    )


def _element_text(element: ET.Element, name: str) -> str | None:
    child = element.find(f"atom:{name}", ATOM_NS)
    if child is None or child.text is None:
        return None
    return html.unescape(child.text.strip())


def _entry_link(element: ET.Element) -> str:
    for child in element.findall("atom:link", ATOM_NS):
        href = child.attrib.get("href")
        if href:
            return html.unescape(href)
    return ""


def _entry_author(element: ET.Element) -> str | None:
    author = element.find("atom:author", ATOM_NS)
    if author is None:
        return None
    name = author.find("atom:name", ATOM_NS)
    if name is None or name.text is None:
        return None
    return html.unescape(name.text.strip())


def _entry_kind(entry: RedditRssEntry) -> str:
    value = entry.entry_id or ""
    if "t1_" in value:
        return "comment"
    if "t3_" in value:
        return "post"
    if "/comments/" in entry.link and "/comment/" in entry.link:
        return "comment"
    return "post"


def _post_id_from_entry(entry: RedditRssEntry) -> str | None:
    match = re.search(r"\bt3_([A-Za-z0-9]+)\b", entry.entry_id)
    if match:
        return match.group(1)
    match = re.search(r"/comments/([A-Za-z0-9]+)/", entry.link)
    return match.group(1) if match else None


def _comment_id_from_entry(entry: RedditRssEntry) -> str | None:
    match = re.search(r"\bt1_([A-Za-z0-9]+)\b", entry.entry_id)
    if match:
        return match.group(1)
    match = re.search(r"/comment/([A-Za-z0-9]+)/?", entry.link)
    return match.group(1) if match else None


def _entry_timestamp(entry: RedditRssEntry) -> str | None:
    return entry.published or entry.updated


def _entry_text(entry: RedditRssEntry) -> str:
    value = html.unescape(entry.content_html or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = HTML_TAG_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalize_author(value: str | None) -> str | None:
    if not value:
        return None
    author = value.strip()
    return author.removeprefix("/u/")


def _canonical_reddit_url(url: str) -> str | None:
    if not url:
        return None
    clean = html.unescape(url)
    if clean.startswith("/"):
        return f"https://www.reddit.com{clean}"
    return clean


def _comment_feed_url(post_url: str, *, limit: int) -> str | None:
    canonical = _canonical_reddit_url(post_url)
    if not canonical:
        return None
    parsed = urlparse(canonical)
    path = parsed.path.rstrip("/")
    if "/comments/" not in path:
        return None
    return f"https://www.reddit.com{path}.rss?limit={limit}"
