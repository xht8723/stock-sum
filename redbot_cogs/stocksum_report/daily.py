"""Daily report scheduling helpers for the stock-sum Redbot cog."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
import json
import re

DAILY_PRESTART_SECONDS = 30 * 60
DAILY_MESSAGE_LIMIT = 1900
DAILY_SOCIAL_DISPLAY_LIMIT = 5
_DAILY_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


@dataclass(frozen=True)
class DailyReportSection:
    """One completed section of the daily DM bundle."""

    kind: str
    title: str
    payload: dict[str, Any] | None = None
    status: dict[str, Any] = field(default_factory=dict)
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


async def _daily_section(kind: str, title: str, runner: Any) -> DailyReportSection:
    """Run and decode one JSON report artifact without aborting the remaining bundle."""

    try:
        artifact = await runner()
        content = artifact.content.decode("utf-8", errors="replace").strip()
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("report JSON must be an object")
    except Exception as exc:
        return DailyReportSection(kind=kind, title=title, error=f"{title} failed: {exc}")
    status = artifact.status if isinstance(artifact.status, dict) else {}
    return DailyReportSection(kind=kind, title=title, payload=payload, status=status)


def _format_daily_report(
    sections: list[DailyReportSection],
    *,
    sent_utc_date: str,
    generated_at: datetime | None = None,
) -> list[str]:
    """Render the structured daily bundle as ordered Discord-sized messages."""

    generated_utc = _coerce_utc(generated_at or datetime.now(timezone.utc))
    by_kind = {section.kind: section for section in sections}
    trendings = by_kind.get("trendings") or DailyReportSection(
        kind="trendings", title="Market Trends", error="Market Trends section was not produced."
    )
    social = by_kind.get("social") or DailyReportSection(
        kind="social", title="High-Priority Social Signals", error="Social Signals section was not produced."
    )
    trading = by_kind.get("trading") or DailyReportSection(
        kind="trading", title="House PTR Disclosures", error="House PTR section was not produced."
    )

    messages = _render_daily_cover(
        [trendings, social, trading],
        trendings=trendings,
        social=social,
        sent_utc_date=sent_utc_date,
        generated_at=generated_utc,
    )
    messages.extend(_render_daily_trendings(trendings))
    messages.extend(_render_daily_social(social))
    messages.extend(_render_daily_trading(trading))
    return messages


def _render_daily_cover(
    sections: list[DailyReportSection],
    *,
    trendings: DailyReportSection,
    social: DailyReportSection,
    sent_utc_date: str,
    generated_at: datetime,
) -> list[str]:
    healthy = sum(1 for section in sections if not section.error and not _section_warnings(section))
    degraded = len(sections) - healthy
    timestamp = int(generated_at.timestamp())
    coverage = f"Coverage: `{healthy}/{len(sections)} healthy`"
    if degraded:
        coverage += f" · `{degraded} degraded` · details appear in the affected sections"
    blocks = [
        f"Report date: `{sent_utc_date}` · Generated <t:{timestamp}:F> (<t:{timestamp}:R>)\n{coverage}",
        _daily_highlights(trendings, social),
    ]
    return _pack_daily_section("Stock-Sum Daily Brief", blocks)


def _daily_highlights(trendings: DailyReportSection, social: DailyReportSection) -> str:
    lines = ["**At a glance**"]
    trend_payload = trendings.payload or {}
    trend_summary = _dict_value(trend_payload.get("summary"))
    if trendings.error:
        lines.append("• Market trend highlights are unavailable.")
    else:
        changes = _dict_items(trend_summary.get("changes"))[:2]
        for change in changes:
            lines.append(_trending_change_highlight(change))

    social_items = _daily_social_items(social.payload or {})
    high_items = [item for item in social_items if _importance(item) == "high"]
    if social.error:
        lines.append("• Social highlights are unavailable.")
    elif high_items:
        counts = _sentiment_counts(high_items)
        sentiment_text = " · ".join(
            f"{count} {sentiment}" for sentiment, count in counts.items() if count
        )
        suffix = f" — {sentiment_text}" if sentiment_text else ""
        plural = "s" if len(high_items) != 1 else ""
        lines.append(f"• **{len(high_items)} high-priority social signal{plural}**{suffix}")
    else:
        lines.append("• No high-priority social signals.")

    overlap = sorted(_trending_tickers(trend_payload) & _social_tickers(high_items))
    if overlap:
        lines.append("• **Trend/social overlap:** " + ", ".join(f"`{ticker}`" for ticker in overlap[:5]))
    if len(lines) == 1:
        lines.append("• No high-priority trend or social highlights.")
    return "\n".join(lines)


def _render_daily_trendings(section: DailyReportSection) -> list[str]:
    if section.error:
        return _pack_daily_section(section.title, [f"_Unavailable: {_compact_text(section.error, 500)}_"])
    payload = section.payload or {}
    if payload.get("skipped"):
        reason = _compact_text(payload.get("skip_reason") or "Trendings source is not configured.", 500)
        return _pack_daily_section(section.title, [f"_{reason}_", *_warning_blocks(section)])

    summary = _dict_value(payload.get("summary"))
    filters = _dict_value(payload.get("filters"))
    display_limit = _positive_int(filters.get("display_limit"), default=5)
    blocks: list[str] = []
    from_date = _compact_text(filters.get("from"), 40)
    to_date = _compact_text(filters.get("to"), 40)
    comparison_days = _positive_int(filters.get("comparison_days"), default=7)
    if from_date or to_date:
        window = from_date if from_date == to_date else f"{from_date or '?'} to {to_date or '?'}"
        blocks.append(
            f"UTC window: `{window}` · compared with the latest prior snapshot within `{comparison_days} days`"
        )
    blocks.extend(_warning_blocks(section))

    changes = _dict_items(summary.get("changes"))[:display_limit]
    if changes:
        blocks.append("__Significant changes__")
        blocks.extend(_trending_change_block(row) for row in changes)

    stock_rows = _limited_platform_rows(summary.get("stocks"), display_limit)
    if stock_rows:
        blocks.append("__Top stocks__")
        blocks.extend(_trending_stock_block(row) for row in stock_rows)

    sector_rows = _limited_platform_rows(summary.get("sectors"), display_limit)
    if sector_rows:
        blocks.append("__Top sectors__")
        blocks.extend(_trending_sector_block(row) for row in sector_rows)

    if not changes and not stock_rows and not sector_rows:
        blocks.append("_No market trend data was available for this window._")
    return _pack_daily_section(section.title, blocks)


def _render_daily_social(section: DailyReportSection) -> list[str]:
    if section.error:
        return _pack_daily_section(section.title, [f"_Unavailable: {_compact_text(section.error, 500)}_"])
    payload = section.payload or {}
    items = _daily_social_items(payload)
    high_items = [item for item in items if _importance(item) == "high"]
    high_items.sort(key=lambda item: (_confidence_rank(item.get("confidence")), item.get("_ordinal", 0)))
    suppressed = len(items) - len(high_items)

    blocks: list[str] = []
    coverage = _social_coverage_text(payload.get("source_windows"))
    if coverage:
        blocks.append(coverage)
    blocks.extend(_warning_blocks(section))
    if not high_items:
        message = "_No high-priority social signals were found._"
        if suppressed:
            message += f" `{suppressed}` medium/low signal{'s were' if suppressed != 1 else ' was'} not included."
        blocks.append(message)
        return _pack_daily_section(section.title, blocks)

    displayed = high_items[:DAILY_SOCIAL_DISPLAY_LIMIT]
    blocks.append(
        f"Showing `{len(displayed)}` of `{len(high_items)}` high-priority signal{'s' if len(high_items) != 1 else ''}."
    )
    blocks.extend(_social_signal_block(item) for item in displayed)
    omitted = len(high_items) - len(displayed)
    if omitted:
        verb = "signals were" if omitted != 1 else "signal was"
        blocks.append(f"_{omitted} additional high-priority {verb} omitted; use `/recent_posts` for the full report._")
    if suppressed:
        blocks.append(f"_{suppressed} medium/low signal{'s were' if suppressed != 1 else ' was'} not included._")
    return _pack_daily_section(section.title, blocks)


def _render_daily_trading(section: DailyReportSection) -> list[str]:
    if section.error:
        return _pack_daily_section(
            section.title,
            ["_Rolling filing window: last 24 hours_", f"_Unavailable: {_compact_text(section.error, 500)}_"],
        )
    payload = section.payload or {}
    rows = _dict_items(payload.get("house_ptr") or _dict_value(payload.get("summary")).get("house_ptr"))
    filters = _dict_value(payload.get("filters"))
    blocks = ["_Rolling filing window: last 24 hours_"]
    filing_start = _compact_text(filters.get("filing_start"), 80)
    filing_end = _compact_text(filters.get("filing_end"), 80)
    if filing_start or filing_end:
        blocks.append(f"UTC coverage: `{filing_start or '?'}` to `{filing_end or '?'}`")
    blocks.extend(_warning_blocks(section))
    if not rows:
        blocks.append("_No new House PTR filings in the rolling last 24 hours._")
        return _pack_daily_section(section.title, blocks)

    blocks.append(f"Disclosure rows: `{len(rows)}`")
    blocks.extend(_trading_disclosure_block(row) for row in rows)
    return _pack_daily_section(section.title, blocks)


def _pack_daily_section(title: str, blocks: list[str], *, limit: int = DAILY_MESSAGE_LIMIT) -> list[str]:
    """Pack complete presentation blocks and repeat the heading on continuation messages."""

    heading = f"**{title}**"
    continued_heading = f"**{title} (continued)**"
    messages: list[str] = []
    current = heading
    for raw_block in blocks:
        block = str(raw_block or "").strip()
        if not block:
            continue
        max_block = max(1, limit - len(continued_heading) - 2)
        for part in _split_oversized_block(block, max_block):
            candidate = f"{current}\n\n{part}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current != heading and current != continued_heading:
                messages.append(current)
            current = f"{continued_heading}\n\n{part}"
    if current != heading or not messages:
        messages.append(current)
    return messages


def _split_oversized_block(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    parts: list[str] = []
    current = ""
    for line in block.splitlines():
        if len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(line[index : index + limit] for index in range(0, len(line), limit))
            continue
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            parts.append(current)
            current = line
    if current:
        parts.append(current)
    return parts or [block[:limit]]


def _section_warnings(section: DailyReportSection) -> list[str]:
    warnings: list[str] = []
    for source in (
        (section.payload or {}).get("pipeline_warnings"),
        section.status.get("warnings"),
    ):
        values = source if isinstance(source, list) else []
        for warning in values:
            message = warning.get("message") if isinstance(warning, dict) else warning
            clean = _compact_text(message, 300)
            if clean and clean not in warnings:
                warnings.append(clean)
    return warnings


def _warning_blocks(section: DailyReportSection) -> list[str]:
    warnings = _section_warnings(section)
    if not warnings:
        return []
    displayed = warnings[:3]
    block = "⚠️ " + " · ".join(displayed)
    if len(warnings) > len(displayed):
        block += f" · {len(warnings) - len(displayed)} more warning(s)"
    return [block]


def _trending_change_highlight(row: dict[str, Any]) -> str:
    ticker = _compact_text(row.get("ticker") or "UNKNOWN", 20)
    platform = _platform_label(row.get("platform"))
    parts = []
    if row.get("mentions_delta_pct") is not None:
        parts.append(f"mentions {_signed_percent(row.get('mentions_delta_pct'))}")
    if row.get("bullish_delta_points") is not None:
        parts.append(f"bullish {_signed_points(row.get('bullish_delta_points'))}")
    if row.get("bearish_delta_points") is not None:
        parts.append(f"bearish {_signed_points(row.get('bearish_delta_points'))}")
    detail = " · ".join(parts) or _compact_text(row.get("change_type") or "significant change", 80)
    return f"• **{ticker}** · {platform} — {detail}"


def _trending_change_block(row: dict[str, Any]) -> str:
    ticker = _compact_text(row.get("ticker") or "UNKNOWN", 20)
    company = _compact_text(row.get("company_name"), 100)
    title = f"{ticker} — {company}" if company else ticker
    change = _compact_text(row.get("change_type") or "change", 80).title()
    current = _display_value(row.get("current_mentions"))
    previous = _display_value(row.get("previous_mentions"))
    mention_text = f"mentions `{previous} → {current}`"
    if row.get("change_type") == "darkhorse":
        mention_text = f"mentions `{current}` · no prior result"
    elif row.get("mentions_delta_pct") is not None:
        mention_text += f" · **{_signed_percent(row.get('mentions_delta_pct'))}**"
    sentiment = []
    if row.get("bullish_delta_points") is not None:
        sentiment.append(f"bullish **{_signed_points(row.get('bullish_delta_points'))}**")
    if row.get("bearish_delta_points") is not None:
        sentiment.append(f"bearish **{_signed_points(row.get('bearish_delta_points'))}**")
    second_line = mention_text + (" · " + " · ".join(sentiment) if sentiment else "")
    return f"• **{title}** · {_platform_label(row.get('platform'))} · {change}\n  {second_line}"


def _trending_stock_block(row: dict[str, Any]) -> str:
    ticker = _compact_text(row.get("ticker") or "UNKNOWN", 20)
    company = _compact_text(row.get("company_name"), 100)
    title = f"{ticker} — {company}" if company else ticker
    return (
        f"• **{title}** · {_platform_label(row.get('platform'))}\n"
        f"  Trend **{_display_value(row.get('trend'))}** · mentions `{_display_value(row.get('mentions'))}` · "
        f"bullish `{_display_percent(row.get('bullish_pct'))}` · bearish `{_display_percent(row.get('bearish_pct'))}`"
    )


def _trending_sector_block(row: dict[str, Any]) -> str:
    sector = _compact_text(row.get("sector") or "Unknown sector", 100)
    tickers = [str(value) for value in _list_value(row.get("top_tickers"))[:5]]
    ticker_text = ", ".join(f"`{ticker}`" for ticker in tickers) or "N/A"
    return (
        f"• **{sector}** · {_platform_label(row.get('platform'))}\n"
        f"  Top tickers {ticker_text} · trend **{_display_value(row.get('trend'))}** · "
        f"mentions `{_display_value(row.get('mentions'))}`"
    )


def _limited_platform_rows(value: Any, limit: int) -> list[dict[str, Any]]:
    rows = _dict_items(value)
    limited: list[dict[str, Any]] = []
    for platform in ("reddit", "x"):
        limited.extend([row for row in rows if str(row.get("platform") or "").lower() == platform][:limit])
    return limited


def _daily_social_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    summary = _dict_value(payload.get("summary"))
    items: list[dict[str, Any]] = []
    ordinal = 0
    for report in _dict_items(summary.get("x_reports")):
        handle = _compact_text(report.get("handle") or "unknown", 80)
        for post in _dict_items(report.get("posts")):
            items.append({**post, "_source_label": f"X @{handle}", "_ordinal": ordinal})
            ordinal += 1
    reddit_report = _dict_value(summary.get("reddit_report"))
    for post in _dict_items(reddit_report.get("posts")):
        subreddit = _compact_text(post.get("subreddit") or "Reddit", 80)
        if subreddit and subreddit != "Reddit" and not subreddit.startswith("r/"):
            subreddit = f"r/{subreddit}"
        items.append({**post, "_source_label": subreddit or "Reddit", "_ordinal": ordinal})
        ordinal += 1
    return items


def _social_signal_block(item: dict[str, Any]) -> str:
    sentiment = _compact_text(item.get("sentiment") or "unclear", 20).upper()
    title = _compact_text(item.get("title") or item.get("post_summary") or "Social signal", 140)
    source = _compact_text(item.get("_source_label") or "Social", 80)
    confidence = _compact_text(item.get("confidence") or "low", 20).lower()
    lines = [f"`{sentiment}` **{title}** · {source} · {confidence} confidence"]
    summary = _compact_text(item.get("post_summary") or item.get("summary") or item.get("claim"), 320)
    if summary:
        lines.append(f"> {summary}")
    interpretation = _compact_text(item.get("interpretation") or item.get("reason"), 260)
    if interpretation:
        lines.append(f"**Why it matters:** {interpretation}")
    tickers = [str(value).upper() for value in _list_value(item.get("tickers")) if value]
    if tickers:
        lines.append("**Tickers:** " + " ".join(f"`{ticker}`" for ticker in tickers[:8]))
    urls = [str(value) for value in _list_value(item.get("urls") or item.get("url")) if value]
    if urls:
        lines.append(f"[Source]({urls[0]})")
    return "\n".join(lines)


def _social_coverage_text(value: Any) -> str:
    windows = _dict_value(value)
    labels = []
    for kind, label in (("x", "X"), ("reddit", "Reddit")):
        lookbacks = []
        for config in _dict_value(windows.get(kind)).values():
            if isinstance(config, dict):
                hours = config.get("lookback_hours")
                if isinstance(hours, int) and hours > 0:
                    lookbacks.append(hours)
        if lookbacks:
            minimum, maximum = min(lookbacks), max(lookbacks)
            span = f"{minimum}h" if minimum == maximum else f"{minimum}–{maximum}h"
            labels.append(f"{label} `{span}`")
    return "Configured source lookback: " + " · ".join(labels) if labels else ""


def _trading_disclosure_block(row: dict[str, Any]) -> str:
    action = _trade_action(row.get("transaction_action") or row.get("transaction_type"))
    ticker = _compact_text(row.get("stock_ticker") or "NO TICKER", 24)
    name = _compact_text(row.get("name") or "Unknown filer", 120)
    filer_parts = [name]
    filer_parts.extend(
        _compact_text(row.get(key), 60) for key in ("status", "state") if row.get(key)
    )
    asset = _compact_text(row.get("asset") or _raw_cells_text(row), 260)
    details = []
    if asset:
        details.append(asset)
    if row.get("amount"):
        details.append(_compact_text(row.get("amount"), 80))
    if row.get("transaction_date"):
        details.append(f"transaction {_compact_text(row.get('transaction_date'), 40)}")
    if row.get("filing_date"):
        details.append(f"filed {_compact_text(row.get('filing_date'), 40)}")
    lines = [f"`{action}` **{ticker}** · {' / '.join(filer_parts)}"]
    if details:
        lines.append(" · ".join(details))
    if row.get("pdf_url"):
        lines.append(f"[PDF]({row['pdf_url']})")
    return "\n".join(lines)


def _trending_tickers(payload: dict[str, Any]) -> set[str]:
    summary = _dict_value(payload.get("summary"))
    tickers = set()
    for row in [*_dict_items(summary.get("changes")), *_dict_items(summary.get("stocks"))]:
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            tickers.add(ticker)
    return tickers


def _social_tickers(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(ticker).upper().strip()
        for item in items
        for ticker in _list_value(item.get("tickers"))
        if str(ticker).strip()
    }


def _sentiment_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {sentiment: 0 for sentiment in ("bullish", "bearish", "mixed", "neutral", "unclear")}
    for item in items:
        sentiment = str(item.get("sentiment") or "unclear").lower()
        counts[sentiment if sentiment in counts else "unclear"] += 1
    return counts


def _importance(item: dict[str, Any]) -> str:
    value = str(item.get("importance") or item.get("priority") or "medium").lower()
    if value.startswith("high"):
        return "high"
    if value.startswith("low"):
        return "low"
    return "medium"


def _confidence_rank(value: Any) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(value or "").lower(), 3)


def _trade_action(value: Any) -> str:
    text = str(value or "UNKNOWN").strip()
    normalized = text.lower()
    if normalized.startswith("p"):
        return "PURCHASE"
    if normalized.startswith("s"):
        return "SELL PARTIAL" if "partial" in normalized else "SELL"
    return text.upper() or "UNKNOWN"


def _raw_cells_text(row: dict[str, Any]) -> str:
    return " | ".join(str(value).strip() for value in _list_value(row.get("raw_cells"))[:4] if str(value).strip())


def _platform_label(value: Any) -> str:
    platform = str(value or "").lower()
    return {"reddit": "Reddit", "x": "X"}.get(platform, platform.title() or "Unknown source")


def _signed_percent(value: Any) -> str:
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _signed_points(value: Any) -> str:
    try:
        return f"{int(value):+d} pts"
    except (TypeError, ValueError):
        return "N/A"


def _display_percent(value: Any) -> str:
    return "N/A" if value is None else f"{value}%"


def _display_value(value: Any) -> str:
    return "N/A" if value in (None, "") else str(value)


def _compact_text(value: Any, limit: int) -> str:
    if value in (None, ""):
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in _list_value(value) if isinstance(item, dict)]


def _list_value(value: Any) -> list[Any]:
    if value in (None, "", {}, []):
        return []
    return value if isinstance(value, list) else [value]
