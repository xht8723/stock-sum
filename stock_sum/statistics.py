"""Statistical aggregation and PNG plotting for stock-sum data."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
import math

from stock_sum.statistic_windowing import (
    SENTIMENT_SCORES,
    bucket_key,
    bucket_keys_between,
    estimate_amount,
    normalize_action,
    normalize_sentiment,
    parse_date,
    parse_datetime,
    resolve_bucket,
    statistic_window,
)
from stock_sum.storage.models import StoredSocialStatisticPoint, StoredTradingStatisticPoint

StatisticMode = Literal["social", "trading"]
StatisticBucket = Literal["auto", "day", "week", "month"]


def build_social_statistic_summary(
    points: list[StoredSocialStatisticPoint],
    *,
    filters: dict[str, Any],
    bucket: StatisticBucket = "auto",
    title: str = "Social Sentiment Statistic",
) -> dict[str, Any]:
    """Aggregate analyzed social sentiment into time buckets."""

    dated: list[tuple[datetime, StoredSocialStatisticPoint]] = []
    skipped_missing_date = 0
    for point in points:
        posted_at = parse_datetime(point.posted_at)
        if posted_at is None:
            skipped_missing_date += 1
            continue
        dated.append((posted_at, point))
    if not dated:
        raise ValueError("No social statistic rows had usable post dates.")

    window_start, window_end = statistic_window(filters, dated)
    resolved_bucket = resolve_bucket(bucket, window_start, window_end)
    grouped: dict[str, dict[str, Any]] = {}
    for key in bucket_keys_between(window_start, window_end, resolved_bucket):
        grouped[key] = _empty_social_bucket(key)
    for posted_at, point in dated:
        key = bucket_key(posted_at, resolved_bucket)
        group = grouped.setdefault(key, _empty_social_bucket(key))
        sentiment = normalize_sentiment(point.sentiment)
        group["post_count"] += 1
        group["sentiment_score_sum"] += SENTIMENT_SCORES[sentiment]
        group["sentiment_counts"][sentiment] += 1
        group["sources"][point.source] = group["sources"].get(point.source, 0) + 1

    buckets = []
    for key in sorted(grouped):
        group = grouped[key]
        count = int(group["post_count"])
        score_sum = float(group.pop("sentiment_score_sum"))
        group["avg_sentiment_score"] = round(score_sum / count, 4) if count else 0.0
        buckets.append(group)

    return {
        "report_type": "statistic",
        "statistic_mode": "social",
        "title": title,
        "bucket": resolved_bucket,
        "filters": filters,
        "date_range": {"start": window_start.date().isoformat(), "end": window_end.date().isoformat()},
        "row_count": len(points),
        "plotted_count": len(dated),
        "skipped": {"missing_date": skipped_missing_date},
        "buckets": buckets,
    }


def build_trading_statistic_summary(
    points: list[StoredTradingStatisticPoint],
    *,
    filters: dict[str, Any],
    bucket: StatisticBucket = "auto",
    title: str = "Financial Disclosure Statistic",
) -> dict[str, Any]:
    """Aggregate House PTR trading rows into time buckets."""

    dated: list[tuple[datetime, StoredTradingStatisticPoint]] = []
    skipped_missing_date = 0
    for point in points:
        traded_at = parse_datetime(point.transaction_date_utc) or parse_date(point.transaction_date)
        if traded_at is None:
            skipped_missing_date += 1
            continue
        dated.append((traded_at, point))
    if not dated:
        raise ValueError("No trading statistic rows had usable transaction dates.")

    window_start, window_end = statistic_window(filters, dated)
    resolved_bucket = resolve_bucket(bucket, window_start, window_end)
    grouped: dict[str, dict[str, Any]] = {}
    for key in bucket_keys_between(window_start, window_end, resolved_bucket):
        grouped[key] = _empty_trading_bucket(key)
    skipped_unknown_amount = 0
    open_ended_amounts = 0
    unknown_actions = 0
    for traded_at, point in dated:
        action = normalize_action(point.transaction_action)
        if action not in {"purchase", "sell", "sell_partial"}:
            unknown_actions += 1
            continue
        estimate, open_ended = estimate_amount(point.amount)
        if estimate is None:
            skipped_unknown_amount += 1
            estimate = 0.0
        elif open_ended:
            open_ended_amounts += 1

        key = bucket_key(traded_at, resolved_bucket)
        group = grouped.setdefault(key, _empty_trading_bucket(key))
        if action == "purchase":
            group["purchase_count"] += 1
            group["purchase_estimated_usd"] += estimate
            group["net_estimated_usd"] += estimate
        else:
            group["sell_count"] += 1
            group["sell_estimated_usd"] += estimate
            group["net_estimated_usd"] -= estimate

    buckets = []
    for key in sorted(grouped):
        group = grouped[key]
        for money_key in ("purchase_estimated_usd", "sell_estimated_usd", "net_estimated_usd"):
            group[money_key] = round(float(group[money_key]), 2)
        group["net_trade_count"] = int(group["purchase_count"]) - int(group["sell_count"])
        buckets.append(group)

    return {
        "report_type": "statistic",
        "statistic_mode": "trading",
        "title": title,
        "bucket": resolved_bucket,
        "filters": filters,
        "date_range": {"start": window_start.date().isoformat(), "end": window_end.date().isoformat()},
        "row_count": len(points),
        "plotted_count": sum(item["purchase_count"] + item["sell_count"] for item in buckets),
        "skipped": {
            "missing_date": skipped_missing_date,
            "unknown_amount": skipped_unknown_amount,
            "unknown_action": unknown_actions,
            "open_ended_amount": open_ended_amounts,
        },
        "buckets": buckets,
    }


def render_statistic_png(summary: dict[str, Any], output_path: Path) -> None:
    """Render statistic summary data to a PNG file."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if summary.get("statistic_mode") == "trading":
        _render_trading_png(summary, output_path, plt)
        return
    _render_social_png(summary, output_path, plt)


