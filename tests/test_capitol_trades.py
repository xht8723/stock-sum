"""Capitol Trades scraper helper tests."""

from stock_sum.collectors.playwright.capitol_trades import (
    CapitolTradesSnapshot,
    CapitolTradesSummaryCard,
    CapitolTrade,
    _extract_cards_from_body,
)


def test_capitol_trades_snapshot_serializes() -> None:
    snapshot = CapitolTradesSnapshot(
        source_url="https://www.capitoltrades.com/trades?page=1",
        cards=[CapitolTradesSummaryCard(label="TRADES", value="36,776")],
        trades=[
            CapitolTrade(
                politician="Nancy Pelosi",
                party="Democrat",
                chamber="House",
                state="CA",
                issuer="Intel Corp",
                ticker="INTC:US",
                published="24 Jun 2026",
                traded="28 May 2026",
                filed_after="25 days",
                owner="Spouse",
                transaction_type="BUY*",
                size="1M-5M",
                price="N/A",
            )
        ],
    )

    data = snapshot.to_dict()

    assert data["cards"][0] == {"label": "TRADES", "value": "36,776"}
    assert data["trades"][0]["politician"] == "Nancy Pelosi"
    assert data["trades"][0]["transaction_type"] == "BUY*"


def test_extract_cards_from_body_uses_metrics_before_table() -> None:
    cards = _extract_cards_from_body(
        """
Capitol Trades
TRADES
POLITICIANS
Issuer Country
36,776
TRADES
1,768
FILINGS
$2.317B
VOLUME
205
POLITICIANS
3,088
ISSUERS
POLITICIAN
TRADED ISSUER
""",
        ("TRADES", "FILINGS", "VOLUME", "POLITICIANS", "ISSUERS"),
    )

    assert [(card.label, card.value) for card in cards] == [
        ("TRADES", "36,776"),
        ("FILINGS", "1,768"),
        ("VOLUME", "$2.317B"),
        ("POLITICIANS", "205"),
        ("ISSUERS", "3,088"),
    ]
