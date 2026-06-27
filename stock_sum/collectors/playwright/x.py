"""Playwright collector for X user timelines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin

from stock_sum.collectors.base import Collector
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import StockSumError
from stock_sum.core.models import RawItem

STATUS_URL_PATTERN = re.compile(r"/(?:i/web/)?status/(\d+)")
LOGIN_TEXT_PATTERNS = (
    "Log in to X",
    "Sign in to X",
    "Don\u2019t miss what\u2019s happening",
    "Don\u2019t miss what\u2019s happening",
    "See what\u2019s happening",
)
BLOCKED_TEXT_PATTERNS = (
    "Something went wrong",
    "Rate limit exceeded",
    "Try reloading",
)
PROMOTED_MARKERS = ("Promoted",)
PINNED_MARKERS = ("Pinned", "Pinned post")
PRIMARY_POST_SELECTOR = 'article[data-testid="tweet"]'
FALLBACK_POST_SELECTOR = "article"
POST_LINK_SELECTOR = 'a[href*="/status/"]'


class XScrapeError(StockSumError):
    """Raised when X timeline scraping fails."""


class XAuthenticationRequired(XScrapeError):
    """Raised when X requires a logged-in browser session."""


@dataclass(frozen=True)
class XPostData:
    """Parsed X post data before conversion to a RawItem."""

    status_id: str
    url: str
    text: str
    author: str | None
    timestamp: str | None
    handle: str


def normalize_handle(handle: str) -> str:
    """Normalize an X handle for URL construction and metadata."""

    return handle.strip().removeprefix("@")


def extract_status_id(url: str | None) -> str | None:
    """Extract an X status id from a URL."""

    if not url:
        return None
    match = STATUS_URL_PATTERN.search(url)
    return match.group(1) if match else None


def is_login_or_blocked_text(text: str) -> bool:
    """Return whether page text indicates login, rate limiting, or blocking."""

    return any(marker in text for marker in LOGIN_TEXT_PATTERNS + BLOCKED_TEXT_PATTERNS)


def should_skip_post(text: str, *, include_pinned: bool = False) -> bool:
    """Return whether a parsed article should be skipped."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(marker in lines for marker in PROMOTED_MARKERS):
        return True
    if not include_pinned and any(marker in lines for marker in PINNED_MARKERS):
        return True
    return False


def post_data_to_raw_item(post: XPostData) -> RawItem:
    """Convert parsed X post data to the shared RawItem model."""

    return RawItem(
        source_id=post.status_id,
        source_type="x_user_timeline",
        url=post.url,
        text=post.text,
        metadata={
            "platform": "x",
            "handle": post.handle,
            "author": post.author,
            "timestamp": post.timestamp,
            "status_id": post.status_id,
        },
    )


def _profile_exists(user_data_dir: str | Path) -> bool:
    path = Path(user_data_dir)
    return path.exists() and any(path.iterdir())


def _launch_options(*, user_data_dir: str | Path, headless: bool, channel: str = "") -> dict[str, Any]:
    options: dict[str, Any] = {
        "user_data_dir": str(user_data_dir),
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
    }
    if channel:
        options["channel"] = channel
    return options


async def _safe_text(locator: Any, *, timeout_ms: int = 1000) -> str:
    try:
        return (await locator.text_content(timeout=timeout_ms)) or ""
    except Exception:
        return ""


async def _safe_attribute(locator: Any, name: str, *, timeout_ms: int = 1000) -> str | None:
    try:
        return await locator.get_attribute(name, timeout=timeout_ms)
    except Exception:
        return None


async def _extract_post_from_article(article: Any, *, handle: str, base_url: str, include_pinned: bool) -> XPostData | None:
    text = (await _safe_text(article)).strip()
    if not text or should_skip_post(text, include_pinned=include_pinned):
        return None

    link_locator = article.locator(POST_LINK_SELECTOR).first
    href = await _safe_attribute(link_locator, "href")
    if not href:
        return None
    url = urljoin(base_url, href)
    status_id = extract_status_id(url)
    if not status_id:
        return None

    time_locator = article.locator("time").first
    timestamp = await _safe_attribute(time_locator, "datetime")
    author = (await _safe_text(article.locator('[data-testid="User-Name"]').first)).strip() or None

    return XPostData(status_id=status_id, url=url, text=text, author=author, timestamp=timestamp, handle=handle)