def _empty_social_bucket(key: str) -> dict[str, Any]:
    return {
        "bucket": key,
        "post_count": 0,
        "sentiment_score_sum": 0.0,
        "sentiment_counts": {sentiment: 0 for sentiment in SENTIMENT_SCORES},
        "sources": {"x": 0, "reddit": 0},
    }


def _empty_trading_bucket(key: str) -> dict[str, Any]:
    return {
        "bucket": key,
        "purchase_count": 0,
        "sell_count": 0,
        "purchase_estimated_usd": 0.0,
        "sell_estimated_usd": 0.0,
        "net_estimated_usd": 0.0,
    }


def _render_social_png(summary: dict[str, Any], output_path: Path, plt: Any) -> None:
    buckets = summary.get("buckets") or []
    labels = [item["bucket"] for item in buckets]
    scores = [item["avg_sentiment_score"] for item in buckets]
    counts = [item["post_count"] for item in buckets]

    fig, ax_score = plt.subplots(figsize=(11, 6.1), facecolor="#1f2329")
    ax_score.set_facecolor("#252a31")
    ax_count = ax_score.twinx()
    x_positions = list(range(len(labels)))
    count_bars = ax_count.bar(x_positions, counts, color="#7aa2f7", alpha=0.22, label="Post count")
    ax_score.plot(x_positions, scores, color="#a6e3a1", marker="o", linewidth=2.2, label="Average sentiment")
    ax_score.axhline(0, color="#c0caf5", linewidth=0.8, alpha=0.45)
    _style_axes(ax_score, ax_count)
    ax_score.set_ylim(-1.05, 1.05)
    ax_score.set_ylabel("Sentiment score", color="#f4f4f5")
    ax_count.set_ylabel("Post count", color="#f4f4f5")
    ax_score.set_title(_chart_title(summary, default_title="Social Sentiment Statistic"), color="#f4f4f5", pad=14)
    _apply_x_labels(ax_score, labels, x_positions)
    _annotate_bars(ax_count, count_bars, counts, formatter=lambda value: f"{int(value)} posts", color="#c0caf5")
    _annotate_points(ax_score, x_positions, scores)
    _combined_legend(fig, ax_score, ax_count)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def _render_trading_png(summary: dict[str, Any], output_path: Path, plt: Any) -> None:
    buckets = summary.get("buckets") or []
    labels = [item["bucket"] for item in buckets]
    x_positions = list(range(len(labels)))
    purchase_usd = [item["purchase_estimated_usd"] for item in buckets]
    sell_usd = [-item["sell_estimated_usd"] for item in buckets]
    purchase_count = [item["purchase_count"] for item in buckets]
    sell_count = [-item["sell_count"] for item in buckets]
    bar_width = 0.38
    purchase_positions = [position - bar_width / 2 for position in x_positions]
    sell_positions = [position + bar_width / 2 for position in x_positions]

    fig, (ax_usd, ax_count) = plt.subplots(2, 1, figsize=(11, 8.1), facecolor="#1f2329", sharex=True)
    for ax in (ax_usd, ax_count):
        ax.set_facecolor("#252a31")
        ax.axhline(0, color="#c0caf5", linewidth=0.8, alpha=0.45)
        _style_single_axis(ax)

    purchase_usd_bars = ax_usd.bar(purchase_positions, purchase_usd, width=bar_width, color="#2dd4bf", alpha=0.8, label="Purchases est. USD")
    sell_usd_bars = ax_usd.bar(sell_positions, sell_usd, width=bar_width, color="#f59e0b", alpha=0.8, label="Sales est. USD")
    purchase_count_bars = ax_count.bar(purchase_positions, purchase_count, width=bar_width, color="#2dd4bf", alpha=0.8, label="Purchase count")
    sell_count_bars = ax_count.bar(sell_positions, sell_count, width=bar_width, color="#f59e0b", alpha=0.8, label="Sale count")
    uses_log_scale = _apply_usd_axis_scale(ax_usd, purchase_usd + sell_usd)
    ax_usd.set_ylabel("Estimated USD", color="#f4f4f5")
    if uses_log_scale:
        ax_usd.set_ylabel("Estimated USD (symmetric log)", color="#f4f4f5")
    ax_count.set_ylabel("Trade count", color="#f4f4f5")
    ax_usd.set_title(_chart_title(summary, default_title="Financial Disclosure Statistic"), color="#f4f4f5", pad=14)
    _apply_x_labels(ax_count, labels, x_positions)
    _annotate_bars(ax_usd, purchase_usd_bars, purchase_usd, formatter=_format_usd_compact, color="#d1fae5")
    _annotate_bars(ax_usd, sell_usd_bars, sell_usd, formatter=_format_usd_compact, color="#ffedd5")
    _annotate_bars(ax_count, purchase_count_bars, purchase_count, formatter=lambda value: f"{int(abs(value))} buys", color="#d1fae5")
    _annotate_bars(ax_count, sell_count_bars, sell_count, formatter=lambda value: f"{int(abs(value))} sells", color="#ffedd5")
    ax_usd.legend(loc="best", facecolor="#252a31", edgecolor="#4b5563", labelcolor="#f4f4f5")
    ax_count.legend(loc="best", facecolor="#252a31", edgecolor="#4b5563", labelcolor="#f4f4f5")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def _style_axes(primary: Any, secondary: Any) -> None:
    _style_single_axis(primary)
    _style_single_axis(secondary)
    primary.grid(True, axis="y", alpha=0.2, color="#c0caf5")
    secondary.grid(False)


