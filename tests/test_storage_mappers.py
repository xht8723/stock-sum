"""Source-aware storage mapper tests."""

import json

from stock_sum.core.errors import UnsupportedSourceTypeError
from stock_sum.core.models import RawItem
from stock_sum.storage.mappers import map_raw_item


def test_x_post_maps_to_source_table_and_media() -> None:
    item = RawItem(
        source_id="123",
        source_type="x_user_timeline",
        url="https://x.com/example/status/123",
        text="hello",
        metadata={
            "entity_type": "x_post",
            "handle": "example",
            "author_handle": "example",
            "media": [
                {
                    "media_type": "photo",
                    "url": "https://cdn.example/img.jpg",
                    "source_path": "legacy.entities.media",
                    "sizes": {"large": {"w": 1200, "h": 800}},
                    "raw": {"media_url_https": "https://cdn.example/img.jpg"},
                }
            ],
            "raw": {"id": "123"},
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_x_posts"
    assert mapped.key == ("example", "123")
    assert mapped.row["status_id"] == "123"
    assert mapped.media_rows[0]["media_url"] == "https://cdn.example/img.jpg"
    raw_media = json.loads(mapped.media_rows[0]["raw_json"])
    assert raw_media["source_path"] == "legacy.entities.media"
    assert raw_media["sizes"] == {"large": {"w": 1200, "h": 800}}


def test_reddit_post_maps_to_source_table_and_media() -> None:
    item = RawItem(
        source_id="abc",
        source_type="reddit_subreddit",
        url="https://www.reddit.com/r/wallstreetbets/comments/abc/post/",
        text="body",
        metadata={
            "entity_type": "reddit_post",
            "subreddit": "wallstreetbets",
            "title": "Post title",
            "media": [
                {
                    "media_type": "image",
                    "url": "https://preview.example/img.jpg",
                    "source_field": "preview.images.source",
                    "width": 1024,
                    "height": 768,
                    "raw": {"url": "https://preview.example/img.jpg"},
                }
            ],
            "raw": {"id": "abc"},
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_reddit_posts"
    assert mapped.key == ("wallstreetbets", "abc")
    assert mapped.row["title"] == "Post title"
    assert mapped.media_rows[0]["media_url"] == "https://preview.example/img.jpg"
    raw_media = json.loads(mapped.media_rows[0]["raw_json"])
    assert raw_media["source_field"] == "preview.images.source"
    assert raw_media["width"] == 1024


def test_reddit_comment_maps_to_source_table() -> None:
    item = RawItem(
        source_id="abc:def",
        source_type="reddit_subreddit",
        url="https://www.reddit.com/r/wallstreetbets/comments/abc/post/def/",
        text="comment",
        metadata={
            "entity_type": "reddit_comment",
            "post_id": "abc",
            "comment_id": "def",
            "body": "comment",
            "raw": {"id": "def"},
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_reddit_comments"
    assert mapped.key == ("abc", "def")
    assert mapped.row["body"] == "comment"


def test_house_ptr_filing_maps_to_source_tables() -> None:
    item = RawItem(
        source_id="20024228",
        source_type="house_ptr_disclosures",
        url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
        text="Jane Doe House PTR disclosure",
        metadata={
            "entity_type": "house_ptr_filing",
            "doc_id": "20024228",
            "year": 2026,
            "name": "Jane Doe",
            "status": "Member",
            "state": "CA",
            "filing_date": "2026-06-30",
            "pdf_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
            "raw_xml": {"DocID": "20024228"},
            "tables": [[["Asset", "Type"], ["AAPL", "Purchase"]]],
            "trade_rows": [
                {
                    "table_index": 0,
                    "row_index": 0,
                    "cells": ["AAPL", "Purchase"],
                    "fields": {
                        "asset": "Apple Inc. - Common Stock (AAPL) [ST]",
                        "transaction_type": "Purchase",
                        "transaction_date": "2026-06-20",
                        "amount": "$1,001 - $15,000",
                    },
                }
            ],
            "extraction_status": "succeeded",
            "extraction_error": None,
        },
    )

    mapped = map_raw_item(item)

    assert mapped.table == "raw_house_ptr_filings"
    assert mapped.key == ("20024228",)
    assert mapped.row["name"] == "Jane Doe"
    assert mapped.media_rows[0]["asset"] == "Apple Inc. - Common Stock (AAPL) [ST]"
    assert mapped.media_rows[0]["asset_type_code"] == "ST"
    assert mapped.media_rows[0]["asset_type_label"] == "Stocks, including ADRs"
    assert mapped.media_rows[0]["stock_ticker"] == "AAPL"
    assert json.loads(mapped.media_rows[0]["raw_cells_json"]) == ["AAPL", "Purchase"]


def test_unsupported_source_type_raises() -> None:
    item = RawItem(source_id="1", source_type="generic_api", url=None, text="data")

    try:
        map_raw_item(item)
    except UnsupportedSourceTypeError as exc:
        assert "Unsupported raw item source type" in str(exc)
    else:
        raise AssertionError("unsupported source type should raise")