async def _extract_public_posts_from_page(page: Any, *, handle: str, base_url: str) -> list[XPostData]:
    raw_posts = await page.evaluate(
        """
        ({ handle, baseUrl }) => {
            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const posts = [];
            const seen = new Set();
            const links = Array.from(document.querySelectorAll('a[href*="/status/"]'));

            for (const link of links) {
                const href = link.getAttribute("href");
                if (!href) continue;

                const url = new URL(href, baseUrl);
                const match = url.pathname.match(new RegExp(`/${handle}/status/(\\\\d+)$`));
                if (!match) continue;

                const statusId = match[1];
                if (seen.has(statusId)) continue;

                const dateText = clean(link.innerText || link.textContent);
                if (!dateText) continue;

                let node = link.parentElement;
                let containerText = "";
                for (let depth = 0; node && depth < 8; depth += 1) {
                    const text = clean(node.innerText || node.textContent);
                    if (text.includes(`@${handle}`) && text.includes(dateText) && text.length > 60) {
                        containerText = text;
                        break;
                    }
                    node = node.parentElement;
                }
                if (!containerText) continue;

                const dateIndex = containerText.indexOf(dateText);
                const author = dateIndex >= 0 ? containerText.slice(0, dateIndex).trim() : "";
                const body = dateIndex >= 0 ? containerText.slice(dateIndex + dateText.length).trim() : containerText;
                if (!body) continue;

                seen.add(statusId);
                posts.push({
                    status_id: statusId,
                    url: url.toString(),
                    text: body,
                    author,
                    timestamp: dateText,
                    handle,
                });
            }
            return posts;
        }
        """,
        {"handle": handle, "baseUrl": base_url},
    )
    return [XPostData(**post) for post in raw_posts]


async def _page_text(page: Any) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


async def _ensure_timeline_or_raise(page: Any, *, selector_timeout_ms: int) -> None:
    try:
        await page.locator(f"{PRIMARY_POST_SELECTOR}, article {POST_LINK_SELECTOR}").first.wait_for(timeout=selector_timeout_ms)
        return
    except Exception as exc:
        text = await _page_text(page)
        if is_login_or_blocked_text(text):
            raise XAuthenticationRequired("X requires a logged-in browser session. Run `stock-sum x login`.") from exc
        raise XScrapeError("X timeline did not load or selectors changed.") from exc


async def _post_article_locator(page: Any) -> Any:
    primary = page.locator(PRIMARY_POST_SELECTOR)
    if await primary.count():
        return primary
    return page.locator(FALLBACK_POST_SELECTOR)


