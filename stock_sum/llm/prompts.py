"""Prompt builders for source-aware market summaries."""

from __future__ import annotations

from typing import Any
import json

from stock_sum.core.summary_input import SummaryInput

SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "x_reports": [
        {
            "handle": "X user handle",
            "overall_summary": ["User-level themes and market stance."],
            "posts": [
                {
                    "source_ref": "x post id such as x1",
                    "title": "Short post label.",
                    "post_summary": "What the post says.",
                    "sentiment": "bullish|bearish|mixed|neutral|unclear",
                    "interpretation": "Why it may matter.",
                    "confidence": "low|medium|high",
                    "media_ids": ["media ids linked to this source_ref, when relevant"],
                    "urls": ["source URL"],
                }
            ],
        }
    ],
    "reddit_report": {
        "overall_summary": ["Cross-post Reddit themes and broad sentiment."],
        "posts": [
            {
                "source_ref": "reddit post id such as r1",
                "title": "Short post label.",
                "post_summary": "What the main post says.",
                "comments_sentiment": "What the comments add and whether they agree, disagree, or joke.",
                "sentiment": "bullish|bearish|mixed|neutral|unclear",
                "interpretation": "Why the post and comments may matter.",
                "confidence": "low|medium|high",
                "media_ids": ["media ids linked to this source_ref, when relevant"],
                "comment_refs": ["comment ids such as r1.c1 when notable"],
                "urls": ["source URL"],
            }
        ],
    },
    "media_observations": [
        {
            "media_id": "media id such as m1",
            "source_ref": "linked source ref",
            "observation": "Only describe visible/linked media evidence when useful.",
        }
    ],
    "risks_or_uncertainties": ["Unsupported claims, missing data, conflicts, or likely noise."],
    "notable_sources": [{"source_ref": "x1", "url": "source URL", "reason": "Why it was notable."}],
    "metadata": {"summary_type": "market_social_digest", "not_financial_advice": True},
}

SYSTEM_PROMPT = """You are a concise market-intelligence analyst summarizing social-market signals.
Separate observed facts from interpretation. Do not provide financial advice, price targets, or trade instructions.
Use source refs and URLs from the payload for attribution. Treat social posts and comments as noisy, unverified signals.
Return valid JSON only. Do not include chain-of-thought or hidden reasoning."""

ANALYSIS_SYSTEM_PROMPT = """You are a concise market-intelligence analyst labeling noisy social-market posts.
Analyze only the provided chunk. Do not provide financial advice, price targets, or trade instructions.
Return valid JSON only. Use exactly the source_ref and source_id values from the input."""

X_ANALYSIS_SCHEMA: dict[str, Any] = {
    "source": "x",
    "posts": [
        {
            "source_ref": "x1",
            "source_id": "status id",
            "sentiment": "bullish|bearish|mixed|neutral|unclear",
            "tags": ["five", "single", "word", "lowercase", "tags"],
            "tickers": ["NBIS", "HOOD"],
            "summary": "One concise sentence describing the post.",
            "interpretation": "One concise sentence on possible market relevance.",
            "importance": "high|medium|low",
            "confidence": "low|medium|high",
        }
    ],
}

REDDIT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "source": "reddit",
    "post": {
        "source_ref": "r1",
        "source_id": "reddit post id",
        "sentiment": "bullish|bearish|mixed|neutral|unclear",
        "tags": ["five", "single", "word", "lowercase", "tags"],
        "tickers": ["NBIS", "HOOD"],
        "summary": "One concise sentence describing the main post.",
        "interpretation": "One concise sentence on post and comment relevance.",
        "importance": "high|medium|low",
        "confidence": "low|medium|high",
        "comments": [
            {
                "source_ref": "r1.c1",
                "source_id": "comment id",
                "parent": "parent id if present",
                "sentiment": "bullish|bearish|mixed|neutral|unclear",
                "summary": "One concise sentence describing the comment.",
                "confidence": "low|medium|high",
            }
        ],
    },
}


def build_trading_summary_messages(
    payload: SummaryInput | dict[str, Any],
    *,
    instructions: str | None = None,
) -> list[dict[str, str]]:
    """Build chat messages for a source-aware trading summary."""

    payload_dict = payload.to_dict(mode="compact") if isinstance(payload, SummaryInput) else payload
    user_sections = [
        "Summarize this source-separated market/social payload.",
        "Use this presentation structure: X reports grouped by user with one overall summary and per-post summaries; Reddit reports with one overall summary and per-post/comment sentiment.",
        "When a source item has media ids, include those ids in the matching signal so renderers can keep images beside the text.",
        "Keep every summary sentence short. Limit each X post and Reddit post object to concise fields, no source excerpts, no markdown, and no extra keys.",
        "Summarize at most five posts per X user and at most five Reddit posts. Prefer the most important posts if there are more.",
        "Return JSON exactly matching these top-level sections:",
        json.dumps(SUMMARY_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
        "Payload JSON:",
        json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":")),
    ]
    if instructions:
        user_sections.insert(1, f"Additional instructions: {instructions}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_sections)},
    ]


def build_analysis_chunk_messages(chunk: dict[str, Any], *, instructions: str | None = None) -> list[dict[str, str]]:
    """Build chat messages for one source chunk analysis."""

    kind = chunk.get("kind")
    schema = X_ANALYSIS_SCHEMA if kind == "x" else REDDIT_ANALYSIS_SCHEMA
    user_sections = [
        f"Analyze this {kind} chunk for market/social sentiment.",
        "Use sentiment only from: bullish, bearish, mixed, neutral, unclear.",
        "For every main post, set importance to high, medium, or low based on market relevance and urgency; do not use confidence for importance.",
        "For every main post, return exactly 5 lowercase single-word tags. Tags should be words like nbis, ai, semis, valuation.",
        "For every main post, return tickers as an array of uppercase stock symbols without $, or [] when none are clearly present.",
        "Ticker values may use . or - for valid symbols. Do not invent tickers from vague company mentions.",
        "For Reddit, analyze every provided comment and keep comment source_ref/source_id/parent links intact.",
        "Keep summaries and interpretations short. No markdown. No extra keys.",
        "Return JSON exactly matching this schema:",
        json.dumps(schema, ensure_ascii=False, indent=2),
        "Chunk JSON:",
        json.dumps(chunk, ensure_ascii=False, separators=(",", ":")),
    ]
    if instructions:
        user_sections.insert(1, f"Additional instructions: {instructions}")
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_sections)},
    ]
