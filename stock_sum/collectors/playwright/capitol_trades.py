"""Headless Playwright scraper for Capitol Trades politician transactions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from stock_sum.core.errors import StockSumError

CAPITOL_TRADES_URL = "https://www.capitoltrades.com/trades?page=1"


class CapitolTradesScrapeError(StockSumError):
    """Raised when Capitol Trades data cannot be scraped."""


@dataclass(frozen=True)
class CapitolTradesSummaryCard:
    """Top summary card from Capitol Trades."""

    label: str
    value: str


@dataclass(frozen=True)
class CapitolTrade:
    """One visible Capitol Trades transaction row."""

    politician: str
    party: str | None
    chamber: str | None
    state: str | None
    issuer: str
    ticker: str | None
    published: str
    traded: str
    filed_after: str
    owner: str
    transaction_type: str
    size: str
    price: str
    detail_url: str | None = None


@dataclass(frozen=True)
class CapitolTradesSnapshot:
    """Scraped Capitol Trades page snapshot."""

    source_url: str
    cards: list[CapitolTradesSummaryCard]
    trades: list[CapitolTrade]

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable snapshot data."""

        return {
            "source_url": self.source_url,
            "cards": [asdict(card) for card in self.cards],
            "trades": [asdict(trade) for trade in self.trades],
        }


async def scrape_capitol_trades(
    *,
    url: str = CAPITOL_TRADES_URL,
    limit: int = 12,
    headless: bool = True,
    channel: str | None = "chromium",
    timeout_seconds: int = 30,
    settle_ms: int = 3000,
) -> CapitolTradesSnapshot:
    """Scrape visible politician trades from Capitol Trades."""

    async with async_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": headless}
        if channel:
            launch_options["channel"] = channel
        browser = await playwright.chromium.launch(**launch_options)
        try:
            page = await browser.new_page(viewport={"width": 1392, "height": 804})
            page.set_default_timeout(timeout_seconds * 1000)
            page.set_default_navigation_timeout(timeout_seconds * 1000)
            return await scrape_capitol_trades_page(page, url=url, limit=limit, settle_ms=settle_ms)
        finally:
            await browser.close()


async def scrape_capitol_trades_page(
    page: Page,
    *,
    url: str = CAPITOL_TRADES_URL,
    limit: int = 12,
    settle_ms: int = 3000,
) -> CapitolTradesSnapshot:
    """Scrape Capitol Trades using an already-created Playwright page."""

    try:
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(settle_ms)
    except PlaywrightError as exc:
        raise CapitolTradesScrapeError(f"Capitol Trades navigation failed: {exc}") from exc

    cards = await _extract_cards(page)
    rows = page.locator("tbody tr")
    row_count = min(await rows.count(), limit)
    trades: list[CapitolTrade] = []
    for index in range(row_count):
        trade = await _extract_trade_row(page, rows.nth(index))
        if trade is not None:
            trades.append(trade)
    if not trades:
        body = await _safe_inner_text(page.locator("body").first)
        raise CapitolTradesScrapeError(
            "Capitol Trades table did not expose trade rows. "
            f"Body preview: {body[:300]}"
        )
    return CapitolTradesSnapshot(source_url=page.url, cards=cards, trades=trades)


async def _extract_cards(page: Page) -> list[CapitolTradesSummaryCard]:
    labels = ("TRADES", "FILINGS", "VOLUME", "POLITICIANS", "ISSUERS")
    body = await _safe_inner_text(page.locator("body").first)
    body_cards = _extract_cards_from_body(body, labels)
    if body_cards:
        return body_cards

    cards: list[CapitolTradesSummaryCard] = []
    for label in labels:
        locator = page.locator(f"text={label}").first
        try:
            card = locator.locator("xpath=ancestor::div[contains(@class, 'rounded')][1]")
            text = await card.inner_text(timeout=1000)
        except PlaywrightError:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[-1].upper() == label:
            cards.append(CapitolTradesSummaryCard(label=label, value=lines[-2]))
    if cards:
        return _dedupe_cards(cards)

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.upper() in labels and index > 0:
            cards.append(CapitolTradesSummaryCard(label=line.upper(), value=lines[index - 1]))
    return _dedupe_cards(cards)