class XUserCollector(Collector):
    """Collects configured X user timeline content through Playwright."""

    def __init__(
        self,
        collector_id: str,
        handles: list[str],
        *,
        limit: int | None = None,
        include_pinned: bool = False,
    ) -> None:
        self.collector_id = collector_id
        self.handles = handles
        self.limit = limit
        self.include_pinned = include_pinned

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Collect X timeline items."""

        items: list[RawItem] = []
        for handle in self.handles:
            items.extend(await self.collect_handle(context, handle, limit=self.limit))
        return items

    async def collect_handle(self, context: RuntimeContext, handle: str, *, limit: int | None = None) -> list[RawItem]:
        """Collect recent posts for one X handle."""

        from playwright.async_api import async_playwright

        config = context.config.playwright
        x_config = config.x
        normalized_handle = normalize_handle(handle)
        max_posts = limit or x_config.max_posts
        selector_timeout_ms = x_config.selector_timeout_seconds * 1000
        profile_dir = Path(x_config.user_data_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with async_playwright() as playwright:
                browser_launcher = getattr(playwright, config.browser)
                browser_context = await browser_launcher.launch_persistent_context(
                    **_launch_options(user_data_dir=profile_dir, headless=config.headless, channel=config.channel),
                )
                try:
                    page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
                    page.set_default_timeout(config.timeout_seconds * 1000)
                    try:
                        await page.goto(f"{x_config.base_url.rstrip('/')}/{normalized_handle}", wait_until="domcontentloaded")
                    except Exception as exc:
                        raise XScrapeError(f"Failed to navigate to X profile @{normalized_handle}.") from exc
                    await _ensure_timeline_or_raise(page, selector_timeout_ms=selector_timeout_ms)
                    return await self._collect_from_page(
                        page,
                        handle=normalized_handle,
                        base_url=x_config.base_url,
                        limit=max_posts,
                        max_scrolls=x_config.max_scrolls,
                    )
                finally:
                    await browser_context.close()
        except XAuthenticationRequired:
            if x_config.login_required_behavior == "empty":
                return []
            raise

    async def _collect_from_page(
        self,
        page: Any,
        *,
        handle: str,
        base_url: str,
        limit: int,
        max_scrolls: int,
    ) -> list[RawItem]:
        seen: set[str] = set()
        items: list[RawItem] = []

        for scroll_index in range(max_scrolls + 1):
            page_text = await _page_text(page)
            if is_login_or_blocked_text(page_text) and not items:
                raise XAuthenticationRequired("X requires a logged-in browser session. Run `stock-sum x login`.")

            for post in await _extract_public_posts_from_page(page, handle=handle, base_url=base_url):
                if post.status_id in seen or should_skip_post(post.text, include_pinned=self.include_pinned):
                    continue
                seen.add(post.status_id)
                items.append(post_data_to_raw_item(post))
                if len(items) >= limit:
                    return items

            articles = await _post_article_locator(page)
            count = await articles.count()
            for index in range(count):
                post = await _extract_post_from_article(
                    articles.nth(index),
                    handle=handle,
                    base_url=base_url,
                    include_pinned=self.include_pinned,
                )
                if post is None or post.status_id in seen:
                    continue
                seen.add(post.status_id)
                items.append(post_data_to_raw_item(post))
                if len(items) >= limit:
                    return items

            if scroll_index >= max_scrolls:
                break
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(1000)

        if not items:
            raise XAuthenticationRequired("No public X posts were collected. Run `stock-sum x login` and retry.")
        return items


async def login_to_x(context: RuntimeContext, *, channel: str | None = None, wait_seconds: int = 600) -> dict[str, Any]:
    """Open a headed persistent browser so the user can log in to X manually."""

    from playwright.async_api import async_playwright

    config = context.config.playwright
    x_config = config.x
    profile_dir = Path(x_config.user_data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser_launcher = getattr(playwright, config.browser)
        browser_context = await browser_launcher.launch_persistent_context(
            **_launch_options(user_data_dir=profile_dir, headless=False, channel=channel if channel is not None else config.channel),
        )
        try:
            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
            page.set_default_timeout(config.timeout_seconds * 1000)
            await page.goto(f"{x_config.base_url.rstrip('/')}/home", wait_until="domcontentloaded")
            try:
                await page.wait_for_event("close", timeout=wait_seconds * 1000)
            except Exception:
                pass
            return x_profile_status(profile_dir)
        finally:
            await browser_context.close()


async def diagnose_x_profile(
    context: RuntimeContext,
    *,
    handle: str,
    channel: str | None = None,
    wait_seconds: int = 5,
) -> dict[str, Any]:
    """Collect public X page diagnostics without logging in."""

    from playwright.async_api import async_playwright

    config = context.config.playwright
    x_config = config.x
    profile_dir = Path(x_config.user_data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    normalized_handle = normalize_handle(handle)

    async with async_playwright() as playwright:
        browser_launcher = getattr(playwright, config.browser)
        browser_context = await browser_launcher.launch_persistent_context(
            **_launch_options(
                user_data_dir=profile_dir,
                headless=config.headless,
                channel=channel if channel is not None else config.channel,
            ),
        )
        try:
            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
            page.set_default_timeout(config.timeout_seconds * 1000)
            requested_url = f"{x_config.base_url.rstrip('/')}/{normalized_handle}"
            try:
                await page.goto(requested_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(wait_seconds * 1000)
                navigation_error = None
            except Exception as exc:
                navigation_error = str(exc)

            data_testids = await page.evaluate(
                """
                () => Array.from(new Set(
                    Array.from(document.querySelectorAll('[data-testid]'))
                        .map((node) => node.getAttribute('data-testid'))
                        .filter(Boolean)
                )).sort()
                """
            )
            counts = await page.evaluate(
                """
                () => ({
                    tweetArticles: document.querySelectorAll('article[data-testid="tweet"]').length,
                    articles: document.querySelectorAll('article').length,
                    tweetText: document.querySelectorAll('[data-testid="tweetText"]').length,
                    statusLinks: document.querySelectorAll('a[href*="/status/"]').length,
                    cellInnerDivs: document.querySelectorAll('[data-testid="cellInnerDiv"]').length,
                })
                """
            )
            status_link_samples = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a[href*="/status/"]')).slice(0, 20).map((link) => {
                    const ancestors = [];
                    let node = link;
                    for (let depth = 0; node && depth < 8; depth += 1) {
                        const text = (node.innerText || node.textContent || "").trim().replace(/\\s+/g, " ");
                        ancestors.push({
                            depth,
                            tag: node.tagName,
                            href: node.getAttribute("href"),
                            role: node.getAttribute("role"),
                            testid: node.getAttribute("data-testid"),
                            className: node.getAttribute("class"),
                            textLength: text.length,
                            textSample: text.slice(0, 220),
                        });
                        node = node.parentElement;
                    }
                    return ancestors;
                })
                """
            )
            body_text = await _page_text(page)
            return {
                "requested_url": requested_url,
                "current_url": page.url,
                "title": await page.title(),
                "navigation_error": navigation_error,
                "counts": counts,
                "data_testids": data_testids[:100],
                "status_link_samples": status_link_samples,
                "body_sample": body_text[:2000],
                "login_or_blocked_detected": is_login_or_blocked_text(body_text),
            }
        finally:
            await browser_context.close()


def x_profile_status(user_data_dir: str | Path) -> dict[str, Any]:
    """Return whether a persistent X browser profile appears to exist."""

    path = Path(user_data_dir)
    return {
        "path": str(path),
        "exists": path.exists(),
        "has_files": _profile_exists(path),
    }
