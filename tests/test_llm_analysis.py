"""Chunked LLM analysis tests."""

from __future__ import annotations

import asyncio
import json
import re

from stock_sum.config.models import AppConfig, LLMConfig
from stock_sum.core.models import Summary
from stock_sum.core.summary_input import SummaryInput, SummaryXPost, SummaryXUserSection
from stock_sum.llm.analysis import LLMAnalysisService, build_analysis_chunks
from stock_sum.llm.prompts import build_analysis_chunk_messages


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


def test_analysis_prompt_requires_main_post_importance_without_comment_importance() -> None:
    messages = build_analysis_chunk_messages(
        {
            "kind": "reddit",
            "subreddit": "wallstreetbets",
            "post": {"id": "r1", "source_id": "abc", "comments": [{"id": "r1.c1", "source_id": "c1"}]},
        }
    )
    content = "\n".join(message["content"] for message in messages)

    assert '"importance": "high|medium|low"' in content
    assert "do not use confidence for importance" in content
    comment_schema = content.split('"comments":', 1)[1]
    assert '"importance"' not in comment_schema.split("Chunk JSON:", 1)[0]


async def test_llm_analysis_runs_chunks_with_bounded_concurrency() -> None:
    repository = FakeAnalysisRepository()
    llm = SlowFakeLLM()
    service = LLMAnalysisService(
        config=AppConfig(
            llm=LLMConfig(
                provider="deepseek",
                model="deepseek-v4-flash",
                api_key_env="DEEPSEEK_API_KEY",
                analysis_x_posts_per_chunk=1,
                analysis_max_concurrency=2,
            )
        ),
        repository=repository,
        llm_client=llm,
    )
    summary_input = SummaryInput(
        profile="default",
        generated_at="2026-06-30T00:00:00+00:00",
        collection_runs=[],
        x=[
            SummaryXUserSection(
                handle="example",
                posts=[
                    SummaryXPost(
                        status_id=str(index),
                        url=f"https://x.com/example/status/{index}",
                        text=f"post {index}",
                        author_handle="example",
                        author_name=None,
                        posted_at_text="2026-06-30T00:00:00+00:00",
                        collected_at="2026-06-30T00:00:00+00:00",
                        engagement={},
                    )
                    for index in range(3)
                ],
            )
        ],
    )

    result = await service.analyze(summary_input)

    assert llm.max_active == 2
    assert llm.calls == 3
    assert result.chunk_count == 3
    assert result.succeeded_count == 3
    assert result.failed_count == 0
    assert [row["source_ref"] for row in repository.x_rows] == ["x1", "x2", "x3"]
    assert {row["importance"] for row in repository.x_rows} == {"high"}
    assert repository.finished["status"] == "succeeded"


class SlowFakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def summarize(self, payload, instructions=None) -> Summary:
        return await self.complete_json([])

    async def complete_json(self, messages) -> Summary:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            payload = "\n".join(str(message.get("content", "")) for message in messages if isinstance(message, dict))
            matches = re.findall(r'"id"\s*:\s*"(x\d+)"', payload)
            source_ref = matches[-1] if matches else "x1"
            parsed = {
                "source": "x",
                "posts": [
                    {
                        "source_ref": source_ref,
                        "sentiment": "bullish",
                        "tags": ["market", "social", "signal", "risk", "watch"],
                        "summary": "summary",
                        "interpretation": "interpretation",
                        "importance": "high",
                        "confidence": "medium",
                    }
                ],
            }
            return Summary(text=json.dumps(parsed), model=self.model, metadata={"parsed": parsed})
        finally:
            self.active -= 1


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.x_rows = []
        self.reddit_post_rows = []
        self.reddit_comment_rows = []
        self.finished = {}

    async def start_llm_analysis_run(self, **kwargs):
        self.started = kwargs

    async def finish_llm_analysis_run(self, **kwargs):
        self.finished = kwargs

    async def save_llm_x_post_analyses(self, rows):
        self.x_rows.extend(rows)

    async def save_llm_reddit_post_analyses(self, rows):
        self.reddit_post_rows.extend(rows)

    async def save_llm_reddit_comment_analyses(self, rows):
        self.reddit_comment_rows.extend(rows)

    async def read_llm_analysis_report(self, *, profile: str, analysis_run_id: str | None = None) -> dict:
        return {"x_reports": [], "reddit_report": {"overall_summary": [], "posts": []}}
