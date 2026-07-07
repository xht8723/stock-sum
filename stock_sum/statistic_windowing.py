"""Date windowing and normalization helpers for statistic reports."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
import re

StatisticBucket = Literal["auto", "day", "week", "month"]

SENTIMENT_SCORES = {
    "bullish": 1.0,
    "bearish": -1.0,
    "mixed": 0.0,
    "neutral": 0.0,
    "unclear": 0.0,
}

_USD_RE = re.compile(r"\$?\s*([0-9][0-9,]*)")


def resolve_bucket(bucket: StatisticBucket, start: datetime, end: datetime) -> Literal["day", "week", "month"]:
    """Resolve auto bucket selection from date span."""

    if bucket in {"day", "week", "month"}:
        return bucket
    span_days = max(0, (end.date() - start.date()).days)
    if span_days <= 45:
        return "day"
    if span_days <= 365:
        return "week"
    return "month"


def statistic_window(filters: dict[str, Any], dated: list[tuple[datetime, Any]]) -> tuple[datetime, datetime]:
    """Return the requested statistic window, falling back to the data span."""

    window_start = parse_datetime(str(filters.get("window_start") or ""))
    window_end = parse_datetime(str(filters.get("window_end") or ""))
    if window_start is not None and window_end is not None and window_start <= window_end:
        return window_start, window_end
    return min(item[0] for item in dated), max(item[0] for item in dated)


def bucket_keys_between(start: datetime, end: datetime, bucket: Literal["day", "week", "month"]) -> list[str]:
    """Return every bucket key touched by the requested window."""

    current = bucket_start(start, bucket)
    final = bucket_start(end, bucket)
    keys = []
    while current <= final:
        keys.append(bucket_key(current, bucket))
        current = next_bucket_start(current, bucket)
    return keys


def bucket_start(value: datetime, bucket: Literal["day", "week", "month"]) -> datetime:
    current = value.astimezone(timezone.utc).date()
    if bucket == "week":
        current = current.fromordinal(current.toordinal() - current.weekday())
    elif bucket == "month":
        current = date(current.year, current.month, 1)
    return datetime.combine(current, time.min, tzinfo=timezone.utc)


def next_bucket_start(value: datetime, bucket: Literal["day", "week", "month"]) -> datetime:
    if bucket == "day":
        return value + timedelta(days=1)
    if bucket == "week":
        return value + timedelta(days=7)
    month = value.month + 1
    year = value.year
    if month == 13:
        month = 1
        year += 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def bucket_key(value: datetime, bucket: Literal["day", "week", "month"]) -> str:
    """Return stable ISO-like bucket key."""

    current = value.astimezone(timezone.utc).date()
    if bucket == "day":
        return current.isoformat()
    if bucket == "week":
        week_start = current.fromordinal(current.toordinal() - current.weekday())
        return week_start.isoformat()
    return date(current.year, current.month, 1).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    """Parse common UTC datetime strings."""

    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return parse_date(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_date(value: str | None) -> datetime | None:
    """Parse date-only strings used in disclosure rows."""

    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return datetime.combine(parsed, time.min, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_sentiment(value: str | None) -> str:
    """Normalize sentiment to a score-bearing value."""

    normalized = (value or "").strip().lower()
    return normalized if normalized in SENTIMENT_SCORES else "unclear"


def normalize_action(value: str | None) -> str:
    """Normalize PTR transaction actions."""

    normalized = (value or "").strip().lower()
    if normalized in {"purchase", "sell", "sell_partial"}:
        return normalized
    if normalized.startswith("p"):
        return "purchase"
    if normalized.startswith("s"):
        return "sell_partial" if "partial" in normalized else "sell"
    return normalized


def estimate_amount(value: str | None) -> tuple[float | None, bool]:
    """Estimate a disclosure amount range using midpoint or lower bound."""

    if not value:
        return None, False
    text = value.replace("\u2013", "-").replace("\u2014", "-")
    numbers = [int(match.replace(",", "")) for match in _USD_RE.findall(text)]
    if len(numbers) >= 2:
        return (numbers[0] + numbers[-1]) / 2.0, False
    if len(numbers) == 1:
        open_ended = "+" in text or "over" in text.lower() or "more" in text.lower()
        return float(numbers[0]), open_ended
    return None, False
