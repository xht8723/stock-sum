"""Public headless Playwright collector for X user timelines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from stock_sum.config.models import CollectorConfig, PlaywrightConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.models import RawItem

X_SOURCE_TYPE = "x_user_timeline"


class XTimelineUnavailableError(StockSumError):
    """Raised when a public X timeline cannot be collected."""


@dataclass(frozen=True)
class ExtractedTweet:
    """Normalized tweet data extracted from one X article."""

    status_id: str
    url: str
    text: str
    posted_at_text: str | None
    author_handle: str | None
    author_name: str | None
    media: list[dict[str, Any]]
    raw: dict[str, Any]


class PlaywrightXUserTimelineCollector:
    """Collect recent public X posts from a user timeline."""

    def __init__(
        self,
        *,
        collector_id: str,
        collector_config: CollectorConfig,
        playwright_config: PlaywrightConfig,
    ) -> None:
        if not collector_config.handle:
            raise ConfigurationError(f"Collector {collector_id} requires handle.")
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.playwright_config = playwright_config

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Open the public X profile and collect recent timeline posts."""

        handle = normalize_handle(self.collector_config.handle or "")
        async with async_playwright() as playwright:
            browser_type = getattr(playwright, self.playwright_config.browser)
            launch_options: dict[str, Any] = {"headless": self.playwright_config.headless}
            if self.playwright_config.channel:
                launch_options["channel"] = self.playwright_config.channel
            browser = await browser_type.launch(**launch_options)
            try:
                page = await browser.new_page()
                page.set_default_timeout(self.playwright_config.timeout_seconds * 1000)
                page.set_default_navigation_timeout(self.playwright_config.timeout_seconds * 1000)
                return await self._collect_page(page, handle)
            finally:
                await browser.close()

    async def _collect_page(self, page: Page, handle: str) -> list[RawItem]:
        target_url = f"{self.playwright_config.x.base_url.rstrip('/')}/{handle}"
        try:
            await page.goto(target_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(self.playwright_config.x.page_settle_ms)
        except PlaywrightError as exc:
            raise XTimelineUnavailableError(f"X timeline navigation failed for @{handle}: {exc}") from exc

        reason = await detect_blocked_timeline(page)
        if reason:
            raise XTimelineUnavailableError(reason)

        tweets: dict[str, ExtractedTweet] = {}
        optional_timeout_ms = min(1000, self.playwright_config.x.selector_timeout_seconds * 1000)
        for scroll_index in range(self.playwright_config.x.max_scrolls + 1):
            await collect_visible_tweets(
                page,
                handle=handle,
                tweets=tweets,
                optional_timeout_ms=optional_timeout_ms,
            )
            if len(tweets) >= self.collector_config.limit:
                break
            if scroll_index >= self.playwright_config.x.max_scrolls:
                break
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(self.playwright_config.x.scroll_pause_ms)

        if not tweets:
            raise XTimelineUnavailableError(
                f"X timeline for @{handle} did not expose public tweet articles. "
                "The page may be login-gated, blocked, empty, or selectors changed."
            )

        extracted = sorted(tweets.values(), key=lambda tweet: x_post_sort_key(tweet.status_id), reverse=True)
        return [tweet_to_raw_item(tweet, handle=handle) for tweet in extracted[: self.collector_config.limit]]


async def detect_blocked_timeline(page: Page) -> str | None:
    """Return a human-readable block reason if the loaded page is not usable."""

    try:
        title = await page.title()
    except PlaywrightError:
        title = ""
    body = await optional_inner_text(page.locator("body").first, timeout_ms=1000)
    haystack = f"{title}\n{body}".lower()
    blocked_markers = (
        "log in to x",
        "sign in to x",
        "create your account",
        "something went wrong",
        "temporarily restricted",
        "rate limit",
        "try again",
        "unusual traffic",
    )
    for marker in blocked_markers:
        if marker in haystack:
            return f"X timeline is not publicly available in headless mode: {marker}."
    return None


async def collect_visible_tweets(
    page: Page,
    *,
    handle: str,
    tweets: dict[str, ExtractedTweet],
    optional_timeout_ms: int,
) -> None:
    """Extract tweet articles currently visible in the page."""

    articles = page.locator('article[data-testid="tweet"], article')
    count = await articles.count()
    for index in range(count):
        article = articles.nth(index)
        tweet = await extract_tweet_article(article, handle=handle, optional_timeout_ms=optional_timeout_ms)
        if tweet is not None:
            tweets[tweet.status_id] = tweet


async def extract_tweet_article(
    article: Locator,
    *,
    handle: str,
    optional_timeout_ms: int = 1000,
) -> ExtractedTweet | None:
    """Extract one tweet from a Playwright article locator."""

    link = article.locator('a[href*="/status/"]').first
    href = await optional_get_attribute(link, "href", timeout_ms=optional_timeout_ms)
    if not href:
        return None
    status_id = status_id_from_url(href)
    if not status_id:
        return None

    url = normalize_x_url(href)
    article_text = clean_article_text(await optional_inner_text(article, timeout_ms=optional_timeout_ms))
    if not article_text and not url:
        return None

    time_locator = article.locator("time").first
    posted_at_text = await optional_get_attribute(time_locator, "datetime", timeout_ms=optional_timeout_ms)
    if not posted_at_text:
        posted_at_text = await optional_inner_text(time_locator, timeout_ms=optional_timeout_ms)
    if not posted_at_text:
        posted_at_text = date_from_article_text(article_text)
    author_handle, author_name = author_from_text(article_text, fallback_handle=handle)
    text = post_body_from_article_text(article_text, author_handle=author_handle or handle)
    media = await extract_article_media(article, optional_timeout_ms=optional_timeout_ms)
    return ExtractedTweet(
        status_id=status_id,
        url=url,
        text=text,
        posted_at_text=posted_at_text,
        author_handle=author_handle,
        author_name=author_name,
        media=media,
        raw={
            "href": href,
            "status_id": status_id,
            "text": text,
            "posted_at_text": posted_at_text,
            "media_count": len(media),
        },
    )


async def extract_article_media(article: Locator, *, optional_timeout_ms: int) -> list[dict[str, Any]]:
    """Extract post media URLs from one article."""

    media: list[dict[str, Any]] = []
    seen: set[str] = set()
    images = article.locator('img[src*="pbs.twimg.com/media"], img[src*="twimg.com/media"]')
    count = await safe_count(images)
    for index in range(count):
        image = images.nth(index)
        src = await optional_get_attribute(image, "src", timeout_ms=optional_timeout_ms)
        if not src or src in seen:
            continue
        seen.add(src)
        media.append(
            {
                "media_key": media_key_from_url(src),
                "media_type": "photo",
                "url": src,
                "alt_text": await optional_get_attribute(image, "alt", timeout_ms=optional_timeout_ms),
                "source_path": "article.img",
            }
        )
    return media


def tweet_to_raw_item(tweet: ExtractedTweet, *, handle: str) -> RawItem:
    """Convert extracted tweet data to the collector interface model."""

    return RawItem(
        source_id=tweet.status_id,
        source_type=X_SOURCE_TYPE,
        url=tweet.url,
        text=tweet.text,
        metadata={
            "entity_type": "x_post",
            "handle": handle,
            "author_handle": tweet.author_handle,
            "author_name": tweet.author_name,
            "posted_at_text": tweet.posted_at_text,
            "reply_count": None,
            "repost_count": None,
            "like_count": None,
            "quote_count": None,
            "view_count": None,
            "media": tweet.media,
            "raw": tweet.raw,
        },
    )


async def optional_get_attribute(locator: Locator, name: str, *, timeout_ms: int) -> str | None:
    try:
        return await locator.get_attribute(name, timeout=timeout_ms)
    except (PlaywrightError, PlaywrightTimeoutError):
        return None


async def optional_inner_text(locator: Locator, *, timeout_ms: int) -> str:
    try:
        return await locator.inner_text(timeout=timeout_ms)
    except (PlaywrightError, PlaywrightTimeoutError):
        return ""


async def safe_count(locator: Locator) -> int:
    try:
        return await locator.count()
    except PlaywrightError:
        return 0


def normalize_handle(handle: str) -> str:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        raise ConfigurationError("X handle cannot be empty.")
    return normalized


def normalize_x_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"https://x.com{href}"
    return f"https://x.com/{href}"


def status_id_from_url(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", url)
    if not match:
        return None
    return match.group(1)


def media_key_from_url(url: str) -> str | None:
    match = re.search(r"/media/([^.?/]+)", url)
    if not match:
        return None
    return match.group(1)


def x_post_sort_key(status_id: str) -> int:
    try:
        return int(status_id)
    except ValueError:
        return 0


def clean_article_text(value: str) -> str:
    lines = [line.strip() for line in value.replace("\r", "\n").split("\n")]
    cleaned = [line for line in lines if line and line not in {"Ad", "Promoted"}]
    return "\n".join(cleaned)


def date_from_article_text(text: str) -> str | None:
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", line):
            return line
        if re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", line):
            return line
    return None


def post_body_from_article_text(text: str, *, author_handle: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = 0
    for index, line in enumerate(lines[:10]):
        if line == f"@{author_handle}" or line.startswith("@"):
            start = index + 1
            break
    body: list[str] = []
    for line in lines[start:]:
        line = line.replace("Show more", "").replace("显示更多", "").strip()
        if not line:
            continue
        if line in {"显示翻译", "This post is unavailable."}:
            continue
        if line.startswith("Replying to "):
            continue
        if date_from_article_text(line):
            continue
        body.append(line)
    return "\n".join(body)


def author_from_text(text: str, *, fallback_handle: str) -> tuple[str | None, str | None]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    handle = fallback_handle
    name: str | None = None
    for index, line in enumerate(lines[:8]):
        if line.startswith("@"):
            handle = line.lstrip("@")
            if index > 0:
                name = lines[index - 1]
            break
    return handle, name
