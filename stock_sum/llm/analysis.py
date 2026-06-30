"""Chunked LLM analysis service for source-level sentiment and tags."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import json

from stock_sum.config.models import AppConfig
from stock_sum.core.models import PipelineSectionWarning, Summary
from stock_sum.core.summary_input import SummaryInput
from stock_sum.llm.base import LLMClient
from stock_sum.llm.prompts import build_analysis_chunk_messages
from stock_sum.storage.repository import StorageRepository

PROMPT_VERSION = "llm-analysis-v1"
SENTIMENTS = {"bullish", "bearish", "mixed", "neutral", "unclear"}
CONFIDENCES = {"low", "medium", "high"}


@dataclass(frozen=True)
class AnalysisChunk:
    """One bounded payload chunk for structured LLM analysis."""

    kind: str
    key: str
    payload: dict[str, Any]


@dataclass
class LLMAnalysisResult:
    """Result of one chunked analysis run."""

    analysis_run_id: str
    profile: str
    provider: str
    model: str
    prompt_version: str
    summary: dict[str, Any]
    chunk_count: int
    succeeded_count: int
    failed_count: int
    warnings: list[PipelineSectionWarning] = field(default_factory=list)


class LLMAnalysisService:
    """Runs source chunks through an LLM and stores structured analyses."""

    def __init__(self, *, config: AppConfig, repository: StorageRepository, llm_client: LLMClient) -> None:
        self.config = config
        self.repository = repository
        self.llm_client = llm_client

    async def analyze(
        self,
        summary_input: SummaryInput,
        *,
        instructions: str | None = None,
        max_images_per_post: int = 3,
        max_images_total: int = 20,
    ) -> LLMAnalysisResult:
        """Analyze all social chunks, persist rows, and return renderer-ready data."""

        compact_payload = summary_input.to_dict(
            mode="compact",
            max_images_per_post=max_images_per_post,
            max_images_total=max_images_total,
        )
        chunks = build_analysis_chunks(
            compact_payload,
            x_posts_per_chunk=self.config.llm.analysis_x_posts_per_chunk,
            max_chars_per_chunk=self.config.llm.analysis_max_chars_per_chunk,
        )
        if not chunks:
            raise RuntimeError("No social chunks are available for LLM analysis.")

        analysis_run_id = uuid4().hex
        await self.repository.start_llm_analysis_run(
            analysis_run_id=analysis_run_id,
            profile=summary_input.profile,
            provider=self.llm_client.provider,
            model=self.llm_client.model,
            prompt_version=PROMPT_VERSION,
            instructions=instructions,
        )

        warnings: list[PipelineSectionWarning] = []
        succeeded_count = 0
        failed_count = 0
        fatal_error: str | None = None

        semaphore = asyncio.Semaphore(self.config.llm.analysis_max_concurrency)

        async def analyze_chunk(chunk: AnalysisChunk) -> tuple[AnalysisChunk, dict[str, Any] | None, Summary | None, Exception | None]:
            async with semaphore:
                try:
                    summary = await self.llm_client.complete_json(
                        build_analysis_chunk_messages(chunk.payload, instructions=instructions)
                    )
                    return chunk, _parsed_summary(summary), summary, None
                except Exception as exc:
                    return chunk, None, None, exc

        results = await asyncio.gather(*(analyze_chunk(chunk) for chunk in chunks))
        for chunk, parsed, summary, error in results:
            if error is None and parsed is not None and summary is not None:
                await self._persist_chunk(
                    analysis_run_id=analysis_run_id,
                    profile=summary_input.profile,
                    chunk=chunk,
                    parsed=parsed,
                    summary=summary,
                )
                succeeded_count += 1
                continue
            failed_count += 1
            fatal_error = str(error)
            warnings.append(
                PipelineSectionWarning(
                    section="llm_analysis",
                    source_id=chunk.key,
                    phase="analyzing",
                    message=str(error),
                )
            )

        status = "succeeded" if succeeded_count else "failed"
        await self.repository.finish_llm_analysis_run(
            analysis_run_id=analysis_run_id,
            status=status,
            chunk_count=len(chunks),
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            error_text=fatal_error if status == "failed" else None,
        )
        if succeeded_count == 0:
            raise RuntimeError(f"All LLM analysis chunks failed: {fatal_error or 'unknown error'}")

        summary = await self.repository.read_llm_analysis_report(
            profile=summary_input.profile,
            analysis_run_id=analysis_run_id,
        )
        return LLMAnalysisResult(
            analysis_run_id=analysis_run_id,
            profile=summary_input.profile,
            provider=self.llm_client.provider,
            model=self.llm_client.model,
            prompt_version=PROMPT_VERSION,
            summary=summary,
            chunk_count=len(chunks),
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            warnings=warnings,
        )

    async def _persist_chunk(
        self,
        *,
        analysis_run_id: str,
        profile: str,
        chunk: AnalysisChunk,
        parsed: dict[str, Any],
        summary: Summary,
    ) -> None:
        if chunk.kind == "x":
            await self.repository.save_llm_x_post_analyses(
                _x_analysis_rows(analysis_run_id, profile, chunk.payload, parsed, summary)
            )
            return
        if chunk.kind == "reddit":
            post_rows, comment_rows = _reddit_analysis_rows(analysis_run_id, profile, chunk.payload, parsed, summary)
            await self.repository.save_llm_reddit_post_analyses(post_rows)
            await self.repository.save_llm_reddit_comment_analyses(comment_rows)
            return
        raise ValueError(f"Unsupported analysis chunk kind: {chunk.kind}")


def build_analysis_chunks(
    compact_payload: dict[str, Any],
    *,
    x_posts_per_chunk: int,
    max_chars_per_chunk: int,
) -> list[AnalysisChunk]:
    """Build bounded source chunks from compact summary input."""

    chunks: list[AnalysisChunk] = []
    for section in compact_payload.get("sources", {}).get("x", []):
        if not isinstance(section, dict):
            continue
        handle = str(section.get("handle") or "unknown")
        current_posts: list[dict[str, Any]] = []
        for post in section.get("posts", []):
            if not isinstance(post, dict):
                continue
            candidate = [*current_posts, post]
            if current_posts and (
                len(candidate) > x_posts_per_chunk
                or _serialized_size({"handle": handle, "posts": candidate}) > max_chars_per_chunk
            ):
                chunks.append(_x_chunk(handle, current_posts))
                current_posts = [post]
            else:
                current_posts = candidate
        if current_posts:
            chunks.append(_x_chunk(handle, current_posts))

    for section in compact_payload.get("sources", {}).get("reddit", []):
        if not isinstance(section, dict):
            continue
        subreddit = str(section.get("subreddit") or "unknown")
        for post in section.get("posts", []):
            if isinstance(post, dict):
                chunks.append(_reddit_chunk(subreddit, post))
    return chunks


def _x_chunk(handle: str, posts: list[dict[str, Any]]) -> AnalysisChunk:
    payload = {"kind": "x", "handle": handle, "posts": posts}
    key = f"x.{handle}:{posts[0].get('id', 'chunk')}"
    return AnalysisChunk(kind="x", key=key, payload=payload)


def _reddit_chunk(subreddit: str, post: dict[str, Any]) -> AnalysisChunk:
    payload = {"kind": "reddit", "subreddit": subreddit, "post": post}
    return AnalysisChunk(kind="reddit", key=f"reddit.{subreddit}:{post.get('id') or post.get('source_id')}", payload=payload)


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _parsed_summary(summary: Summary) -> dict[str, Any]:
    parsed = summary.metadata.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    try:
        parsed = json.loads(summary.text)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM analysis response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM analysis response was not a JSON object.")
    return parsed


def _x_analysis_rows(
    analysis_run_id: str,
    profile: str,
    chunk: dict[str, Any],
    parsed: dict[str, Any],
    summary: Summary,
) -> list[dict[str, Any]]:
    posts_by_ref = {post.get("id"): post for post in chunk.get("posts", []) if isinstance(post, dict)}
    rows = []
    for post in parsed.get("posts", []):
        if not isinstance(post, dict):
            continue
        source_ref = str(post.get("source_ref") or "")
        source = posts_by_ref.get(source_ref, {})
        source_id = str(post.get("source_id") or source.get("source_id") or "")
        if not source_ref or not source_id:
            continue
        rows.append(
            {
                "analysis_run_id": analysis_run_id,
                "profile": profile,
                "handle": str(chunk.get("handle") or source.get("author") or "unknown"),
                "status_id": source_id,
                "source_ref": source_ref,
                "url": source.get("url"),
                "posted_at_text": source.get("time"),
                "sentiment": _sentiment(post.get("sentiment")),
                "tags_json": json.dumps(_tags(post.get("tags")), ensure_ascii=False),
                "summary": _text(post.get("summary")),
                "interpretation": _text(post.get("interpretation")),
                "confidence": _confidence(post.get("confidence")),
                "raw_response_json": _raw_response_json(summary),
                "analyzed_at": _utc_now(),
            }
        )
    return rows


def _reddit_analysis_rows(
    analysis_run_id: str,
    profile: str,
    chunk: dict[str, Any],
    parsed: dict[str, Any],
    summary: Summary,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    input_post = chunk.get("post") if isinstance(chunk.get("post"), dict) else {}
    parsed_post = parsed.get("post") if isinstance(parsed.get("post"), dict) else {}
    source_ref = str(parsed_post.get("source_ref") or input_post.get("id") or "")
    post_id = str(parsed_post.get("source_id") or input_post.get("source_id") or "")
    subreddit = str(chunk.get("subreddit") or input_post.get("subreddit") or "unknown")
    comment_rows = []
    counts = {sentiment: 0 for sentiment in ("bullish", "bearish", "mixed", "neutral", "unclear")}
    input_comments_by_ref = {
        comment.get("id"): comment for comment in input_post.get("comments", []) if isinstance(comment, dict)
    }
    for comment in parsed_post.get("comments", []):
        if not isinstance(comment, dict):
            continue
        comment_ref = str(comment.get("source_ref") or "")
        input_comment = input_comments_by_ref.get(comment_ref, {})
        comment_id = str(comment.get("source_id") or input_comment.get("source_id") or "")
        if not comment_ref or not comment_id:
            continue
        sentiment = _sentiment(comment.get("sentiment"))
        counts[sentiment] += 1
        comment_rows.append(
            {
                "analysis_run_id": analysis_run_id,
                "profile": profile,
                "subreddit": subreddit,
                "post_id": post_id,
                "comment_id": comment_id,
                "source_ref": comment_ref,
                "parent_id": comment.get("parent") or input_comment.get("parent"),
                "sentiment": sentiment,
                "summary": _text(comment.get("summary")),
                "confidence": _confidence(comment.get("confidence")),
                "raw_response_json": _raw_response_json(summary),
                "analyzed_at": _utc_now(),
            }
        )
    post_rows = []
    if source_ref and post_id:
        post_rows.append(
            {
                "analysis_run_id": analysis_run_id,
                "profile": profile,
                "subreddit": subreddit,
                "post_id": post_id,
                "source_ref": source_ref,
                "title": str(input_post.get("title") or parsed_post.get("title") or "Reddit post"),
                "url": input_post.get("url"),
                "created_at_text": input_post.get("time"),
                "sentiment": _sentiment(parsed_post.get("sentiment")),
                "tags_json": json.dumps(_tags(parsed_post.get("tags")), ensure_ascii=False),
                "summary": _text(parsed_post.get("summary")),
                "interpretation": _text(parsed_post.get("interpretation")),
                "confidence": _confidence(parsed_post.get("confidence")),
                "comment_sentiment_counts_json": json.dumps(counts, ensure_ascii=False, sort_keys=True),
                "raw_response_json": _raw_response_json(summary),
                "analyzed_at": _utc_now(),
            }
        )
    return post_rows, comment_rows


def _sentiment(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in SENTIMENTS else "unclear"


def _confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in CONFIDENCES else "low"


def _tags(value: Any) -> list[str]:
    tags = []
    iterable = value if isinstance(value, list) else []
    for item in iterable:
        tag = "".join(character for character in str(item).strip().lower() if character.isalnum())
        if tag and tag not in tags:
            tags.append(tag)
    while len(tags) < 5:
        fallback = ["market", "social", "signal", "risk", "watch"][len(tags)]
        if fallback not in tags:
            tags.append(fallback)
    return tags[:5]


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return text or "No clear analysis."


def _raw_response_json(summary: Summary) -> str:
    metadata = {key: value for key, value in summary.metadata.items() if key != "raw_response"}
    return json.dumps({"text": summary.text, "metadata": metadata}, ensure_ascii=False, sort_keys=True, default=str)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
