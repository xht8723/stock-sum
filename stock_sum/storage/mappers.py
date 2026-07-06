"""Source-aware raw item mappers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json

from stock_sum.collectors.api.house import (
    HOUSE_PTR_SOURCE_TYPE,
    normalize_house_date,
    normalize_house_name,
    normalize_house_transaction_action,
    parse_house_asset_metadata,
)
from stock_sum.collectors.api.sec_13f import SEC_13F_SOURCE_TYPE, normalize_sec_date, normalize_sec_name, sec_filing_url
from stock_sum.collectors.api.xpoz import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE
from stock_sum.collectors.rss.x import X_RSS_SOURCE_TYPE
from stock_sum.collectors.rss.reddit import REDDIT_RSS_SOURCE_TYPE
from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem


@dataclass(frozen=True)
class MappedRawItem:
    """A raw item mapped to one source-specific table."""

    table: str
    key: tuple[Any, ...]
    row: dict[str, Any]
    media_rows: list[dict[str, Any]] = field(default_factory=list)


def metadata_json(item: RawItem) -> str:
    """Serialize item metadata for raw storage."""

    return json.dumps(item.metadata, ensure_ascii=False, sort_keys=True, default=str)


def raw_json(value: Any) -> str:
    """Serialize raw provider data for storage."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def map_raw_item(item: RawItem) -> MappedRawItem:
    """Map a raw item to a supported source-specific table."""

    if item.source_type in {X_SOURCE_TYPE, X_RSS_SOURCE_TYPE}:
        return _map_x_post(item)
    if item.source_type in {REDDIT_SOURCE_TYPE, REDDIT_RSS_SOURCE_TYPE}:
        entity_type = item.metadata.get("entity_type")
        if entity_type == "reddit_post":
            return _map_reddit_post(item)
        if entity_type == "reddit_comment":
            return _map_reddit_comment(item)
    if item.source_type == HOUSE_PTR_SOURCE_TYPE:
        return _map_house_ptr_filing(item)
    if item.source_type == SEC_13F_SOURCE_TYPE:
        return _map_sec_13f_dataset(item)
    raise UnsupportedSourceTypeError(f"Unsupported raw item source type: {item.source_type}")


def _map_x_post(item: RawItem) -> MappedRawItem:
    media_rows = [
        {
            "status_id": item.source_id,
            "media_key": media.get("media_key"),
            "media_type": media.get("media_type"),
            "media_url": media.get("url"),
            "alt_text": media.get("alt_text"),
            "raw_json": raw_json(media),
        }
        for media in item.metadata.get("media", [])
        if isinstance(media, dict) and media.get("url")
    ]
    return MappedRawItem(
        table="raw_x_posts",
        key=(item.metadata.get("handle"), item.source_id),
        row={
            "status_id": item.source_id,
            "handle": item.metadata.get("handle"),
            "author_handle": item.metadata.get("author_handle"),
            "author_name": item.metadata.get("author_name"),
            "posted_at_text": item.metadata.get("posted_at_text"),
            "url": item.url,
            "text": item.text,
            "reply_count": item.metadata.get("reply_count"),
            "repost_count": item.metadata.get("repost_count"),
            "like_count": item.metadata.get("like_count"),
            "quote_count": item.metadata.get("quote_count"),
            "view_count": item.metadata.get("view_count"),
            "raw_json": raw_json(item.metadata.get("raw", item.metadata)),
            "collected_at": item.collected_at.isoformat(),
            "posted_at_utc": _normalized_timestamp(item.metadata.get("posted_at_text")),
        },
        media_rows=media_rows,
    )


