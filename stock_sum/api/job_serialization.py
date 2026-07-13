"""Pure serialization and sorting helpers for HTTP jobs."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timezone
from typing import Any
import json

from stock_sum.api.job_store import _parse_utc_datetime


def _worker_error_detail(stdout: bytes, stderr: bytes) -> str:
    text = (stderr or stdout).decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-10:])
    return f"Worker failed:\n{tail}"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _safe_warning_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _pipeline_result_to_dict(result: Any) -> dict[str, Any]:
    data = asdict(result)
    data["collected_count"] = result.collected_count
    data["inserted_count"] = result.inserted_count
    data["updated_count"] = result.updated_count
    return data


def _warnings_to_dicts(warnings: list[Any]) -> list[dict[str, Any]]:
    return [asdict(warning) for warning in warnings]


def _summary_input_has_social_data(summary_input: Any) -> bool:
    for section in getattr(summary_input, "x", []):
        if getattr(section, "posts", []):
            return True
    for section in getattr(summary_input, "reddit", []):
        if getattr(section, "posts", []):
            return True
    return False


def _no_social_data_message(result: Any) -> str:
    failed = [run.collector_id for run in result.runs if run.status == "failed"]
    message = "Collection completed with no usable source data."
    if failed:
        message += f" Failed collectors: {', '.join(failed)}."
    return message


def _house_ptr_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "doc_id": row.doc_id,
            "year": row.year,
            "name": row.name,
            "status": row.status,
            "state": row.state,
            "filing_date": row.filing_date,
            "filing_date_utc": row.filing_date_utc,
            "pdf_url": row.pdf_url,
            "table_index": row.table_index,
            "row_index": row.row_index,
            "asset": row.asset,
            "asset_type_code": row.asset_type_code,
            "asset_type_label": row.asset_type_label,
            "stock_ticker": row.stock_ticker,
            "transaction_type": row.transaction_type,
            "transaction_date": row.transaction_date,
            "transaction_date_utc": row.transaction_date_utc,
            "transaction_action": row.transaction_action,
            "amount": row.amount,
            "raw_cells": row.raw_cells,
            "collected_at": row.collected_at,
        }
        for row in rows
    ]


def _sec_13f_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "dataset_id": row.dataset_id,
            "dataset_label": row.dataset_label,
            "accession_number": row.accession_number,
            "cik": row.cik,
            "manager_name": row.manager_name,
            "filing_date": row.filing_date,
            "filing_date_utc": row.filing_date_utc,
            "period_of_report": row.period_of_report,
            "period_of_report_utc": row.period_of_report_utc,
            "info_table_sk": row.info_table_sk,
            "issuer": row.issuer,
            "title_of_class": row.title_of_class,
            "cusip": row.cusip,
            "figi": row.figi,
            "value": row.value,
            "ssh_prn_amt": row.ssh_prn_amt,
            "ssh_prn_type": row.ssh_prn_type,
            "put_call": row.put_call,
            "investment_discretion": row.investment_discretion,
            "other_manager": row.other_manager,
            "voting_auth_sole": row.voting_auth_sole,
            "voting_auth_shared": row.voting_auth_shared,
            "voting_auth_none": row.voting_auth_none,
            "filing_url": row.filing_url,
        }
        for row in rows
    ]


def _adanos_stock_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "job_id": row.job_id,
            "platform": row.platform,
            "rank": row.rank,
            "window_from": row.window_from,
            "window_to": row.window_to,
            "ticker": row.ticker,
            "company_name": row.company_name,
            "trend": row.trend,
            "mentions": row.mentions,
            "bullish_pct": row.bullish_pct,
            "bearish_pct": row.bearish_pct,
            "sentiment_score": row.sentiment_score,
            "buzz_score": row.buzz_score,
            "trend_history": row.trend_history,
            "fetched_at": row.fetched_at,
        }
        for row in rows
    ]


def _adanos_sector_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "job_id": row.job_id,
            "platform": row.platform,
            "rank": row.rank,
            "window_from": row.window_from,
            "window_to": row.window_to,
            "sector": row.sector,
            "top_tickers": row.top_tickers,
            "trend": row.trend,
            "mentions": row.mentions,
            "bullish_pct": row.bullish_pct,
            "bearish_pct": row.bearish_pct,
            "sentiment_score": row.sentiment_score,
            "buzz_score": row.buzz_score,
            "fetched_at": row.fetched_at,
        }
        for row in rows
    ]


def _adanos_trending_change_dicts(
    current_rows: list[Any],
    prior_rows: list[Any],
    *,
    has_history: bool,
    mentions_change_pct: float,
    sentiment_change_pct: float,
    minimum_mentions: int,
) -> list[dict[str, Any]]:
    """Compare current Adanos stock rows to latest prior platform/ticker rows."""

    if not has_history:
        return []
    prior_by_key = {
        (str(row.platform).lower(), str(row.ticker).upper()): row
        for row in prior_rows
        if getattr(row, "platform", None) and getattr(row, "ticker", None)
    }
    changes: list[dict[str, Any]] = []
    for row in current_rows:
        current_mentions = _safe_int(getattr(row, "mentions", None))
        if current_mentions is None or current_mentions < minimum_mentions:
            continue
        key = (str(getattr(row, "platform", "")).lower(), str(getattr(row, "ticker", "")).upper())
        if not key[0] or not key[1]:
            continue
        prior = prior_by_key.get(key)
        if prior is None:
            changes.append(_adanos_darkhorse_change(row))
            continue
        mention_delta, mention_delta_pct, mention_flagged = _mentions_change(
            current_mentions,
            _safe_int(getattr(prior, "mentions", None)),
            threshold=mentions_change_pct,
        )
        bullish_delta, bullish_flagged = _sentiment_point_change(
            _safe_int(getattr(row, "bullish_pct", None)),
            _safe_int(getattr(prior, "bullish_pct", None)),
            threshold=sentiment_change_pct,
        )
        bearish_delta, bearish_flagged = _sentiment_point_change(
            _safe_int(getattr(row, "bearish_pct", None)),
            _safe_int(getattr(prior, "bearish_pct", None)),
            threshold=sentiment_change_pct,
        )
        change_parts: list[str] = []
        if mention_flagged:
            change_parts.append("mentions")
        if bullish_flagged or bearish_flagged:
            change_parts.append("sentiment")
        if not change_parts:
            continue
        changes.append(
            {
                "platform": row.platform,
                "ticker": row.ticker,
                "company_name": row.company_name,
                "change_type": " + ".join(change_parts),
                "current_mentions": current_mentions,
                "previous_mentions": _safe_int(getattr(prior, "mentions", None)),
                "mentions_delta": mention_delta,
                "mentions_delta_pct": mention_delta_pct,
                "current_bullish_pct": _safe_int(getattr(row, "bullish_pct", None)),
                "previous_bullish_pct": _safe_int(getattr(prior, "bullish_pct", None)),
                "bullish_delta_points": bullish_delta,
                "current_bearish_pct": _safe_int(getattr(row, "bearish_pct", None)),
                "previous_bearish_pct": _safe_int(getattr(prior, "bearish_pct", None)),
                "bearish_delta_points": bearish_delta,
                "current_fetched_at": row.fetched_at,
                "previous_fetched_at": prior.fetched_at,
                "current_window_from": row.window_from,
                "current_window_to": row.window_to,
                "previous_window_from": prior.window_from,
                "previous_window_to": prior.window_to,
            }
        )
    return sorted(changes, key=_adanos_trending_change_sort_key)


def _sort_house_ptr_rows(rows: list[Any], *, prefer_filing_date: bool = False) -> list[Any]:
    """Sort House PTR rows newest-first for final reports."""

    return sorted(rows, key=lambda row: _house_ptr_row_sort_key(row, prefer_filing_date=prefer_filing_date), reverse=True)


def _analysis_response_data(
    *,
    provider: str,
    analysis: Any,
    input_media: dict[str, Any],
) -> dict[str, Any]:
    return {
        "report_type": "social",
        "provider": provider,
        "model": analysis.model,
        "summary_text": json.dumps(analysis.summary, ensure_ascii=False),
        "summary": analysis.summary,
        "input_media": input_media,
        "metadata": {
            "analysis_run_id": analysis.analysis_run_id,
            "prompt_version": analysis.prompt_version,
            "chunk_count": analysis.chunk_count,
            "succeeded_count": analysis.succeeded_count,
            "failed_count": analysis.failed_count,
        },
    }


def _adanos_trending_change_sort_key(row: dict[str, Any]) -> tuple[float, str, str]:
    return (-_adanos_trending_change_score(row), str(row.get("platform") or ""), str(row.get("ticker") or ""))


def _adanos_trending_change_score(row: dict[str, Any]) -> float:
    mention_delta_pct = _safe_float(row.get("mentions_delta_pct"))
    mention_delta = _safe_float(row.get("mentions_delta"))
    sentiment_deltas = [
        abs(delta)
        for delta in (
            _safe_float(row.get("bullish_delta_points")),
            _safe_float(row.get("bearish_delta_points")),
        )
        if delta is not None
    ]
    components: list[float] = []
    if mention_delta_pct is not None:
        components.append(abs(mention_delta_pct))
    elif mention_delta is not None:
        components.append(abs(mention_delta))
    if sentiment_deltas:
        components.append(sum(sentiment_deltas) / len(sentiment_deltas))
    if not components:
        return 0.0
    return sum(components) / len(components)


def _adanos_darkhorse_change(row: Any) -> dict[str, Any]:
    return {
        "platform": row.platform,
        "ticker": row.ticker,
        "company_name": row.company_name,
        "change_type": "darkhorse",
        "current_mentions": _safe_int(getattr(row, "mentions", None)),
        "previous_mentions": None,
        "mentions_delta": None,
        "mentions_delta_pct": None,
        "current_bullish_pct": _safe_int(getattr(row, "bullish_pct", None)),
        "previous_bullish_pct": None,
        "bullish_delta_points": None,
        "current_bearish_pct": _safe_int(getattr(row, "bearish_pct", None)),
        "previous_bearish_pct": None,
        "bearish_delta_points": None,
        "current_fetched_at": row.fetched_at,
        "previous_fetched_at": None,
        "current_window_from": row.window_from,
        "current_window_to": row.window_to,
        "previous_window_from": None,
        "previous_window_to": None,
    }


def _mentions_change(current: int, previous: int | None, *, threshold: float) -> tuple[int | None, float | None, bool]:
    if previous is None:
        return None, None, False
    delta = current - previous
    if previous == 0:
        return delta, None, current != 0
    delta_pct = (delta / previous) * 100
    return delta, delta_pct, abs(delta_pct) >= threshold


def _sentiment_point_change(current: int | None, previous: int | None, *, threshold: float) -> tuple[int | None, bool]:
    if current is None or previous is None:
        return None, False
    delta = current - previous
    return delta, abs(delta) >= threshold


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _house_ptr_row_sort_key(row: Any, *, prefer_filing_date: bool = False) -> tuple[datetime, datetime, datetime, str, int, int]:
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    transaction_at = _parse_utc_datetime(getattr(row, "transaction_date_utc", None))
    if transaction_at is None:
        transaction_at = _parse_simple_date(getattr(row, "transaction_date", None)) or minimum
    filing_at = _parse_utc_datetime(getattr(row, "filing_date_utc", None))
    if filing_at is None:
        filing_at = _parse_simple_date(getattr(row, "filing_date", None)) or minimum
    collected_at = _parse_utc_datetime(getattr(row, "collected_at", None)) or minimum
    primary_at = filing_at if prefer_filing_date else transaction_at
    secondary_at = transaction_at if prefer_filing_date else filing_at
    return (
        primary_at,
        secondary_at,
        collected_at,
        str(getattr(row, "doc_id", "")),
        int(getattr(row, "table_index", 0) or 0),
        int(getattr(row, "row_index", 0) or 0),
    )


def _parse_simple_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed_date = datetime.strptime(text, fmt).date()
            return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
