"""Nitter RSS collector for X user timelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any
from urllib.parse import quote, urlparse
import asyncio
import html
import re
import xml.etree.ElementTree as ET

import httpx

from stock_sum.collectors.api.xpoz import media_key_from_url, normalize_x_handle
from stock_sum.config.models import CollectorConfig, NitterRssProviderConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.models import PipelineSectionWarning, ProviderApiResponse, RawItem

X_RSS_SOURCE_TYPE = "x_rss_user_timeline"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
HTML_TAG_RE = re.compile(r"<[^>]+>")
MEDIA_URL_RE = re.compile(r"https?://[^\s\"'<>]+?\.(?:jpg|jpeg|png|gif|webp|mp4)(?:\?[^\s\"'<>]+)?", re.IGNORECASE)


class NitterRssError(StockSumError):
    """Base error for Nitter RSS failures."""


class NitterRssRetryableError(NitterRssError):
    """Raised for retryable Nitter RSS failures."""


@dataclass(frozen=True)
class NitterRssEntry:
    """Parsed Nitter RSS/Atom entry."""

    entry_id: str
    title: str
    link: str
    author: str | None
    published: str | None
    content_html: str
    enclosures: list[str]
    raw: dict[str, Any]


class NitterRssXUserTimelineCollector:
    """Collect X user timeline posts through a Nitter RSS feed."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        provider_config: NitterRssProviderConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not collector_config.handle:
            raise ConfigurationError(f"Collector {collector_id} requires handle.")
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
        handle = normalize_x_handle(self.collector_config.handle or "")
        entries = await self._fetch_entries(handle)

        items: list[RawItem] = []
        for entry in entries[: self.provider_config.listing_limit]:
            item = _raw_x_rss_item(entry, handle)
            if item is None:
                self._warning("parsing", f"Skipped malformed Nitter RSS item: {entry.link or entry.entry_id}")
                continue
            items.append(item)
        return sorted(items, key=lambda item: (item.metadata.get("posted_at_text") or "", item.source_id), reverse=True)

    async def _fetch_entries(self, handle: str) -> list[NitterRssEntry]:
        url = self._feed_url(handle)
        text = await self._fetch_text(url)
        entries = parse_nitter_rss_entries(text)
        self.api_responses.append(
            ProviderApiResponse(
                provider="nitter_rss",
                tool_name="user_timeline_rss",
                request_arguments={"handle": handle, "limit": self.provider_config.listing_limit, "url": url},
                raw_response_text=text,
                parsed_rows=[entry.raw for entry in entries],
                row_count=len(entries),
            )
        )
        return entries

    async def _fetch_text(self, url: str) -> str:
        attempt = 0
        while True:
            if self._deadline_reached():
                raise NitterRssRetryableError("Nitter RSS total timeout reached.")
            try:
                async with httpx.AsyncClient(timeout=self.provider_config.timeout_seconds, transport=self.transport) as client:
                    response = await client.get(
                        url,
                        headers={
                            "User-Agent": self.provider_config.user_agent,
                            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                        },
                    )
            except httpx.TransportError as exc:
                error: Exception = NitterRssRetryableError(f"transport failed: {exc}")
            else:
                if response.status_code == 429:
                    error = NitterRssRetryableError("HTTP 429 Too Many Requests")
                elif response.status_code >= 500:
                    error = NitterRssRetryableError(f"HTTP {response.status_code}")
                elif response.status_code >= 400:
                    raise NitterRssError(f"HTTP {response.status_code}")
                else:
                    return response.text

            if attempt >= self.provider_config.max_retries:
                raise error
            attempt += 1
            if self.provider_config.retry_delay_seconds > 0:
                await asyncio.sleep(min(self.provider_config.retry_delay_seconds, self._remaining_timeout()))

    def _feed_url(self, handle: str) -> str:
        return f"{self.provider_config.base_url.rstrip('/')}/{quote(handle, safe='')}/rss"

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


def parse_nitter_rss_entries(text: str) -> list[NitterRssEntry]:
    """Parse Nitter RSS/Atom XML into entries."""

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise NitterRssError("Nitter RSS returned malformed XML.") from exc

    if _local_name(root.tag) == "feed":
        return [_atom_entry(element) for element in root.findall("atom:entry", ATOM_NS)]
    channel = next((child for child in root if _local_name(child.tag) == "channel"), root)
    return [_rss_item(element) for element in channel if _local_name(element.tag) == "item"]


def _rss_item(element: ET.Element) -> NitterRssEntry:
    title = _child_text(element, "title") or ""
    link = _child_text(element, "link") or ""
    guid = _child_text(element, "guid") or link
    author = _child_text(element, "creator") or _child_text(element, "author")
    published = _child_text(element, "pubDate") or _child_text(element, "date")
    content_html = _child_text(element, "description") or _child_text(element, "encoded") or ""
    enclosures = _enclosure_urls(element)
    return NitterRssEntry(
        entry_id=guid,
        title=title,
        link=link,
        author=author,
        published=published,
        content_html=content_html,
        enclosures=enclosures,
        raw={
            "id": guid,
            "title": title,
            "link": link,
            "author": author,
            "published": published,
            "content": content_html,
            "enclosures": enclosures,
        },
    )


