"""SQLite write helpers for stored LLM analysis rows."""

from __future__ import annotations

from typing import Any

import aiosqlite

from stock_sum.storage.json_codec import _normalized_tickers


async def _replace_llm_x_ticker_rows(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        DELETE FROM llm_x_post_tickers
        WHERE analysis_run_id = ? AND status_id = ?
        """,
        (row["analysis_run_id"], row["status_id"]),
    )
    for ticker in _normalized_tickers(row.get("tickers_json")):
        await db.execute(
            """
            INSERT INTO llm_x_post_tickers (
                analysis_run_id, handle, status_id, ticker, source_ref, analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["analysis_run_id"],
                row["handle"],
                row["status_id"],
                ticker,
                row["source_ref"],
                row["analyzed_at"],
            ),
        )


async def _replace_llm_reddit_ticker_rows(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        DELETE FROM llm_reddit_post_tickers
        WHERE analysis_run_id = ? AND post_id = ?
        """,
        (row["analysis_run_id"], row["post_id"]),
    )
    for ticker in _normalized_tickers(row.get("tickers_json")):
        await db.execute(
            """
            INSERT INTO llm_reddit_post_tickers (
                analysis_run_id, subreddit, post_id, ticker, source_ref, analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["analysis_run_id"],
                row["subreddit"],
                row["post_id"],
                ticker,
                row["source_ref"],
                row["analyzed_at"],
            ),
        )
