"""SQLite storage JSON, scalar, and filter normalization helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
import json
import re


TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.-][A-Z0-9]{1,3})?$")


def normalized_ticker(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    ticker = value.strip().upper()
    if ticker.startswith("$"):
        ticker = ticker[1:].strip()
    ticker = ticker.replace("/", ".")
    return ticker if TICKER_PATTERN.fullmatch(ticker) else None


def normalized_tickers(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        parsed = value
    if not isinstance(parsed, list):
        return []
    tickers: list[str] = []
    for item in parsed:
        ticker = normalized_ticker(item)
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def tickers_json(value: Any) -> str:
    return json.dumps(normalized_tickers(value), ensure_ascii=False)


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def datetime_param(value: datetime | date, *, end_of_day: bool = False) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = datetime.combine(value, time.max if end_of_day else time.min, tzinfo=timezone.utc)
    else:
        parsed = value
    return utc_datetime(parsed).isoformat()


def date_param(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return utc_datetime(value).date().isoformat()
    return value.isoformat()


def normalized_upper_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def normalized_contains_filter(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None


def normalized_tag_filter(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


def tags_contain(tags_json: str | None, fuzzy_tag: str) -> bool:
    normalized_tag = normalized_tag_filter(fuzzy_tag)
    if not normalized_tag:
        return False
    for item in json_list(tags_json):
        tag = normalized_tag_filter(str(item))
        if tag == normalized_tag:
            return True
    return False


def normalized_action_filter(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return None if normalized == "all" else normalized


def json_obj(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalized_importance(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("high"):
        return "high"
    if text.startswith("low"):
        return "low"
    if text.startswith("med"):
        return "medium"
    return "medium"


_normalized_ticker = normalized_ticker
_normalized_tickers = normalized_tickers
_tickers_json = tickers_json
_utc_datetime = utc_datetime
_datetime_param = datetime_param
_date_param = date_param
_normalized_upper_filter = normalized_upper_filter
_normalized_contains_filter = normalized_contains_filter
_normalized_tag_filter = normalized_tag_filter
_tags_contain = tags_contain
_normalized_action_filter = normalized_action_filter
_json_obj = json_obj
_json_list = json_list
_optional_str = optional_str
_optional_int = optional_int
_optional_float = optional_float
_normalized_importance = normalized_importance
