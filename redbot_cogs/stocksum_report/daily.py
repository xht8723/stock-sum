"""Daily report scheduling helpers for the stock-sum Redbot cog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import re

DAILY_PRESTART_SECONDS = 30 * 60
_DAILY_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


@dataclass(frozen=True)
class DailyReportSection:
    """One completed section of the daily DM bundle."""

    title: str
    content: str
    error: str | None = None


def _empty_daily_subscription() -> dict[str, Any]:
    return {
        "enabled": False,
        "time_utc": "",
        "last_sent_utc_date": "",
        "last_error": "",
        "created_at": "",
        "updated_at": "",
    }


def _normalize_daily_subscription(payload: Any) -> dict[str, Any]:
    normalized = _empty_daily_subscription()
    if isinstance(payload, dict):
        normalized.update({key: value for key, value in payload.items() if key in normalized})
    normalized["enabled"] = bool(normalized["enabled"])
    normalized["time_utc"] = str(normalized["time_utc"] or "")
    normalized["last_sent_utc_date"] = str(normalized["last_sent_utc_date"] or "")
    normalized["last_error"] = str(normalized["last_error"] or "")
    return normalized


def _validate_daily_time(value: str) -> tuple[str | None, str | None]:
    clean = value.strip()
    if not _DAILY_TIME_RE.fullmatch(clean):
        return None, "daily time must be UTC HH:MM in 24-hour format."
    return clean, None


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _daily_target_date_if_due(subscription: dict[str, Any], *, now_utc: datetime) -> str | None:
    normalized = _normalize_daily_subscription(subscription)
    if not normalized["enabled"]:
        return None
    try:
        hour_text, minute_text = normalized["time_utc"].split(":", 1)
        scheduled_hour = int(hour_text)
        scheduled_minute = int(minute_text)
    except (AttributeError, TypeError, ValueError):
        return None

    try:
        scheduled_today = now_utc.replace(hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0)
    except ValueError:
        return None
    prestart = timedelta(seconds=DAILY_PRESTART_SECONDS)
    for scheduled_at in (scheduled_today + timedelta(days=1), scheduled_today):
        target_date = scheduled_at.date().isoformat()
        if normalized["last_sent_utc_date"] == target_date:
            continue
        if now_utc >= scheduled_at - prestart:
            return target_date
    return None


async def _daily_section(title: str, runner: Any) -> DailyReportSection:
    try:
        artifact = await runner()
    except Exception as exc:
        return DailyReportSection(title=title, content="", error=f"{title} failed: {exc}")
    content = artifact.content.decode("utf-8", errors="replace").strip()
    return DailyReportSection(title=title, content=content or "Report generated, but it did not contain any text.")


def _format_daily_report(sections: list[DailyReportSection], *, sent_utc_date: str) -> str:
    lines = [
        "**Stock-Sum Daily Report**",
        f"UTC date: {sent_utc_date}",
    ]
    for section in sections:
        lines.extend(["", f"## {section.title}"])
        if section.error:
            lines.append(f"Warning: {section.error}")
        else:
            lines.append(section.content)
    return "\n".join(lines)