def _map_reddit_post(item: RawItem) -> MappedRawItem:
    media_rows = [
        {
            "post_id": item.source_id,
            "media_type": media.get("media_type"),
            "media_url": media.get("url"),
            "source_field": media.get("source_field"),
            "raw_json": raw_json(media),
        }
        for media in item.metadata.get("media", [])
        if isinstance(media, dict) and media.get("url")
    ]
    return MappedRawItem(
        table="raw_reddit_posts",
        key=(item.metadata.get("subreddit"), item.source_id),
        row={
            "post_id": item.source_id,
            "subreddit": item.metadata.get("subreddit"),
            "fullname": item.metadata.get("fullname"),
            "title": item.metadata.get("title"),
            "author": item.metadata.get("author"),
            "url": item.url,
            "permalink": item.metadata.get("permalink"),
            "selftext": item.text,
            "score": item.metadata.get("score"),
            "ups": item.metadata.get("ups"),
            "upvote_ratio": item.metadata.get("upvote_ratio"),
            "num_comments": item.metadata.get("num_comments"),
            "thumbnail_url": item.metadata.get("thumbnail_url"),
            "created_at_text": item.metadata.get("created_at_text"),
            "raw_json": raw_json(item.metadata.get("raw", item.metadata)),
            "collected_at": item.collected_at.isoformat(),
            "created_at_utc": _normalized_timestamp(item.metadata.get("created_at_text")),
        },
        media_rows=media_rows,
    )


def _map_reddit_comment(item: RawItem) -> MappedRawItem:
    return MappedRawItem(
        table="raw_reddit_comments",
        key=(item.metadata.get("post_id"), item.metadata.get("comment_id")),
        row={
            "comment_id": item.metadata.get("comment_id"),
            "post_id": item.metadata.get("post_id"),
            "parent_id": item.metadata.get("parent_id"),
            "author": item.metadata.get("author"),
            "body": item.metadata.get("body", item.text),
            "score": item.metadata.get("score"),
            "ups": item.metadata.get("ups"),
            "url": item.url,
            "created_at_text": item.metadata.get("created_at_text"),
            "depth": item.metadata.get("depth"),
            "raw_json": raw_json(item.metadata.get("raw", item.metadata)),
            "collected_at": item.collected_at.isoformat(),
            "created_at_utc": _normalized_timestamp(item.metadata.get("created_at_text")),
        },
    )


def _map_house_ptr_filing(item: RawItem) -> MappedRawItem:
    trade_rows = []
    for row in item.metadata.get("trade_rows", []):
        if not isinstance(row, dict):
            continue
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        asset_metadata = parse_house_asset_metadata(fields.get("asset"))
        trade_rows.append(
            {
                "doc_id": item.source_id,
                "table_index": row.get("table_index", 0),
                "row_index": row.get("row_index", 0),
                "asset": fields.get("asset"),
                "asset_type_code": fields.get("asset_type_code") or asset_metadata["asset_type_code"],
                "asset_type_label": fields.get("asset_type_label") or asset_metadata["asset_type_label"],
                "stock_ticker": fields.get("stock_ticker") or asset_metadata["stock_ticker"],
                "transaction_type": fields.get("transaction_type"),
                "transaction_date": fields.get("transaction_date"),
                "transaction_date_utc": normalize_house_date(fields.get("transaction_date")),
                "transaction_action": normalize_house_transaction_action(fields.get("transaction_type")),
                "amount": fields.get("amount"),
                "raw_cells_json": raw_json(row.get("cells", [])),
                "raw_json": raw_json(row),
            }
        )
    return MappedRawItem(
        table="raw_house_ptr_filings",
        key=(item.source_id,),
        row={
            "doc_id": item.source_id,
            "year": item.metadata.get("year"),
            "name": item.metadata.get("name"),
            "prefix": item.metadata.get("prefix"),
            "first_name": item.metadata.get("first_name"),
            "last_name": item.metadata.get("last_name"),
            "suffix": item.metadata.get("suffix"),
            "display_name": item.metadata.get("display_name") or item.metadata.get("name"),
            "name_normalized": item.metadata.get("name_normalized") or normalize_house_name(item.metadata.get("display_name") or item.metadata.get("name")),
            "status": item.metadata.get("status"),
            "state": item.metadata.get("state"),
            "filing_date": item.metadata.get("filing_date"),
            "filing_date_utc": item.metadata.get("filing_date_utc") or normalize_house_date(item.metadata.get("filing_date")),
            "pdf_url": item.metadata.get("pdf_url") or item.url,
            "raw_xml_json": raw_json(item.metadata.get("raw_xml", {})),
            "tables_json": raw_json(item.metadata.get("tables", [])),
            "extraction_status": item.metadata.get("extraction_status"),
            "extraction_error": item.metadata.get("extraction_error"),
            "collected_at": item.collected_at.isoformat(),
        },
        media_rows=trade_rows,
    )


