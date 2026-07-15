"""Validation and filter helpers for HTTP job options."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from stock_sum.api.job_models import (
    Sec13FReportJobOptions,
    StatisticJobOptions,
    TradingReportJobOptions,
    TrendingsReportJobOptions,
)


def validate_trading_filters(options: TradingReportJobOptions) -> None:
    if not any(
        (
            options.name,
            options.start_date,
            options.end_date,
            options.days,
            options.filing_start_date,
            options.filing_end_date,
            options.filing_days,
            options.collected_days,
            options.asset_type,
            options.ticker,
        )
    ):
        raise ValueError(
            "Trading report requires at least one filter: name, transaction dates, filing dates, collected_days, days, asset_type, or ticker."
        )
    if options.days is not None and options.days < 1:
        raise ValueError("Trading report days must be a positive integer.")
    if options.filing_days is not None and options.filing_days < 1:
        raise ValueError("Trading report filing_days must be a positive integer.")
    if options.collected_days is not None and options.collected_days < 1:
        raise ValueError("Trading report collected_days must be a positive integer.")
    if options.days is not None and (options.start_date or options.end_date):
        raise ValueError("Trading report accepts either days or explicit start/end dates, not both.")
    if options.filing_days is not None and (options.filing_start_date or options.filing_end_date):
        raise ValueError("Trading report accepts either filing_days or explicit filing_start_date/filing_end_date, not both.")


def validate_13f_filters(options: Sec13FReportJobOptions) -> None:
    has_filter = any(
        (
            options.manager,
            options.cik,
            options.accession_number,
            options.issuer,
            options.cusip,
            options.figi,
            options.put_call,
            options.period_start,
            options.period_end,
            options.filing_start,
            options.filing_end,
            options.min_value is not None,
            options.min_shares is not None,
        )
    )
    if not has_filter:
        raise ValueError("13F report requires at least one filter: manager, issuer, CIK, accession, CUSIP, FIGI, date, value, or shares.")
    if options.limit < 1:
        raise ValueError("13F report limit must be at least 1.")


def validate_trendings_filters(options: TrendingsReportJobOptions) -> None:
    if options.mode not in {"html", "markdown", "discord", "text", "json"}:
        raise ValueError("Trendings report mode must be html, markdown, discord, text, or json.")
    if options.limit < 1:
        raise ValueError("Trendings report limit must be at least 1.")
    if options.days < 1:
        raise ValueError("Trendings report days must be at least 1.")
    if options.comparison_days < 1:
        raise ValueError("Trendings report comparison_days must be at least 1.")
    if options.mentions_change_pct <= 0:
        raise ValueError("Trendings report mentions_change_pct must be greater than 0.")
    if options.sentiment_change_pct <= 0:
        raise ValueError("Trendings report sentiment_change_pct must be greater than 0.")
    if options.minimum_mentions < 1:
        raise ValueError("Trendings report minimum_mentions must be at least 1.")
    trendings_date_window(options)


def validate_statistic_filters(options: StatisticJobOptions) -> None:
    if options.mode not in {"social", "trading"}:
        raise ValueError("Statistic mode must be social or trading.")
    if options.bucket not in {"auto", "day", "week", "month"}:
        raise ValueError("Statistic bucket must be auto, day, week, or month.")
    if options.days is not None and options.days < 1:
        raise ValueError("Statistic days must be a positive integer.")
    if options.days is not None and (options.start_date or options.end_date):
        raise ValueError("Statistic accepts either days or explicit start/end dates, not both.")
    if options.mode == "social":
        if options.source not in {"x", "reddit", "all"}:
            raise ValueError("Statistic source must be x, reddit, or all.")
        if options.sentiment not in {"bullish", "bearish", "mixed", "neutral", "unclear", "all"}:
            raise ValueError("Statistic sentiment must be bullish, bearish, mixed, neutral, unclear, or all.")
    if options.mode == "trading" and options.action not in {"purchase", "sell", "sell_partial", "all"}:
        raise ValueError("Statistic action must be purchase, sell, sell_partial, or all.")
    has_filter = any(
        (
            options.ticker,
            options.fuzzy_tag,
            options.name,
            options.asset_name,
            options.asset_type,
            options.days,
            options.start_date,
            options.end_date,
        )
    )
    if not has_filter:
        raise ValueError("Statistic requires at least one filter: ticker, fuzzy_tag, name, asset_name, asset_type, days, or date range.")


def trading_date_window(options: TradingReportJobOptions) -> tuple[datetime | None, datetime | None]:
    if options.days is not None:
        now = datetime.now(timezone.utc)
        return now - timedelta(days=options.days), now
    return parse_date_filter(options.start_date, end_of_day=False), parse_date_filter(options.end_date, end_of_day=True)


def trading_filing_date_window(options: TradingReportJobOptions) -> tuple[datetime | None, datetime | None]:
    if options.filing_days is not None:
        now = datetime.now(timezone.utc)
        return now - timedelta(days=options.filing_days), now
    return (
        parse_date_filter(options.filing_start_date, end_of_day=False),
        parse_date_filter(options.filing_end_date, end_of_day=True),
    )


def trading_collected_window(options: TradingReportJobOptions) -> tuple[datetime | None, datetime | None]:
    if options.collected_days is None:
        return None, None
    now = datetime.now(timezone.utc)
    return now - timedelta(days=options.collected_days), now


def statistic_date_window(options: StatisticJobOptions) -> tuple[datetime | None, datetime | None]:
    if options.days is not None:
        now = datetime.now(timezone.utc)
        return now - timedelta(days=options.days), now
    return parse_date_filter(options.start_date, end_of_day=False), parse_date_filter(options.end_date, end_of_day=True)


def trendings_date_window(options: TrendingsReportJobOptions) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    from_date = parse_yyyy_mm_dd(options.from_date, "from") if options.from_date else None
    to_date = parse_yyyy_mm_dd(options.to_date, "to") if options.to_date else None
    span = max(int(options.days), 1) - 1
    if from_date is None and to_date is None:
        to_date = today
        from_date = to_date - timedelta(days=span)
    elif from_date is None and to_date is not None:
        from_date = to_date - timedelta(days=span)
    elif from_date is not None and to_date is None:
        to_date = from_date + timedelta(days=span)
    assert from_date is not None and to_date is not None
    if from_date > to_date:
        raise ValueError("Trendings report from date must be on or before to date.")
    return from_date, to_date


def parse_yyyy_mm_dd(value: str, label: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Trendings report {label} date must use YYYY-MM-DD.") from exc


def parse_date_filter(value: str | None, *, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed_date = datetime.strptime(text, fmt).date()
            return datetime.combine(parsed_date, time.max if end_of_day else time.min, tzinfo=timezone.utc)
        except ValueError:
            continue
    parsed = parse_utc_datetime(text)
    if parsed is None:
        raise ValueError(f"Invalid trading report date: {value}")
    if end_of_day and parsed.time() == time.min:
        return datetime.combine(parsed.date(), time.max, tzinfo=timezone.utc)
    return parsed


def trading_filter_data(
    options: TradingReportJobOptions,
    transaction_start: datetime | None,
    transaction_end: datetime | None,
    filing_start: datetime | None,
    filing_end: datetime | None,
    collected_start: datetime | None,
    collected_end: datetime | None,
) -> dict[str, Any]:
    return {
        "name": options.name,
        "start_date": options.start_date,
        "end_date": options.end_date,
        "days": options.days,
        "filing_start_date": options.filing_start_date,
        "filing_end_date": options.filing_end_date,
        "filing_days": options.filing_days,
        "collected_days": options.collected_days,
        "asset_type": options.asset_type,
        "ticker": options.ticker,
        "transaction_start": transaction_start.isoformat() if transaction_start else None,
        "transaction_end": transaction_end.isoformat() if transaction_end else None,
        "filing_start": filing_start.isoformat() if filing_start else None,
        "filing_end": filing_end.isoformat() if filing_end else None,
        "collected_start": collected_start.isoformat() if collected_start else None,
        "collected_end": collected_end.isoformat() if collected_end else None,
        "limit": options.limit,
        "force_refresh": options.force_refresh,
        "allow_empty": options.allow_empty,
    }


def sec_13f_filter_data(
    options: Sec13FReportJobOptions,
    period_start: datetime | None,
    period_end: datetime | None,
    filing_start: datetime | None,
    filing_end: datetime | None,
) -> dict[str, Any]:
    return {
        "manager": options.manager,
        "cik": options.cik,
        "accession_number": options.accession_number,
        "issuer": options.issuer,
        "cusip": options.cusip,
        "figi": options.figi,
        "put_call": options.put_call,
        "period_start": period_start.date().isoformat() if period_start else None,
        "period_end": period_end.date().isoformat() if period_end else None,
        "filing_start": filing_start.date().isoformat() if filing_start else None,
        "filing_end": filing_end.date().isoformat() if filing_end else None,
        "min_value": options.min_value,
        "min_shares": options.min_shares,
        "limit": options.limit,
        "force_refresh": options.force_refresh,
    }


def statistic_filter_data(
    options: StatisticJobOptions,
    start_at: datetime | None,
    end_at: datetime | None,
) -> dict[str, Any]:
    return {
        "mode": options.mode,
        "ticker": options.ticker,
        "fuzzy_tag": options.fuzzy_tag if options.mode == "social" else None,
        "name": options.name,
        "asset_name": options.asset_name if options.mode == "trading" else None,
        "asset_type": options.asset_type,
        "action": options.action,
        "source": options.source if options.mode == "social" else None,
        "sentiment": options.sentiment if options.mode == "social" else None,
        "start_date": options.start_date,
        "end_date": options.end_date,
        "days": options.days,
        "bucket": options.bucket,
        "window_start": start_at.isoformat() if start_at else None,
        "window_end": end_at.isoformat() if end_at else None,
    }


def parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_validate_trading_filters = validate_trading_filters
_validate_13f_filters = validate_13f_filters
_validate_trendings_filters = validate_trendings_filters
_validate_statistic_filters = validate_statistic_filters
_trading_date_window = trading_date_window
_trading_filing_date_window = trading_filing_date_window
_trading_collected_window = trading_collected_window
_statistic_date_window = statistic_date_window
_trendings_date_window = trendings_date_window
_parse_yyyy_mm_dd = parse_yyyy_mm_dd
_parse_date_filter = parse_date_filter
_trading_filter_data = trading_filter_data
_sec_13f_filter_data = sec_13f_filter_data
_statistic_filter_data = statistic_filter_data
