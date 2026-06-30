"""Chunked LLM analysis tests."""

from __future__ import annotations

from stock_sum.llm.analysis import build_analysis_chunks


def test_build_analysis_chunks_splits_x_posts_by_count() -> None:
    payload = {
        "sources": {
            "x": [
                {
                    "handle": "example",
                    "posts": [
                        {"id": "x1", "source_id": "1", "text": "one"},
                        {"id": "x2", "source_id": "2", "text": "two"},
                        {"id": "x3", "source_id": "3", "text": "three"},
                    ],
                }
            ],
            "reddit": [],
        }
    }

    chunks = build_analysis_chunks(payload, x_posts_per_chunk=2, max_chars_per_chunk=12000)

    assert [chunk.kind for chunk in chunks] == ["x", "x"]
    assert [post["source_id"] for post in chunks[0].payload["posts"]] == ["1", "2"]
    assert [post["source_id"] for post in chunks[1].payload["posts"]] == ["3"]


def test_build_analysis_chunks_creates_one_reddit_chunk_per_post() -> None:
    payload = {
        "sources": {
            "x": [],
            "reddit": [
                {
                    "subreddit": "wallstreetbets",
                    "posts": [
                        {"id": "r1", "source_id": "a", "comments": [{"id": "r1.c1", "source_id": "c1"}]},
                        {"id": "r2", "source_id": "b", "comments": []},
                    ],
                }
            ],
        }
    }

    chunks = build_analysis_chunks(payload, x_posts_per_chunk=10, max_chars_per_chunk=12000)

    assert [chunk.kind for chunk in chunks] == ["reddit", "reddit"]
    assert chunks[0].payload["post"]["source_id"] == "a"
    assert chunks[0].payload["post"]["comments"][0]["source_id"] == "c1"