def _map_sec_13f_dataset(item: RawItem) -> MappedRawItem:
    rows_by_table = item.metadata.get("rows_by_table") if isinstance(item.metadata.get("rows_by_table"), dict) else {}
    dataset_id = item.metadata.get("dataset_id") or item.source_id
    dataset_row = {
        "dataset_id": dataset_id,
        "label": item.metadata.get("label") or item.text,
        "download_url": item.metadata.get("download_url") or item.url,
        "sha256": item.metadata.get("sha256"),
        "byte_size": item.metadata.get("byte_size"),
        "row_counts_json": raw_json(item.metadata.get("row_counts", {})),
        "downloaded_at": item.collected_at.isoformat(),
    }
    child_rows: list[dict[str, Any]] = []
    for table_name, rows in rows_by_table.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            child_rows.append(_sec_13f_child_row(str(table_name), dataset_id, row))
    return MappedRawItem(
        table="raw_sec_13f_datasets",
        key=(dataset_id,),
        row=dataset_row,
        media_rows=child_rows,
    )


def _sec_13f_child_row(table_name: str, dataset_id: str, row: dict[str, Any]) -> dict[str, Any]:
    accession = _row_value(row, "ACCESSION_NUMBER")
    filing_date = _row_value(row, "FILING_DATE")
    period = _row_value(row, "PERIODOFREPORT")
    manager_name = _row_value(row, "FILINGMANAGER_NAME")
    cik = _row_value(row, "CIK")
    common = {
        "table_name": table_name,
        "dataset_id": dataset_id,
        "accession_number": accession,
        "raw_json": raw_json(row),
    }
    if table_name == "submissions":
        return {
            **common,
            "filing_date": filing_date,
            "filing_date_utc": normalize_sec_date(filing_date),
            "submission_type": _row_value(row, "SUBMISSIONTYPE"),
            "cik": cik,
            "period_of_report": period,
            "period_of_report_utc": normalize_sec_date(period),
        }
    if table_name == "coverpages":
        return {
            **common,
            "manager_name": manager_name,
            "manager_name_normalized": normalize_sec_name(manager_name),
            "report_type": _row_value(row, "REPORTTYPE"),
            "form_13f_file_number": _row_value(row, "FORM13FFILENUMBER"),
            "raw_json": raw_json(row),
        }
    if table_name == "info_tables":
        value = _int_or_none(_row_value(row, "VALUE"))
        shares = _int_or_none(_row_value(row, "SSHPRNAMT"))
        return {
            **common,
            "info_table_sk": _row_value(row, "INFOTABLE_SK"),
            "issuer": _row_value(row, "NAMEOFISSUER"),
            "issuer_normalized": normalize_sec_name(_row_value(row, "NAMEOFISSUER")),
            "title_of_class": _row_value(row, "TITLEOFCLASS"),
            "cusip": _row_value(row, "CUSIP").upper() or None,
            "figi": _row_value(row, "FIGI").upper() or None,
            "value": value,
            "ssh_prn_amt": shares,
            "ssh_prn_type": _row_value(row, "SSHPRNAMTTYPE"),
            "put_call": _row_value(row, "PUTCALL").upper() or None,
            "investment_discretion": _row_value(row, "INVESTMENTDISCRETION"),
            "other_manager": _row_value(row, "OTHERMANAGER"),
            "voting_auth_sole": _int_or_none(_row_value(row, "VOTING_AUTH_SOLE")),
            "voting_auth_shared": _int_or_none(_row_value(row, "VOTING_AUTH_SHARED")),
            "voting_auth_none": _int_or_none(_row_value(row, "VOTING_AUTH_NONE")),
        }
    return common


def _row_value(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _int_or_none(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _normalized_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromtimestamp(float(normalized), timezone.utc)
    except (OverflowError, OSError, ValueError):
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat()
