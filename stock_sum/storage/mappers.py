"""Source-aware raw item mappers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json

from stock_sum.collectors.api.scrape_creators import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE
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

    if item.source_type == X_SOURCE_TYPE:
        return _map_x_post(item)
    if item.source_type == REDDIT_SOURCE_TYPE:
        entity_type = item.metadata.get("entity_type")
        if entity_type == "reddit_post":
            return _map_reddit_post(item)
        if entity_type == "reddit_comment":
            return _map_reddit_comment(item)
    raise UnsupportedSourceTypeError(f"Unsupported raw item source type: {item.source_type}")


def _map_x_post(item: RawItem) -> MappedRawItem:
    media_rows = [
        {
            "status_id": item.source_id,
            "media_key": media.get("media_key"),
            "media_type": media.get("media_type"),
            "media_url": media.get("url"),
            "alt_text": media.get("alt_text"),
            "raw_json": raw_json(media.get("raw", media)),
        }
        for media in item.metadata.get("media", [])
        if isinstance(media, dict) and media.get("url")
    ]
    return MappedRawItem(
        table="raw_scrape_creators_x_posts",
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
            "raw_json": raw_json(media.get("raw", media)),
        }
        for media in item.metadata.get("media", [])
        if isinstance(media, dict) and media.get("url")
    ]
    return MappedRawItem(
        table="raw_scrape_creators_reddit_posts",
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
        },
        media_rows=media_rows,
    )


def _map_reddit_comment(item: RawItem) -> MappedRawItem:
    return MappedRawItem(
        table="raw_scrape_creators_reddit_comments",
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
        },
    )
