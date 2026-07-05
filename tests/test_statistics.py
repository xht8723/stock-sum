"""Tests for statistic aggregation helpers."""

from __future__ import annotations

from stock_sum.statistics import (
    build_social_statistic_summary,
    build_trading_statistic_summary,
    estimate_amount,
    resolve_bucket,
)
from stock_sum.storage.models import StoredSocialStatisticPoint, StoredTradingStatisticPoint


def test_social_statistic_summary_scores_and_counts_by_bucket() -> None:
    summary = build_social_statistic_summary(
        [
            _social_point("2026-06-30T10:00:00+00:00", "bullish", source="x"),
            _social_point("2026-06-30T11:00:00+00:00", "bearish", source="reddit"),
            _social_point("2026-07-01T10:00:00+00:00", "bullish", source="x"),
        ],
        filters={"ticker": "NVDA"},
        bucket="day",
    )

    assert summary["bucket"] == "day"
    assert summary["row_count"] == 3
    assert summary["buckets"] == [
        {
            "bucket": "2026-06-30",
            "post_count": 2,
            "sentiment_counts": {"bullish": 1, "bearish": 1, "mixed": 0, "neutral": 0, "unclear": 0},
            "sources": {"x": 1, "reddit": 1},
            "avg_sentiment_score": 0.0,
        },
        {
            "bucket": "2026-07-01",
            "post_count": 1,
            "sentiment_counts": {"bullish": 1, "bearish": 0, "mixed": 0, "neutral": 0, "unclear": 0},
            "sources": {"x": 1, "reddit": 0},
            "avg_sentiment_score": 1.0,
        },
    ]


def test_trading_statistic_summary_estimates_purchase_and_sale_flow() -> None:
    summary = build_trading_statistic_summary(
        [
            _trading_point("2026-06-30T00:00:00+00:00", "purchase", "$1,001 - $15,000"),
            _trading_point("2026-06-30T00:00:00+00:00", "sell", "$50,001 - $100,000"),
            _trading_point("2026-07-01T00:00:00+00:00", "sell_partial", "$250,001+"),
        ],
        filters={"ticker": "AAPL"},
        bucket="day",
    )

    assert summary["buckets"][0]["purchase_count"] == 1
    assert summary["buckets"][0]["sell_count"] == 1
    assert summary["buckets"][0]["purchase_estimated_usd"] == 8000.5
    assert summary["buckets"][0]["sell_estimated_usd"] == 75000.5
    assert summary["buckets"][0]["net_trade_count"] == 0
    assert summary["buckets"][1]["sell_count"] == 1
    assert summary["skipped"]["open_ended_amount"] == 1


def test_auto_bucket_selection_and_amount_estimation() -> None:
    assert resolve_bucket("auto", _social_dt("2026-01-01"), _social_dt("2026-02-10")) == "day"
    assert resolve_bucket("auto", _social_dt("2026-01-01"), _social_dt("2026-06-01")) == "week"
    assert resolve_bucket("auto", _social_dt("2025-01-01"), _social_dt("2026-06-01")) == "month"
    assert estimate_amount("$1,001 - $15,000") == (8000.5, False)
    assert estimate_amount("$250,001+") == (250001.0, True)
    assert estimate_amount("unknown") == (None, False)


def test_trading_statistic_uses_requested_window_for_buckets() -> None:
    summary = build_trading_statistic_summary(
        [
            _trading_point("2026-06-05T00:00:00+00:00", "sell", "$1,001 - $15,000"),
            _trading_point("2026-06-16T00:00:00+00:00", "sell", "$1,001 - $15,000"),
        ],
        filters={
            "ticker": "AMZN",
            "days": 60,
            "window_start": "2026-04-17T00:00:00+00:00",
            "window_end": "2026-06-16T00:00:00+00:00",
        },
        bucket="auto",
    )

    assert summary["bucket"] == "week"
    assert summary["date_range"] == {"start": "2026-04-17", "end": "2026-06-16"}
    assert summary["buckets"][0]["bucket"] == "2026-04-13"
    assert summary["buckets"][-1]["bucket"] == "2026-06-15"
    assert sum(item["sell_count"] for item in summary["buckets"]) == 2


def _social_point(posted_at: str, sentiment: str, *, source: str) -> StoredSocialStatisticPoint:
    return StoredSocialStatisticPoint(
        source=source,
        profile="default",
        ticker="NVDA",
        source_id=f"{source}-{posted_at}",
        source_ref=f"{source}1",
        label="source",
        sentiment=sentiment,
        importance="high",
        posted_at=posted_at,
        analyzed_at="2026-07-01T00:00:00+00:00",
    )


def _trading_point(transaction_at: str, action: str, amount: str) -> StoredTradingStatisticPoint:
    return StoredTradingStatisticPoint(
        doc_id=f"doc-{transaction_at}-{action}",
        name="Jane Doe",
        state="CA",
        asset="Apple Inc. - Common Stock (AAPL) [ST]",
        asset_type_code="ST",
        stock_ticker="AAPL",
        transaction_action=action,
        transaction_date=transaction_at[:10],
        transaction_date_utc=transaction_at,
        amount=amount,
    )


def _social_dt(value: str):
    from datetime import datetime, timezone

    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