def _style_single_axis(ax: Any) -> None:
    ax.tick_params(colors="#f4f4f5")
    for spine in ax.spines.values():
        spine.set_color("#4b5563")
    ax.yaxis.label.set_color("#f4f4f5")
    ax.xaxis.label.set_color("#f4f4f5")
    ax.grid(True, axis="y", alpha=0.18, color="#c0caf5")


def _apply_x_labels(ax: Any, labels: list[str], x_positions: list[int]) -> None:
    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels, rotation=35, ha="right", color="#f4f4f5")
    if len(labels) > 18:
        step = max(1, len(labels) // 12)
        for index, label in enumerate(ax.get_xticklabels()):
            label.set_visible(index % step == 0 or index == len(labels) - 1)


def _combined_legend(fig: Any, primary: Any, secondary: Any) -> None:
    handles1, labels1 = primary.get_legend_handles_labels()
    handles2, labels2 = secondary.get_legend_handles_labels()
    fig.legend(
        handles1 + handles2,
        labels1 + labels2,
        loc="upper right",
        bbox_to_anchor=(0.96, 0.93),
        facecolor="#252a31",
        edgecolor="#4b5563",
        labelcolor="#f4f4f5",
    )


def _chart_title(summary: dict[str, Any], *, default_title: str) -> str:
    title = str(summary.get("title") or default_title).strip() or default_title
    buckets = summary.get("buckets") or []
    bucket = str(summary.get("bucket") or "bucket")
    date_range = _summary_date_range(summary, buckets)
    filters = _format_filter_summary(summary.get("filters") or {})
    if summary.get("statistic_mode") == "trading":
        total_buys = sum(int(item.get("purchase_count") or 0) for item in buckets)
        total_sells = sum(int(item.get("sell_count") or 0) for item in buckets)
        subtitle = (
            f"{filters} | {date_range} | {bucket} buckets | "
            f"{total_buys} purchases, {total_sells} sales"
        )
    else:
        total_posts = sum(int(item.get("post_count") or 0) for item in buckets)
        subtitle = f"{filters} | {date_range} | {bucket} buckets | {total_posts} analyzed posts"
    return f"{title}\n{subtitle}"


def _summary_date_range(summary: dict[str, Any], buckets: list[dict[str, Any]]) -> str:
    explicit = summary.get("date_range")
    if isinstance(explicit, dict):
        start = str(explicit.get("start") or "").strip()
        end = str(explicit.get("end") or "").strip()
        if start and end:
            return f"{start} to {end}"
    labels = [str(item.get("bucket") or "") for item in buckets if item.get("bucket")]
    return f"{labels[0]} to {labels[-1]}" if labels else "no dated rows"


def _format_filter_summary(filters: dict[str, Any]) -> str:
    displayed: list[str] = []
    for key in (
        "ticker",
        "name",
        "asset_type",
        "action",
        "source",
        "sentiment",
        "days",
        "start_date",
        "end_date",
    ):
        value = filters.get(key)
        if value in (None, "", "all"):
            continue
        label = key.replace("_", " ")
        displayed.append(f"{label}: {value}")
    return ", ".join(displayed) if displayed else "all matching records"


def _apply_usd_axis_scale(ax: Any, values: list[float | int]) -> bool:
    nonzero = [abs(float(value)) for value in values if value]
    if len(nonzero) < 2:
        return False
    smallest = min(nonzero)
    largest = max(nonzero)
    if smallest <= 0 or largest / smallest < 100:
        return False
    linthresh = max(1000.0, smallest)
    ax.set_yscale("symlog", linthresh=linthresh)
    _set_symlog_usd_ticks(ax, values, linthresh=linthresh)
    ax.margins(y=0.25)
    return True


def _set_symlog_usd_ticks(ax: Any, values: list[float | int], *, linthresh: float) -> None:
    positives = [float(value) for value in values if value > 0]
    negatives = [abs(float(value)) for value in values if value < 0]
    ticks = [-tick for tick in reversed(_log_money_ticks(max(negatives, default=0.0), linthresh=linthresh))]
    ticks.append(0.0)
    ticks.extend(_log_money_ticks(max(positives, default=0.0), linthresh=linthresh))
    ax.set_yticks(ticks)
    ax.set_yticklabels([_format_usd_compact(tick) for tick in ticks])


def _log_money_ticks(max_value: float, *, linthresh: float) -> list[float]:
    if max_value <= 0:
        return []
    first = 10 ** math.ceil(math.log10(max(linthresh, 1.0)))
    ticks = []
    value = float(first)
    while value <= max_value * 1.05:
        ticks.append(value)
        value *= 10
    if not ticks:
        ticks.append(max_value)
    return ticks


def _annotate_bars(ax: Any, bars: Any, values: list[float | int], *, formatter: Any, color: str) -> None:
    if not bars:
        return
    if ax.get_yscale() == "symlog":
        _annotate_symlog_bars(ax, bars, values, formatter=formatter, color=color)
        return
    y_min, y_max = ax.get_ylim()
    offset = (y_max - y_min) * 0.025 if y_max != y_min else 0.1
    ax.set_ylim(y_min - offset * 2, y_max + offset * 2)
    for bar, value in zip(bars, values, strict=False):
        if value == 0:
            continue
        height = float(bar.get_height())
        x = bar.get_x() + bar.get_width() / 2
        if height >= 0:
            y = height + offset
            va = "bottom"
        else:
            y = height - offset
            va = "top"
        ax.text(
            x,
            y,
            formatter(value),
            ha="center",
            va=va,
            color=color,
            fontsize=8,
            rotation=0,
            clip_on=False,
        )


def _annotate_symlog_bars(ax: Any, bars: Any, values: list[float | int], *, formatter: Any, color: str) -> None:
    for bar, value in zip(bars, values, strict=False):
        if value == 0:
            continue
        height = float(bar.get_height())
        x = bar.get_x() + bar.get_width() / 2
        y = height * 1.18
        va = "bottom" if height >= 0 else "top"
        ax.text(
            x,
            y,
            formatter(value),
            ha="center",
            va=va,
            color=color,
            fontsize=8,
            rotation=0,
            clip_on=False,
        )


def _annotate_points(ax: Any, x_positions: list[int], values: list[float]) -> None:
    for x, value in zip(x_positions, values, strict=False):
        offset = 0.08 if value < 0.9 else -0.12
        va = "bottom" if offset > 0 else "top"
        ax.text(
            x,
            value + offset,
            f"{value:+.2f}",
            ha="center",
            va=va,
            color="#d9f99d",
            fontsize=8,
            clip_on=False,
        )


def _format_usd_compact(value: float | int) -> str:
    absolute = abs(float(value))
    sign = "-" if float(value) < 0 else ""
    if absolute >= 1_000_000_000:
        return f"{sign}${absolute / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{sign}${absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}${absolute / 1_000:.1f}K"
    return f"{sign}${absolute:.0f}"
