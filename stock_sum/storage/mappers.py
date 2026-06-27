"""Source-aware raw item storage mappers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem

X_SOURCE_TYPE = "x_user_timeline"
REDDIT_SOURCE_TYPE = "subreddit_posts"


@dataclass(frozen=True)
class SourceRow:
    """Source-specific table row data for a raw item."""

    table_name: str
    values: dict[str, Any]
    unique_columns: tuple[str, ...]


def metadata_json(item: RawItem) -> str:
    """Serialize raw item metadata deterministically."""

    return json.dumps(item.metadata, ensure_ascii=False, sort_keys=True)


def map_x_post(item: RawItem) -> SourceRow:
    """Map an X raw item to `raw_x_posts`."""

    handle = item.metadata.get("handle")
    if not handle:
        raise UnsupportedSourceTypeError("X raw items require metadata.handle.")

    return SourceRow(
        table_name="raw_x_posts",
        unique_columns=("handle", "status_id"),
        values={
            "status_id": item.source_id,
            "handle": handle,
            "author": item.metadata.get("author"),
            "posted_at_text": item.metadata.get("timestamp"),
            "url": item.url,
            "text": item.text,
            "metadata_json": metadata_json(item),
            "collected_at": item.collected_at.isoformat(),
        },
    )


def map_reddit_post(item: RawItem) -> SourceRow:
    """Map a planned Reddit raw item to `raw_reddit_posts`."""

    subreddit = item.metadata.get("subreddit")
    post_id = item.metadata.get("post_id") or item.source_id
    if not subreddit or not post_id:
        raise UnsupportedSourceTypeError("Reddit raw items require metadata.subreddit and source_id/post_id.")

    return SourceRow(
        table_name="raw_reddit_posts",
        unique_columns=("subreddit", "post_id"),
        values={
            "subreddit": subreddit,
            "post_id": post_id,
            "title": item.metadata.get("title"),
            "author": item.metadata.get("author"),
            "url": item.url,
            "text": item.text,
            "score": item.metadata.get("score"),
            "comment_count": item.metadata.get("comment_count"),
            "metadata_json": metadata_json(item),
            "collected_at": item.collected_at.isoformat(),
        },
    )


def map_raw_item(item: RawItem) -> SourceRow:
    """Map a raw item to its source-specific table row."""

    if item.source_type == X_SOURCE_TYPE:
        return map_x_post(item)
    if item.source_type == REDDIT_SOURCE_TYPE:
        return map_reddit_post(item)
    raise UnsupportedSourceTypeError(f"Unsupported raw item source type: {item.source_type}")