def _atom_entry(element: ET.Element) -> NitterRssEntry:
    title = _atom_text(element, "title") or ""
    link = _atom_link(element)
    entry_id = _atom_text(element, "id") or link
    author = _atom_author(element)
    published = _atom_text(element, "published") or _atom_text(element, "updated")
    content_html = _atom_text(element, "content") or _atom_text(element, "summary") or ""
    enclosures = _enclosure_urls(element)
    return NitterRssEntry(
        entry_id=entry_id,
        title=title,
        link=link,
        author=author,
        published=published,
        content_html=content_html,
        enclosures=enclosures,
        raw={
            "id": entry_id,
            "title": title,
            "link": link,
            "author": author,
            "published": published,
            "content": content_html,
            "enclosures": enclosures,
        },
    )


def _raw_x_rss_item(entry: NitterRssEntry, handle: str) -> RawItem | None:
    status_id = _status_id(entry)
    if not status_id:
        return None
    author = _entry_author_handle(entry, handle)
    url = f"https://x.com/{author}/status/{status_id}"
    media = _extract_media(entry)
    return RawItem(
        source_id=status_id,
        source_type=X_RSS_SOURCE_TYPE,
        url=url,
        text=_entry_text(entry),
        metadata={
            "entity_type": "x_post",
            "handle": handle,
            "author_handle": author,
            "author_name": None,
            "posted_at_text": _normalized_timestamp(entry.published),
            "reply_count": None,
            "repost_count": None,
            "like_count": None,
            "quote_count": None,
            "view_count": None,
            "media": media,
            "raw": entry.raw,
            "provider": "nitter_rss",
            "source_url": entry.link,
        },
    )


def _status_id(entry: NitterRssEntry) -> str | None:
    for value in (entry.link, entry.entry_id):
        match = re.search(r"/status(?:es)?/(\d+)", value or "")
        if match:
            return match.group(1)
    return None


def _entry_author_handle(entry: NitterRssEntry, fallback: str) -> str:
    for value in (entry.author, entry.link, entry.entry_id):
        handle = _handle_from_value(value or "")
        if handle:
            return handle
    return fallback


def _handle_from_value(value: str) -> str | None:
    value = html.unescape(value).strip()
    if not value:
        return None
    if value.startswith("@"):
        return value.lstrip("@").split()[0]
    parsed = urlparse(value)
    if parsed.path:
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0] not in {"i", "search"}:
            return parts[0].lstrip("@")
    match = re.search(r"@([A-Za-z0-9_]{1,20})", value)
    return match.group(1) if match else None


def _entry_text(entry: NitterRssEntry) -> str:
    content = _strip_html(entry.content_html)
    if content:
        return content
    title = entry.title.strip()
    if ":" in title:
        return title.split(":", 1)[1].strip()
    return title


def _strip_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = HTML_TAG_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_media(entry: NitterRssEntry) -> list[dict[str, Any]]:
    seen: set[str] = set()
    media: list[dict[str, Any]] = []
    for url in [*entry.enclosures, *MEDIA_URL_RE.findall(entry.content_html)]:
        clean = _usable_media_url(url)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        media.append(
            {
                "media_key": media_key_from_url(clean),
                "media_type": _media_type_from_url(clean),
                "url": clean,
                "source_field": "rss",
                "raw": {"url": clean},
            }
        )
    return media


def _normalized_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat()


def _usable_media_url(value: str | None) -> str | None:
    if not value:
        return None
    url = html.unescape(value).replace("&amp;", "&").strip()
    if not url.startswith(("http://", "https://")):
        return None
    return url


def _media_type_from_url(url: str) -> str:
    if re.search(r"\.mp4(\?|$)", url, re.IGNORECASE):
        return "video"
    if re.search(r"\.gif(\?|$)", url, re.IGNORECASE):
        return "gif"
    return "image"


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return html.unescape((child.text or "").strip()) or None
    return None


def _atom_text(element: ET.Element, name: str) -> str | None:
    child = element.find(f"atom:{name}", ATOM_NS)
    if child is None or child.text is None:
        return None
    return html.unescape(child.text.strip()) or None


def _atom_link(element: ET.Element) -> str:
    for child in element.findall("atom:link", ATOM_NS):
        href = child.attrib.get("href")
        if href:
            return html.unescape(href)
    return ""


def _atom_author(element: ET.Element) -> str | None:
    author = element.find("atom:author", ATOM_NS)
    if author is None:
        return None
    name = author.find("atom:name", ATOM_NS)
    if name is None or name.text is None:
        return None
    return html.unescape(name.text.strip())


def _enclosure_urls(element: ET.Element) -> list[str]:
    urls: list[str] = []
    for child in element.iter():
        tag = _local_name(child.tag)
        if tag in {"enclosure", "content", "thumbnail"}:
            url = child.attrib.get("url")
            if url:
                urls.append(html.unescape(url))
    return urls


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