def _extract_cards_from_body(body: str, labels: tuple[str, ...]) -> list[CapitolTradesSummaryCard]:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    try:
        table_index = next(index for index, line in enumerate(lines) if line == "POLITICIAN")
    except StopIteration:
        table_index = len(lines)
    cards: list[CapitolTradesSummaryCard] = []
    for index, line in enumerate(lines[:table_index]):
        label = line.upper()
        if label not in labels or index == 0:
            continue
        value = lines[index - 1]
        if _looks_like_metric_value(value):
            cards.append(CapitolTradesSummaryCard(label=label, value=value))
    return _dedupe_cards(cards)


def _looks_like_metric_value(value: str) -> bool:
    return bool(value.startswith("$") or any(char.isdigit() for char in value)) and len(value) <= 20


def _dedupe_cards(cards: list[CapitolTradesSummaryCard]) -> list[CapitolTradesSummaryCard]:
    seen: set[str] = set()
    result: list[CapitolTradesSummaryCard] = []
    for card in cards:
        if card.label in seen:
            continue
        seen.add(card.label)
        result.append(card)
    return result


async def _extract_trade_row(page: Page, row: Any) -> CapitolTrade | None:
    cells = row.locator("td")
    if await cells.count() < 9:
        return None

    politician_cell = cells.nth(0)
    issuer_cell = cells.nth(1)
    politician = await _safe_inner_text(politician_cell.locator(".politician-name").first)
    party = await _safe_inner_text(politician_cell.locator(".party").first) or None
    chamber = await _safe_inner_text(politician_cell.locator(".chamber").first) or None
    state = await _safe_inner_text(politician_cell.locator(".us-state-compact").first) or None
    if not politician:
        parts = [part.strip() for part in (await _safe_inner_text(politician_cell)).splitlines() if part.strip()]
        politician = parts[0] if parts else ""

    issuer = await _safe_inner_text(issuer_cell.locator("a").first)
    issuer_text = [part.strip() for part in (await _safe_inner_text(issuer_cell)).splitlines() if part.strip()]
    if not issuer and issuer_text:
        issuer = issuer_text[0]
    ticker = issuer_text[1] if len(issuer_text) > 1 else None

    type_cell = cells.nth(6)
    tx_type = (await _safe_inner_text(type_cell.locator(".tx-type").first) or await _safe_inner_text(type_cell)).upper()
    if await type_cell.locator(".has-asterisk").count() > 0 and not tx_type.endswith("*"):
        tx_type += "*"

    detail_url = None
    href = await _safe_get_attribute(cells.nth(9).locator("a").first, "href")
    if href:
        detail_url = href if href.startswith("http") else f"https://www.capitoltrades.com{href}"

    return CapitolTrade(
        politician=politician,
        party=party,
        chamber=chamber,
        state=state,
        issuer=issuer,
        ticker=ticker,
        published=_join_cell_date(await _safe_inner_text(cells.nth(2))),
        traded=_join_cell_date(await _safe_inner_text(cells.nth(3))),
        filed_after=_filed_after(await _safe_inner_text(cells.nth(4))),
        owner=await _safe_inner_text(cells.nth(5)),
        transaction_type=tx_type,
        size=await _safe_inner_text(cells.nth(7)),
        price=await _safe_inner_text(cells.nth(8)),
        detail_url=detail_url,
    )


def _join_cell_date(value: str) -> str:
    return " ".join(part.strip() for part in value.splitlines() if part.strip())


def _filed_after(value: str) -> str:
    parts = [part.strip() for part in value.splitlines() if part.strip()]
    if len(parts) == 2 and parts[0] == "days":
        return f"{parts[1]} days"
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}"
    return " ".join(parts)


async def _safe_inner_text(locator: Any) -> str:
    try:
        return (await locator.inner_text(timeout=1000)).strip()
    except PlaywrightError:
        return ""


async def _safe_get_attribute(locator: Any, name: str) -> str | None:
    try:
        return await locator.get_attribute(name, timeout=1000)
    except PlaywrightError:
        return None
