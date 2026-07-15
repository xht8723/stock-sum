"""SQLite storage repository implementation."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import json
from typing import Any

import aiosqlite

from stock_sum.collectors.api.house import (
    normalize_house_date,
    normalize_house_name,
    normalize_house_transaction_action,
    parse_house_asset_metadata,
)
from stock_sum.collectors.api.sec_13f import normalize_sec_name, sec_filing_url
from stock_sum.core.models import ProviderApiResponse, RawItem, RawItemSaveResult
from stock_sum.storage.mappers import MappedRawItem, map_raw_item
from stock_sum.storage.models import (
    StoredAdanosResponseCacheEntry,
    StoredAdanosTrendingSector,
    StoredAdanosTrendingStock,
    StoredCollectionRun,
    StoredDownloadedMedia,
    StoredHousePtrFiling,
    StoredHousePtrTradeRow,
    StoredMediaAsset,
    StoredRedditComment,
    StoredRedditPost,
    StoredSec13FHolding,
    StoredSocialStatisticPoint,
    StoredStatisticFuzzyMatch,
    StoredTradingStatisticPoint,
    StoredXPost,
)
from stock_sum.storage.json_codec import (
    _date_param,
    _datetime_param,
    _json_list,
    _json_obj,
    _normalized_action_filter,
    _normalized_contains_filter,
    _normalized_importance,
    _normalized_tag_filter,
    _normalized_ticker,
    _normalized_tickers,
    _normalized_upper_filter,
    _optional_float,
    _optional_int,
    _optional_str,
    _tags_contain,
    _tickers_json,
    _utc_datetime,
)
from stock_sum.storage.analysis_writes import _replace_llm_reddit_ticker_rows, _replace_llm_x_ticker_rows
from stock_sum.storage.media_sqlite import _get_downloaded_media, _upsert_downloaded_media
from stock_sum.storage.migrations import apply_migrations
from stock_sum.storage.schema import SCHEMA_SQL


class SQLiteStorageRepository:
    """SQLite repository for collection runs and source-specific raw items."""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)

    async def initialize(self) -> None:
        """Create database tables if they do not exist."""

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute("PRAGMA busy_timeout = 30000")
            await db.executescript(SCHEMA_SQL)
            await apply_migrations(db)

    async def start_collection_run(
        self,
        *,
        run_id: str,
        collector_id: str,
        source_type: str | None = None,
    ) -> None:
        """Insert an in-progress collection run row."""

        await self.initialize()
        now = _utc_now()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO collection_runs (
                    run_id, collector_id, source_type, status, started_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, collector_id, source_type, "running", now),
            )
            await db.commit()

    async def finish_collection_run(
        self,
        *,
        run_id: str,
        status: str,
        collected_count: int = 0,
        inserted_count: int = 0,
        updated_count: int = 0,
        source_type: str | None = None,
        error_text: str | None = None,
    ) -> None:
        """Mark a collection run as complete or failed."""

        now = _utc_now()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                UPDATE collection_runs
                SET status = ?,
                    finished_at = ?,
                    collected_count = ?,
                    inserted_count = ?,
                    updated_count = ?,
                    source_type = COALESCE(?, source_type),
                    error_text = ?
                WHERE run_id = ?
                """,
                (status, now, collected_count, inserted_count, updated_count, source_type, error_text, run_id),
            )
            await db.commit()

    async def save_raw_items(self, items: list[RawItem]) -> RawItemSaveResult:
        """Persist raw items into source-specific tables and the shared index."""

        await self.initialize()
        if not items:
            return RawItemSaveResult(source_type="", collected_count=0, inserted_count=0, updated_count=0)

        inserted = 0
        updated = 0
        async with aiosqlite.connect(self.sqlite_path) as db:
            for item in items:
                mapped_item = map_raw_item(item)
                row_existed = await _mapped_row_exists(db, mapped_item)
                await _upsert_mapped_item(db, mapped_item)
                await _upsert_index_row(db, item)
                if row_existed:
                    updated += 1
                else:
                    inserted += 1
            await db.commit()

        source_types = {item.source_type for item in items}
        return RawItemSaveResult(
            source_type=items[0].source_type if len(source_types) == 1 else "mixed",
            collected_count=len(items),
            inserted_count=inserted,
            updated_count=updated,
        )

    async def save_provider_api_responses(
        self,
        *,
        collection_run_id: str,
        collector_id: str,
        responses: list[ProviderApiResponse],
    ) -> None:
        """Persist raw provider API/tool responses for one collection run."""

        await self.initialize()
        if not responses:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for response in responses:
                await db.execute(
                    """
                    INSERT INTO raw_provider_api_responses (
                        response_id, collection_run_id, collector_id, provider, tool_name,
                        request_arguments_json, raw_response_text, parsed_rows_json,
                        row_count, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        collection_run_id,
                        collector_id,
                        response.provider,
                        response.tool_name,
                        json.dumps(response.request_arguments, ensure_ascii=False, sort_keys=True, default=str),
                        response.raw_response_text,
                        json.dumps(response.parsed_rows, ensure_ascii=False, sort_keys=True, default=str),
                        response.row_count,
                        response.fetched_at.isoformat(),
                    ),
                )
            await db.commit()

    async def save_adanos_trendings(
        self,
        *,
        job_id: str,
        from_date: str,
        to_date: str,
        responses: list,
        cache_keys_by_endpoint: dict[tuple[str, str], str] | None = None,
    ) -> None:
        """Persist raw and normalized Adanos trendings responses."""

        await self.initialize()
        if not responses:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for response in responses:
                platform = str(getattr(response, "platform"))
                category = str(getattr(response, "category"))
                fetched_at = getattr(response, "fetched_at").isoformat()
                rows = list(getattr(response, "rows", []) or [])
                request_args_json = json.dumps(
                    getattr(response, "request_args"),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                raw_response_text = str(getattr(response, "raw_response_text"))
                status = str(getattr(response, "status"))
                await db.execute(
                    """
                    INSERT INTO raw_adanos_trending_responses (
                        response_id, job_id, platform, category, endpoint,
                        request_args_json, raw_response_text, error_text,
                        status, row_count, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        job_id,
                        platform,
                        category,
                        str(getattr(response, "endpoint")),
                        request_args_json,
                        raw_response_text,
                        getattr(response, "error", None),
                        status,
                        len(rows),
                        fetched_at,
                    ),
                )
                cache_key = (cache_keys_by_endpoint or {}).get((platform, category))
                if status == "succeeded" and cache_key:
                    await db.execute(
                        """
                        INSERT INTO adanos_response_cache_entries (
                            cache_key, source_job_id, platform, category, endpoint,
                            request_args_json, raw_response_text, row_count, fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            source_job_id = excluded.source_job_id,
                            platform = excluded.platform,
                            category = excluded.category,
                            endpoint = excluded.endpoint,
                            request_args_json = excluded.request_args_json,
                            raw_response_text = excluded.raw_response_text,
                            row_count = excluded.row_count,
                            fetched_at = excluded.fetched_at
                        WHERE excluded.fetched_at >= adanos_response_cache_entries.fetched_at
                        """,
                        (
                            cache_key,
                            job_id,
                            platform,
                            category,
                            str(getattr(response, "endpoint")),
                            request_args_json,
                            raw_response_text,
                            len(rows),
                            fetched_at,
                        ),
                    )
                if status != "succeeded":
                    continue
                if category == "stocks":
                    for fallback_rank, row in enumerate(rows, start=1):
                        rank = _optional_int(row.get("rank")) or fallback_rank
                        ticker = str(row.get("ticker") or "").upper().strip()
                        if not ticker:
                            continue
                        await db.execute(
                            """
                            INSERT OR REPLACE INTO raw_adanos_trending_stocks (
                                job_id, platform, rank, window_from, window_to,
                                ticker, company_name, trend, mentions, bullish_pct,
                                bearish_pct, sentiment_score, buzz_score,
                                trend_history_json, raw_json, fetched_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                job_id,
                                platform,
                                rank,
                                from_date,
                                to_date,
                                ticker,
                                _optional_str(row.get("company_name")),
                                _optional_str(row.get("trend")),
                                _optional_int(row.get("mentions")),
                                _optional_int(row.get("bullish_pct")),
                                _optional_int(row.get("bearish_pct")),
                                _optional_float(row.get("sentiment_score")),
                                _optional_float(row.get("buzz_score")),
                                json.dumps(row.get("trend_history") or [], ensure_ascii=False),
                                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
                                fetched_at,
                            ),
                        )
                elif category == "sectors":
                    for fallback_rank, row in enumerate(rows, start=1):
                        rank = _optional_int(row.get("rank")) or fallback_rank
                        sector = str(row.get("sector") or "").strip()
                        if not sector:
                            continue
                        top_tickers = row.get("top_tickers") if isinstance(row.get("top_tickers"), list) else []
                        await db.execute(
                            """
                            INSERT OR REPLACE INTO raw_adanos_trending_sectors (
                                job_id, platform, rank, window_from, window_to,
                                sector, top_tickers_json, trend, mentions,
                                bullish_pct, bearish_pct, sentiment_score,
                                buzz_score, trend_history_json, raw_json, fetched_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                job_id,
                                platform,
                                rank,
                                from_date,
                                to_date,
                                sector,
                                json.dumps([str(item) for item in top_tickers], ensure_ascii=False),
                                _optional_str(row.get("trend")),
                                _optional_int(row.get("mentions")),
                                _optional_int(row.get("bullish_pct")),
                                _optional_int(row.get("bearish_pct")),
                                _optional_float(row.get("sentiment_score")),
                                _optional_float(row.get("buzz_score")),
                                json.dumps(row.get("trend_history") or [], ensure_ascii=False),
                                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
                                fetched_at,
                            ),
                        )
            await db.commit()

    async def read_adanos_response_cache_entries(
        self,
        *,
        cache_keys: list[str],
        fresh_after: str,
    ) -> list[StoredAdanosResponseCacheEntry]:
        """Read fresh Adanos endpoint responses and prune expired entries."""

        await self.initialize()
        normalized_keys = sorted({str(key).strip() for key in cache_keys if str(key).strip()})
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                "DELETE FROM adanos_response_cache_entries WHERE fetched_at <= ?",
                (fresh_after,),
            )
            if not normalized_keys:
                await db.commit()
                return []
            placeholders = ", ".join("?" for _ in normalized_keys)
            rows = await db.execute_fetchall(
                f"""
                SELECT cache_key, source_job_id, platform, category, endpoint,
                       request_args_json, raw_response_text, row_count, fetched_at
                FROM adanos_response_cache_entries
                WHERE cache_key IN ({placeholders})
                  AND fetched_at > ?
                ORDER BY platform ASC, category ASC
                """,
                [*normalized_keys, fresh_after],
            )
            await db.commit()
        return [
            StoredAdanosResponseCacheEntry(
                cache_key=row[0],
                source_job_id=row[1],
                platform=row[2],
                category=row[3],
                endpoint=row[4],
                request_args=_json_obj(row[5]),
                raw_response_text=row[6],
                row_count=row[7],
                fetched_at=row[8],
            )
            for row in rows
        ]

    async def delete_adanos_response_cache_entries(self, *, cache_keys: list[str]) -> None:
        """Delete unusable Adanos endpoint cache entries."""

        await self.initialize()
        normalized_keys = sorted({str(key).strip() for key in cache_keys if str(key).strip()})
        if not normalized_keys:
            return
        placeholders = ", ".join("?" for _ in normalized_keys)
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                f"DELETE FROM adanos_response_cache_entries WHERE cache_key IN ({placeholders})",
                normalized_keys,
            )
            await db.commit()

    async def read_adanos_trending_stocks(
        self,
        *,
        job_id: str,
        limit: int | None = None,
    ) -> list[StoredAdanosTrendingStock]:
        """Read stored Adanos trending stock rows for one job."""

        await self.initialize()
        sql = """
            SELECT job_id, platform, rank, window_from, window_to, ticker,
                   company_name, trend, mentions, bullish_pct, bearish_pct,
                   sentiment_score, buzz_score, trend_history_json, raw_json, fetched_at
            FROM raw_adanos_trending_stocks
            WHERE job_id = ?
            ORDER BY platform ASC, rank ASC
        """
        params: list[Any] = [job_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            rows = await db.execute_fetchall(sql, params)
        return [
            StoredAdanosTrendingStock(
                job_id=row[0],
                platform=row[1],
                rank=row[2],
                window_from=row[3],
                window_to=row[4],
                ticker=row[5],
                company_name=row[6],
                trend=row[7],
                mentions=row[8],
                bullish_pct=row[9],
                bearish_pct=row[10],
                sentiment_score=row[11],
                buzz_score=row[12],
                trend_history=_json_list(row[13]),
                raw_metadata=_json_obj(row[14]),
                fetched_at=row[15],
            )
            for row in rows
        ]

    async def read_adanos_trending_stocks_for_sources(
        self,
        *,
        sources: list[tuple[str, str]],
    ) -> list[StoredAdanosTrendingStock]:
        """Read stock rows from exact platform/source-job snapshots."""

        await self.initialize()
        normalized_sources = _normalized_adanos_sources(sources)
        if not normalized_sources:
            return []
        predicate, params = _adanos_sources_predicate(normalized_sources)
        sql = f"""
            SELECT job_id, platform, rank, window_from, window_to, ticker,
                   company_name, trend, mentions, bullish_pct, bearish_pct,
                   sentiment_score, buzz_score, trend_history_json, raw_json, fetched_at
            FROM raw_adanos_trending_stocks
            WHERE {predicate}
            ORDER BY platform ASC, rank ASC
        """
        async with aiosqlite.connect(self.sqlite_path) as db:
            rows = await db.execute_fetchall(sql, params)
        return _stored_adanos_stock_rows(rows)

    async def read_latest_prior_adanos_trending_stocks(
        self,
        *,
        exclude_job_id: str | None,
        tickers: list[str],
        since_fetched_at: str,
        exclude_sources: list[tuple[str, str]] | None = None,
    ) -> list[StoredAdanosTrendingStock]:
        """Read latest historical Adanos stock rows for each platform/ticker."""

        await self.initialize()
        normalized_tickers = sorted({ticker.upper().strip() for ticker in tickers if ticker and ticker.strip()})
        if not normalized_tickers:
            return []
        placeholders = ", ".join("?" for _ in normalized_tickers)
        cte_exclusions, cte_exclusion_params = _adanos_history_exclusions(
            exclude_job_id=exclude_job_id,
            exclude_sources=exclude_sources,
        )
        outer_exclusions, outer_exclusion_params = _adanos_history_exclusions(
            alias="s",
            exclude_job_id=exclude_job_id,
            exclude_sources=exclude_sources,
        )
        cte_conditions = [*cte_exclusions, "fetched_at >= ?", f"ticker IN ({placeholders})"]
        outer_conditions = outer_exclusions or ["1 = 1"]
        sql = f"""
            WITH latest AS (
                SELECT platform, ticker, MAX(fetched_at) AS latest_fetched_at
                FROM raw_adanos_trending_stocks
                WHERE {' AND '.join(cte_conditions)}
                GROUP BY platform, ticker
            )
            SELECT s.job_id, s.platform, s.rank, s.window_from, s.window_to, s.ticker,
                   s.company_name, s.trend, s.mentions, s.bullish_pct, s.bearish_pct,
                   s.sentiment_score, s.buzz_score, s.trend_history_json, s.raw_json, s.fetched_at
            FROM raw_adanos_trending_stocks s
            JOIN latest l
             ON s.platform = l.platform
             AND s.ticker = l.ticker
             AND s.fetched_at = l.latest_fetched_at
            WHERE {' AND '.join(outer_conditions)}
            ORDER BY s.platform ASC, s.ticker ASC, s.fetched_at DESC
        """
        params: list[Any] = [
            *cte_exclusion_params,
            since_fetched_at,
            *normalized_tickers,
            *outer_exclusion_params,
        ]
        async with aiosqlite.connect(self.sqlite_path) as db:
            rows = await db.execute_fetchall(sql, params)
        return _stored_adanos_stock_rows(rows)

    async def has_prior_adanos_trending_stock_history(
        self,
        *,
        exclude_job_id: str | None,
        since_fetched_at: str,
        exclude_sources: list[tuple[str, str]] | None = None,
    ) -> bool:
        """Return whether any prior Adanos stock history exists in the comparison window."""

        await self.initialize()
        exclusions, exclusion_params = _adanos_history_exclusions(
            exclude_job_id=exclude_job_id,
            exclude_sources=exclude_sources,
        )
        conditions = [*exclusions, "fetched_at >= ?"]
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(
                f"""
                SELECT 1
                FROM raw_adanos_trending_stocks
                WHERE {' AND '.join(conditions)}
                LIMIT 1
                """,
                [*exclusion_params, since_fetched_at],
            )
            row = await cursor.fetchone()
        return row is not None

    async def read_adanos_trending_sectors(
        self,
        *,
        job_id: str,
        limit: int | None = None,
    ) -> list[StoredAdanosTrendingSector]:
        """Read stored Adanos trending sector rows for one job."""

        await self.initialize()
        sql = """
            SELECT job_id, platform, rank, window_from, window_to, sector,
                   top_tickers_json, trend, mentions, bullish_pct, bearish_pct,
                   sentiment_score, buzz_score, trend_history_json, raw_json, fetched_at
            FROM raw_adanos_trending_sectors
            WHERE job_id = ?
            ORDER BY platform ASC, rank ASC
        """
        params: list[Any] = [job_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            rows = await db.execute_fetchall(sql, params)
        return [
            StoredAdanosTrendingSector(
                job_id=row[0],
                platform=row[1],
                rank=row[2],
                window_from=row[3],
                window_to=row[4],
                sector=row[5],
                top_tickers=[str(item) for item in _json_list(row[6])],
                trend=row[7],
                mentions=row[8],
                bullish_pct=row[9],
                bearish_pct=row[10],
                sentiment_score=row[11],
                buzz_score=row[12],
                trend_history=_json_list(row[13]),
                raw_metadata=_json_obj(row[14]),
                fetched_at=row[15],
            )
            for row in rows
        ]

    async def read_adanos_trending_sectors_for_sources(
        self,
        *,
        sources: list[tuple[str, str]],
    ) -> list[StoredAdanosTrendingSector]:
        """Read sector rows from exact platform/source-job snapshots."""

        await self.initialize()
        normalized_sources = _normalized_adanos_sources(sources)
        if not normalized_sources:
            return []
        predicate, params = _adanos_sources_predicate(normalized_sources)
        sql = f"""
            SELECT job_id, platform, rank, window_from, window_to, sector,
                   top_tickers_json, trend, mentions, bullish_pct, bearish_pct,
                   sentiment_score, buzz_score, trend_history_json, raw_json, fetched_at
            FROM raw_adanos_trending_sectors
            WHERE {predicate}
            ORDER BY platform ASC, rank ASC
        """
        async with aiosqlite.connect(self.sqlite_path) as db:
            rows = await db.execute_fetchall(sql, params)
        return _stored_adanos_sector_rows(rows)

    async def start_llm_analysis_run(
        self,
        *,
        analysis_run_id: str,
        provider: str,
        model: str,
        prompt_version: str,
        instructions: str | None = None,
    ) -> None:
        """Insert an in-progress LLM analysis run."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO llm_analysis_runs (
                    analysis_run_id, provider, model, prompt_version,
                    status, started_at, instructions
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (analysis_run_id, provider, model, prompt_version, "running", _utc_now(), instructions),
            )
            await db.commit()

    async def finish_llm_analysis_run(
        self,
        *,
        analysis_run_id: str,
        status: str,
        chunk_count: int = 0,
        succeeded_count: int = 0,
        failed_count: int = 0,
        error_text: str | None = None,
    ) -> None:
        """Mark a chunked LLM analysis run complete or failed."""

        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                UPDATE llm_analysis_runs
                SET status = ?,
                    finished_at = ?,
                    chunk_count = ?,
                    succeeded_count = ?,
                    failed_count = ?,
                    error_text = ?
                WHERE analysis_run_id = ?
                """,
                (status, _utc_now(), chunk_count, succeeded_count, failed_count, error_text, analysis_run_id),
            )
            await db.commit()

    async def save_llm_x_post_analyses(self, rows: list[dict]) -> None:
        """Persist X post analysis rows."""

        await self.initialize()
        if not rows:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for row in rows:
                row = {**row, "tickers_json": _tickers_json(row.get("tickers_json"))}
                await db.execute(
                    """
                    INSERT INTO llm_x_post_analyses (
                        analysis_run_id, handle, status_id, source_ref, url,
                        posted_at_text, sentiment, tags_json, tickers_json, summary, interpretation,
                        importance, confidence, raw_response_json, analyzed_at
                    ) VALUES (
                        :analysis_run_id, :handle, :status_id, :source_ref, :url,
                        :posted_at_text, :sentiment, :tags_json, :tickers_json, :summary, :interpretation,
                        :importance, :confidence, :raw_response_json, :analyzed_at
                    )
                    ON CONFLICT (analysis_run_id, status_id) DO UPDATE SET
                        source_ref = excluded.source_ref,
                        url = excluded.url,
                        posted_at_text = excluded.posted_at_text,
                        sentiment = excluded.sentiment,
                        tags_json = excluded.tags_json,
                        tickers_json = excluded.tickers_json,
                        summary = excluded.summary,
                        interpretation = excluded.interpretation,
                        importance = excluded.importance,
                        confidence = excluded.confidence,
                        raw_response_json = excluded.raw_response_json,
                        analyzed_at = excluded.analyzed_at
                    """,
                    row,
                )
                await _replace_llm_x_ticker_rows(db, row)
            await db.commit()

    async def save_llm_reddit_post_analyses(self, rows: list[dict]) -> None:
        """Persist Reddit post analysis rows."""

        await self.initialize()
        if not rows:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for row in rows:
                row = {**row, "tickers_json": _tickers_json(row.get("tickers_json"))}
                await db.execute(
                    """
                    INSERT INTO llm_reddit_post_analyses (
                        analysis_run_id, subreddit, post_id, source_ref, title,
                        url, created_at_text, sentiment, tags_json, tickers_json, summary, interpretation,
                        importance, confidence, comment_sentiment_counts_json, raw_response_json, analyzed_at
                    ) VALUES (
                        :analysis_run_id, :subreddit, :post_id, :source_ref, :title,
                        :url, :created_at_text, :sentiment, :tags_json, :tickers_json, :summary, :interpretation,
                        :importance, :confidence, :comment_sentiment_counts_json, :raw_response_json, :analyzed_at
                    )
                    ON CONFLICT (analysis_run_id, post_id) DO UPDATE SET
                        source_ref = excluded.source_ref,
                        title = excluded.title,
                        url = excluded.url,
                        created_at_text = excluded.created_at_text,
                        sentiment = excluded.sentiment,
                        tags_json = excluded.tags_json,
                        tickers_json = excluded.tickers_json,
                        summary = excluded.summary,
                        interpretation = excluded.interpretation,
                        importance = excluded.importance,
                        confidence = excluded.confidence,
                        comment_sentiment_counts_json = excluded.comment_sentiment_counts_json,
                        raw_response_json = excluded.raw_response_json,
                        analyzed_at = excluded.analyzed_at
                    """,
                    row,
                )
                await _replace_llm_reddit_ticker_rows(db, row)
            await db.commit()

    async def save_llm_reddit_comment_analyses(self, rows: list[dict]) -> None:
        """Persist Reddit comment analysis rows."""

        await self.initialize()
        if not rows:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO llm_reddit_comment_analyses (
                        analysis_run_id, subreddit, post_id, comment_id, source_ref,
                        parent_id, sentiment, summary, confidence, raw_response_json, analyzed_at
                    ) VALUES (
                        :analysis_run_id, :subreddit, :post_id, :comment_id, :source_ref,
                        :parent_id, :sentiment, :summary, :confidence, :raw_response_json, :analyzed_at
                    )
                    ON CONFLICT (analysis_run_id, post_id, comment_id) DO UPDATE SET
                        source_ref = excluded.source_ref,
                        parent_id = excluded.parent_id,
                        sentiment = excluded.sentiment,
                        summary = excluded.summary,
                        confidence = excluded.confidence,
                        raw_response_json = excluded.raw_response_json,
                        analyzed_at = excluded.analyzed_at
                    """,
                    row,
                )
            await db.commit()

    async def read_llm_analysis_report(self, *, analysis_run_id: str | None = None) -> dict:
        """Read stored analysis rows as renderer-ready summary data."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            run_id = analysis_run_id or await _latest_analysis_run_id(db)
            if run_id is None:
                return {"x_reports": [], "reddit_report": {"overall_summary": [], "posts": []}}
            x_reports = await _read_analysis_x_reports(db, run_id)
            reddit_report = await _read_analysis_reddit_report(db, run_id)
        return {"x_reports": x_reports, "reddit_report": reddit_report}

    async def read_llm_social_posts_by_ticker(
        self,
        *,
        ticker: str,
        analysis_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read analyzed social posts linked to a normalized ticker."""

        await self.initialize()
        normalized_ticker = _normalized_ticker(ticker)
        if not normalized_ticker:
            return []
        async with aiosqlite.connect(self.sqlite_path) as db:
            run_id = analysis_run_id or await _latest_analysis_run_id(db)
            if run_id is None:
                return []
            x_rows = await _read_x_posts_by_ticker(db, run_id, normalized_ticker)
            reddit_rows = await _read_reddit_posts_by_ticker(db, run_id, normalized_ticker)
        return [*x_rows, *reddit_rows]

    async def read_social_statistic_points(
        self,
        *,
        ticker: str | None = None,
        fuzzy_tag: str | None = None,
        source: str | None = None,
        sentiment: str | None = None,
        posted_start: datetime | None = None,
        posted_end: datetime | None = None,
        analysis_run_id: str | None = None,
    ) -> list[StoredSocialStatisticPoint]:
        """Read analyzed social rows for statistic charting."""

        await self.initialize()
        normalized_ticker = _normalized_ticker(ticker) if ticker else None
        normalized_fuzzy_tag = _normalized_tag_filter(fuzzy_tag)
        normalized_source = (source or "all").strip().lower()
        normalized_sentiment = (sentiment or "").strip().lower() or None
        if normalized_source not in {"all", "x", "reddit"}:
            return []
        async with aiosqlite.connect(self.sqlite_path) as db:
            run_id = analysis_run_id or await _latest_analysis_run_id(db)
            if run_id is None:
                return []
            points: list[StoredSocialStatisticPoint] = []
            if normalized_source in {"all", "x"}:
                points.extend(
                    await _read_x_statistic_points(
                        db,
                        analysis_run_id=run_id,
                        ticker=normalized_ticker,
                        fuzzy_tag=normalized_fuzzy_tag,
                        sentiment=normalized_sentiment,
                        posted_start=posted_start,
                        posted_end=posted_end,
                    )
                )
            if normalized_source in {"all", "reddit"}:
                points.extend(
                    await _read_reddit_statistic_points(
                        db,
                        analysis_run_id=run_id,
                        ticker=normalized_ticker,
                        fuzzy_tag=normalized_fuzzy_tag,
                        sentiment=normalized_sentiment,
                        posted_start=posted_start,
                        posted_end=posted_end,
                    )
                )
        points.sort(key=lambda item: item.posted_at or "", reverse=True)
        return points

    async def search_social_statistic_tags(
        self,
        *,
        query: str,
        limit: int = 5,
    ) -> list[StoredStatisticFuzzyMatch]:
        """Return tag candidates from the latest social analysis run."""

        await self.initialize()
        query_text = _normalized_tag_filter(query)
        if not query_text:
            return []
        async with aiosqlite.connect(self.sqlite_path) as db:
            run_id = await _latest_analysis_run_id(db)
            if run_id is None:
                return []
            cursor = await db.execute(
                """
                SELECT 'x' AS source, tags_json
                FROM llm_x_post_analyses
                WHERE analysis_run_id = ?
                UNION ALL
                SELECT 'reddit' AS source, tags_json
                FROM llm_reddit_post_analyses
                WHERE analysis_run_id = ?
                """,
                (run_id, run_id),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        counts: dict[str, dict[str, Any]] = {}
        for source, tags_json in rows:
            seen_in_row: set[str] = set()
            for tag_value in _json_list(tags_json):
                tag = _normalized_tag_filter(str(tag_value))
                if not tag or query_text not in tag or tag in seen_in_row:
                    continue
                seen_in_row.add(tag)
                item = counts.setdefault(tag, {"row_count": 0, "x_count": 0, "reddit_count": 0})
                item["row_count"] += 1
                if source == "x":
                    item["x_count"] += 1
                elif source == "reddit":
                    item["reddit_count"] += 1

        sorted_tags = sorted(
            counts.items(),
            key=lambda item: (
                0 if item[0] == query_text else 1 if item[0].startswith(query_text) else 2,
                -int(item[1]["row_count"]),
                item[0],
            ),
        )[: max(1, limit)]
        return [
            StoredStatisticFuzzyMatch(
                mode="social",
                label=tag,
                source="social_tags",
                match_value=tag,
                row_count=int(data["row_count"]),
                x_count=int(data["x_count"]),
                reddit_count=int(data["reddit_count"]),
                statistic_filters={"fuzzy_tag": tag},
            )
            for tag, data in sorted_tags
        ]

    async def list_collection_runs(
        self,
        *,
        limit: int | None = None,
    ) -> list[StoredCollectionRun]:
        """Return stored collection runs."""

        await self.initialize()
        query = """
            SELECT run_id, collector_id, source_type, status, started_at, finished_at,
                   collected_count, inserted_count, updated_count, error_text
            FROM collection_runs
        """
        params: list[Any] = []
        query += " ORDER BY started_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        return [
            StoredCollectionRun(
                run_id=row[0],
                collector_id=row[1],
                source_type=row[2],
                status=row[3],
                started_at=row[4],
                finished_at=row[5],
                collected_count=row[6],
                inserted_count=row[7],
                updated_count=row[8],
                error_text=row[9],
            )
            for row in rows
        ]

    async def read_x_posts(
        self,
        *,
        handles: list[str] | None = None,
        since_posted_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredXPost]:
        """Read stored X posts with media."""

        await self.initialize()
        query = """
            SELECT status_id, handle, author_handle, author_name, posted_at_text, url, text,
                   reply_count, repost_count, like_count, quote_count, view_count,
                   raw_json, collected_at
            FROM raw_x_posts
        """
        params: list[Any] = []
        conditions: list[str] = []
        if handles:
            placeholders = ",".join("?" for _ in handles)
            conditions.append(f"handle IN ({placeholders})")
            params.extend(handles)
        if since_posted_at is not None:
            conditions.append("posted_at_utc >= ?")
            params.append(_utc_datetime(since_posted_at).isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY posted_at_utc DESC, CAST(status_id AS INTEGER) DESC, collected_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

            posts: list[StoredXPost] = []
            for row in rows:
                posts.append(
                    StoredXPost(
                        status_id=row[0],
                        handle=row[1],
                        author_handle=row[2],
                        author_name=row[3],
                        posted_at_text=row[4],
                        url=row[5],
                        text=row[6],
                        reply_count=row[7],
                        repost_count=row[8],
                        like_count=row[9],
                        quote_count=row[10],
                        view_count=row[11],
                        raw_metadata=_json_obj(row[12]),
                        collected_at=row[13],
                        media=await _read_x_media(db, row[0]),
                    )
                )
        return posts

    async def read_reddit_posts(
        self,
        *,
        subreddits: list[str] | None = None,
        since_posted_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[StoredRedditPost]:
        """Read stored Reddit posts with media and comments."""

        await self.initialize()
        query = """
            SELECT post_id, subreddit, fullname, title, author, url, permalink, selftext,
                   score, ups, upvote_ratio, num_comments, thumbnail_url, created_at_text,
                   raw_json, collected_at
            FROM raw_reddit_posts
        """
        params: list[Any] = []
        conditions: list[str] = []
        if subreddits:
            placeholders = ",".join("?" for _ in subreddits)
            conditions.append(f"subreddit IN ({placeholders})")
            params.extend(subreddits)
        if since_posted_at is not None:
            conditions.append("created_at_utc >= ?")
            params.append(_utc_datetime(since_posted_at).isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at_utc DESC, collected_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

            posts: list[StoredRedditPost] = []
            for row in rows:
                post_id = row[0]
                posts.append(
                    StoredRedditPost(
                        post_id=post_id,
                        subreddit=row[1],
                        fullname=row[2],
                        title=row[3],
                        author=row[4],
                        url=row[5],
                        permalink=row[6],
                        selftext=row[7],
                        score=row[8],
                        ups=row[9],
                        upvote_ratio=row[10],
                        num_comments=row[11],
                        thumbnail_url=row[12],
                        created_at_text=row[13],
                        raw_metadata=_json_obj(row[14]),
                        collected_at=row[15],
                        media=await _read_reddit_media(db, post_id),
                        comments=await _read_reddit_comments(db, post_id),
                    )
                )
        return posts

    async def existing_house_ptr_doc_ids(self, *, year: int | None = None) -> set[str]:
        """Return terminally processed House PTR DocIDs safe to skip."""

        await self.initialize()
        query = "SELECT doc_id FROM raw_house_ptr_filings WHERE extraction_status IN (?, ?, ?)"
        params: list[Any] = ["succeeded", "photo_scanned", "unparsed"]
        if year is not None:
            query += " AND year = ?"
            params.append(year)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return {str(row[0]) for row in rows}

    async def read_house_ptr_filings(
        self,
        *,
        name_contains: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        filing_start: datetime | None = None,
        filing_end: datetime | None = None,
        collected_start: datetime | None = None,
        collected_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        order_by_filing_date: bool = False,
        order_by_collected_at: bool = False,
    ) -> list[StoredHousePtrFiling]:
        """Read filing metadata independently of extracted transaction rows."""

        await self.initialize()
        query = """
            SELECT f.doc_id, f.year, COALESCE(f.display_name, f.name), f.status, f.state,
                   f.filing_date, f.filing_date_utc, f.pdf_url, f.extraction_status,
                   f.extraction_error, f.extraction_warnings_json, f.extraction_metadata_json,
                   (SELECT COUNT(*) FROM raw_house_ptr_trade_rows count_rows WHERE count_rows.doc_id = f.doc_id),
                   f.collected_at
            FROM raw_house_ptr_filings f
        """
        conditions: list[str] = []
        params: list[Any] = []
        normalized_name = normalize_house_name(name_contains)
        if normalized_name:
            conditions.append("f.name_normalized LIKE ?")
            params.append(f"%{normalized_name}%")
        if filing_start is not None:
            conditions.append("f.filing_date_utc >= ?")
            params.append(_datetime_param(filing_start))
        if filing_end is not None:
            conditions.append("f.filing_date_utc <= ?")
            params.append(_datetime_param(filing_end, end_of_day=True))
        if collected_start is not None:
            conditions.append("f.collected_at >= ?")
            params.append(_datetime_param(collected_start))
        if collected_end is not None:
            conditions.append("f.collected_at <= ?")
            params.append(_datetime_param(collected_end))

        trade_conditions: list[str] = []
        if transaction_start is not None:
            trade_conditions.append("trade.transaction_date_utc >= ?")
            params.append(_datetime_param(transaction_start))
        if transaction_end is not None:
            trade_conditions.append("trade.transaction_date_utc <= ?")
            params.append(_datetime_param(transaction_end, end_of_day=True))
        normalized_asset_type = _normalized_upper_filter(asset_type)
        if normalized_asset_type:
            trade_conditions.append("UPPER(trade.asset_type_code) = ?")
            params.append(normalized_asset_type)
        normalized_ticker = _normalized_upper_filter(ticker)
        if normalized_ticker:
            trade_conditions.append("UPPER(trade.stock_ticker) = ?")
            params.append(normalized_ticker)
        if trade_conditions:
            conditions.append(
                "EXISTS (SELECT 1 FROM raw_house_ptr_trade_rows trade WHERE trade.doc_id = f.doc_id AND "
                + " AND ".join(trade_conditions)
                + ")"
            )
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        if order_by_collected_at:
            query += " ORDER BY f.collected_at DESC, f.doc_id DESC"
        elif order_by_filing_date:
            query += " ORDER BY COALESCE(f.filing_date_utc, f.collected_at) DESC, f.collected_at DESC, f.doc_id DESC"
        else:
            query += " ORDER BY f.collected_at DESC, f.doc_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [
            StoredHousePtrFiling(
                doc_id=row[0],
                year=row[1],
                name=row[2],
                status=row[3],
                state=row[4],
                filing_date=row[5],
                filing_date_utc=row[6],
                pdf_url=row[7],
                extraction_status=row[8],
                extraction_error=row[9],
                extraction_warnings=[item for item in _json_list(row[10]) if isinstance(item, dict)],
                extraction_metadata=_json_obj(row[11]),
                transaction_count=int(row[12] or 0),
                collected_at=row[13],
            )
            for row in rows
        ]

    async def read_house_ptr_trades(
        self,
        *,
        name_contains: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        filing_start: datetime | None = None,
        filing_end: datetime | None = None,
        collected_start: datetime | None = None,
        collected_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        order_by_filing_date: bool = False,
    ) -> list[StoredHousePtrTradeRow]:
        """Read House PTR trade rows joined with filing metadata."""

        await self.initialize()
        query = """
            SELECT f.doc_id, f.year, COALESCE(f.display_name, f.name), f.status, f.state,
                   f.filing_date, f.filing_date_utc, f.pdf_url,
                   r.table_index, r.row_index, r.asset, r.asset_type_code, r.asset_type_label,
                   r.stock_ticker, r.transaction_type, r.transaction_date,
                   r.transaction_date_utc, r.transaction_action, r.amount, r.raw_cells_json,
                   r.raw_json, f.collected_at
            FROM raw_house_ptr_trade_rows r
            JOIN raw_house_ptr_filings f ON f.doc_id = r.doc_id
        """
        conditions: list[str] = []
        params: list[Any] = []
        normalized_name = normalize_house_name(name_contains)
        if normalized_name:
            conditions.append("f.name_normalized LIKE ?")
            params.append(f"%{normalized_name}%")
        if transaction_start is not None:
            conditions.append("r.transaction_date_utc >= ?")
            params.append(_datetime_param(transaction_start))
        if transaction_end is not None:
            conditions.append("r.transaction_date_utc <= ?")
            params.append(_datetime_param(transaction_end, end_of_day=True))
        if filing_start is not None:
            conditions.append("f.filing_date_utc >= ?")
            params.append(_datetime_param(filing_start))
        if filing_end is not None:
            conditions.append("f.filing_date_utc <= ?")
            params.append(_datetime_param(filing_end, end_of_day=True))
        if collected_start is not None:
            conditions.append("f.collected_at >= ?")
            params.append(_datetime_param(collected_start))
        if collected_end is not None:
            conditions.append("f.collected_at <= ?")
            params.append(_datetime_param(collected_end))
        normalized_asset_type = _normalized_upper_filter(asset_type)
        if normalized_asset_type:
            conditions.append("UPPER(r.asset_type_code) = ?")
            params.append(normalized_asset_type)
        normalized_ticker = _normalized_upper_filter(ticker)
        if normalized_ticker:
            conditions.append("UPPER(r.stock_ticker) = ?")
            params.append(normalized_ticker)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        if order_by_filing_date:
            query += """
                ORDER BY COALESCE(f.filing_date_utc, f.collected_at) DESC,
                         COALESCE(r.transaction_date_utc, f.filing_date_utc, f.collected_at) DESC,
                         f.collected_at DESC,
                         f.doc_id DESC, r.table_index ASC, r.row_index ASC
            """
        else:
            query += """
                ORDER BY COALESCE(r.transaction_date_utc, f.filing_date_utc, f.collected_at) DESC,
                         f.collected_at DESC,
                         f.doc_id DESC, r.table_index ASC, r.row_index ASC
            """
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [
            StoredHousePtrTradeRow(
                doc_id=row[0],
                year=row[1],
                name=row[2],
                status=row[3],
                state=row[4],
                filing_date=row[5],
                filing_date_utc=row[6],
                pdf_url=row[7],
                table_index=row[8],
                row_index=row[9],
                asset=row[10],
                asset_type_code=row[11],
                asset_type_label=row[12],
                stock_ticker=row[13],
                transaction_type=row[14],
                transaction_date=row[15],
                transaction_date_utc=row[16],
                transaction_action=row[17],
                amount=row[18],
                raw_cells=_json_list(row[19]),
                raw_metadata=_json_obj(row[20]),
                collected_at=row[21],
            )
            for row in rows
        ]

    async def read_trading_statistic_points(
        self,
        *,
        name_contains: str | None = None,
        asset_name: str | None = None,
        transaction_start: datetime | None = None,
        transaction_end: datetime | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        action: str | None = None,
    ) -> list[StoredTradingStatisticPoint]:
        """Read House PTR trade rows for statistic charting."""

        await self.initialize()
        query = """
            SELECT f.doc_id, COALESCE(f.display_name, f.name), f.state,
                   r.asset, r.asset_type_code, r.stock_ticker, r.transaction_action,
                   r.transaction_date, r.transaction_date_utc, r.amount
            FROM raw_house_ptr_trade_rows r
            JOIN raw_house_ptr_filings f ON f.doc_id = r.doc_id
        """
        conditions: list[str] = []
        params: list[Any] = []
        normalized_name = normalize_house_name(name_contains)
        if normalized_name:
            conditions.append("f.name_normalized LIKE ?")
            params.append(f"%{normalized_name}%")
        normalized_asset_name = _normalized_contains_filter(asset_name)
        if normalized_asset_name:
            conditions.append("LOWER(COALESCE(r.asset, '')) LIKE ?")
            params.append(f"%{normalized_asset_name}%")
        if transaction_start is not None:
            conditions.append("r.transaction_date_utc >= ?")
            params.append(_datetime_param(transaction_start))
        if transaction_end is not None:
            conditions.append("r.transaction_date_utc <= ?")
            params.append(_datetime_param(transaction_end, end_of_day=True))
        normalized_asset_type = _normalized_upper_filter(asset_type)
        if normalized_asset_type:
            conditions.append("UPPER(r.asset_type_code) = ?")
            params.append(normalized_asset_type)
        normalized_ticker = _normalized_upper_filter(ticker)
        if normalized_ticker:
            conditions.append("UPPER(r.stock_ticker) = ?")
            params.append(normalized_ticker)
        normalized_action = _normalized_action_filter(action)
        if normalized_action:
            conditions.append("r.transaction_action = ?")
            params.append(normalized_action)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += """
            ORDER BY COALESCE(r.transaction_date_utc, f.filing_date_utc, f.collected_at) DESC,
                     f.doc_id DESC, r.table_index ASC, r.row_index ASC
        """
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [
            StoredTradingStatisticPoint(
                doc_id=row[0],
                name=row[1],
                state=row[2],
                asset=row[3],
                asset_type_code=row[4],
                stock_ticker=row[5],
                transaction_action=row[6],
                transaction_date=row[7],
                transaction_date_utc=row[8],
                amount=row[9],
            )
            for row in rows
        ]

    async def search_trading_statistic_assets(
        self,
        *,
        query: str,
        limit: int = 5,
    ) -> list[StoredStatisticFuzzyMatch]:
        """Return House PTR asset candidates for statistic fuzzy search."""

        await self.initialize()
        normalized_query = _normalized_contains_filter(query)
        if not normalized_query:
            return []
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(
                """
                SELECT r.asset, r.asset_type_code, r.stock_ticker, COUNT(*) AS row_count
                FROM raw_house_ptr_trade_rows r
                WHERE LOWER(COALESCE(r.asset, '')) LIKE ?
                GROUP BY r.asset, r.asset_type_code, r.stock_ticker
                ORDER BY
                    CASE
                        WHEN LOWER(COALESCE(r.asset, '')) = ? THEN 0
                        WHEN LOWER(COALESCE(r.asset, '')) LIKE ? THEN 1
                        ELSE 2
                    END,
                    row_count DESC,
                    r.asset ASC
                LIMIT ?
                """,
                (f"%{normalized_query}%", normalized_query, f"{normalized_query}%", max(1, limit)),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        matches: list[StoredStatisticFuzzyMatch] = []
        for asset, asset_type_code, stock_ticker, row_count in rows:
            filters: dict[str, Any] = {"asset_name": asset}
            if asset_type_code:
                filters["asset_type"] = asset_type_code
            if stock_ticker:
                filters["ticker"] = stock_ticker
            matches.append(
                StoredStatisticFuzzyMatch(
                    mode="trading",
                    label=str(asset or "Unknown asset"),
                    source="house_ptr_assets",
                    match_value=str(asset or ""),
                    row_count=int(row_count or 0),
                    ticker=stock_ticker,
                    asset_name=asset,
                    asset_type_code=asset_type_code,
                    statistic_filters=filters,
                )
            )
        return matches

    async def read_sec_13f_holdings(
        self,
        *,
        manager: str | None = None,
        cik: str | None = None,
        accession_number: str | None = None,
        issuer: str | None = None,
        cusip: str | None = None,
        figi: str | None = None,
        put_call: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        filing_start: datetime | None = None,
        filing_end: datetime | None = None,
        min_value: int | None = None,
        min_shares: int | None = None,
        limit: int | None = 20,
    ) -> list[StoredSec13FHolding]:
        """Read SEC 13F holdings joined with submission and cover page metadata."""

        await self.initialize()
        query = """
            SELECT i.dataset_id, d.label, i.accession_number, s.cik, c.manager_name,
                   s.filing_date, s.filing_date_utc, s.period_of_report, s.period_of_report_utc,
                   i.info_table_sk, i.issuer, i.title_of_class, i.cusip, i.figi, i.value,
                   i.ssh_prn_amt, i.ssh_prn_type, i.put_call, i.investment_discretion,
                   i.other_manager, i.voting_auth_sole, i.voting_auth_shared, i.voting_auth_none,
                   i.raw_json
            FROM raw_sec_13f_info_tables i
            JOIN raw_sec_13f_datasets d ON d.dataset_id = i.dataset_id
            LEFT JOIN raw_sec_13f_submissions s
                ON s.dataset_id = i.dataset_id AND s.accession_number = i.accession_number
            LEFT JOIN raw_sec_13f_coverpages c
                ON c.dataset_id = i.dataset_id AND c.accession_number = i.accession_number
        """
        conditions: list[str] = []
        params: list[Any] = []
        manager_filter = normalize_sec_name(manager)
        if manager_filter:
            conditions.append("c.manager_name_normalized LIKE ?")
            params.append(f"%{manager_filter}%")
        cik_filter = str(cik or "").strip().lstrip("0")
        if cik_filter:
            conditions.append("LTRIM(s.cik, '0') = ?")
            params.append(cik_filter)
        accession_filter = str(accession_number or "").strip()
        if accession_filter:
            conditions.append("i.accession_number = ?")
            params.append(accession_filter)
        issuer_filter = normalize_sec_name(issuer)
        if issuer_filter:
            conditions.append("i.issuer_normalized LIKE ?")
            params.append(f"%{issuer_filter}%")
        normalized_cusip = _normalized_upper_filter(cusip)
        if normalized_cusip:
            conditions.append("UPPER(i.cusip) = ?")
            params.append(normalized_cusip)
        normalized_figi = _normalized_upper_filter(figi)
        if normalized_figi:
            conditions.append("UPPER(i.figi) = ?")
            params.append(normalized_figi)
        normalized_put_call = _normalized_upper_filter(put_call)
        if normalized_put_call:
            conditions.append("UPPER(i.put_call) = ?")
            params.append(normalized_put_call)
        if period_start is not None:
            conditions.append("s.period_of_report_utc >= ?")
            params.append(_date_param(period_start))
        if period_end is not None:
            conditions.append("s.period_of_report_utc <= ?")
            params.append(_date_param(period_end))
        if filing_start is not None:
            conditions.append("s.filing_date_utc >= ?")
            params.append(_date_param(filing_start))
        if filing_end is not None:
            conditions.append("s.filing_date_utc <= ?")
            params.append(_date_param(filing_end))
        if min_value is not None:
            conditions.append("i.value >= ?")
            params.append(min_value)
        if min_shares is not None:
            conditions.append("i.ssh_prn_amt >= ?")
            params.append(min_shares)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += """
            ORDER BY COALESCE(s.period_of_report_utc, '') DESC,
                     COALESCE(s.filing_date_utc, '') DESC,
                     COALESCE(i.value, 0) DESC,
                     i.accession_number DESC,
                     CAST(i.info_table_sk AS INTEGER) ASC
        """
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(query, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [
            StoredSec13FHolding(
                dataset_id=row[0],
                dataset_label=row[1],
                accession_number=row[2],
                cik=row[3],
                manager_name=row[4],
                filing_date=row[5],
                filing_date_utc=row[6],
                period_of_report=row[7],
                period_of_report_utc=row[8],
                info_table_sk=row[9],
                issuer=row[10],
                title_of_class=row[11],
                cusip=row[12],
                figi=row[13],
                value=row[14],
                ssh_prn_amt=row[15],
                ssh_prn_type=row[16],
                put_call=row[17],
                investment_discretion=row[18],
                other_manager=row[19],
                voting_auth_sole=row[20],
                voting_auth_shared=row[21],
                voting_auth_none=row[22],
                filing_url=sec_filing_url(row[3], row[2]),
                raw_metadata=_json_obj(row[23]),
            )
            for row in rows
        ]

    async def get_downloaded_media(self, remote_url: str) -> StoredDownloadedMedia | None:
        """Return downloaded media metadata by remote URL."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            return await _get_downloaded_media(db, remote_url)

    async def save_downloaded_media(self, media: StoredDownloadedMedia) -> None:
        """Persist downloaded media metadata."""

        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            await _upsert_downloaded_media(db, media)
            await db.commit()

def _normalized_adanos_sources(sources: list[tuple[str, str]] | None) -> list[tuple[str, str]]:
    return sorted(
        {
            (str(platform).strip(), str(job_id).strip())
            for platform, job_id in (sources or [])
            if str(platform).strip() and str(job_id).strip()
        }
    )


def _adanos_sources_predicate(sources: list[tuple[str, str]], *, alias: str = "") -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: list[Any] = []
    for platform, job_id in sources:
        clauses.append(f"({prefix}platform = ? AND {prefix}job_id = ?)")
        params.extend((platform, job_id))
    return " OR ".join(clauses) or "0 = 1", params


def _adanos_history_exclusions(
    *,
    exclude_job_id: str | None,
    exclude_sources: list[tuple[str, str]] | None,
    alias: str = "",
) -> tuple[list[str], list[Any]]:
    prefix = f"{alias}." if alias else ""
    conditions: list[str] = []
    params: list[Any] = []
    if exclude_job_id:
        conditions.append(f"{prefix}job_id != ?")
        params.append(exclude_job_id)
    normalized_sources = _normalized_adanos_sources(exclude_sources)
    if normalized_sources:
        predicate, source_params = _adanos_sources_predicate(normalized_sources, alias=alias)
        conditions.append(f"NOT ({predicate})")
        params.extend(source_params)
    return conditions, params


def _stored_adanos_stock_rows(rows: list[Any]) -> list[StoredAdanosTrendingStock]:
    return [
        StoredAdanosTrendingStock(
            job_id=row[0],
            platform=row[1],
            rank=row[2],
            window_from=row[3],
            window_to=row[4],
            ticker=row[5],
            company_name=row[6],
            trend=row[7],
            mentions=row[8],
            bullish_pct=row[9],
            bearish_pct=row[10],
            sentiment_score=row[11],
            buzz_score=row[12],
            trend_history=_json_list(row[13]),
            raw_metadata=_json_obj(row[14]),
            fetched_at=row[15],
        )
        for row in rows
    ]


def _stored_adanos_sector_rows(rows: list[Any]) -> list[StoredAdanosTrendingSector]:
    return [
        StoredAdanosTrendingSector(
            job_id=row[0],
            platform=row[1],
            rank=row[2],
            window_from=row[3],
            window_to=row[4],
            sector=row[5],
            top_tickers=[str(item) for item in _json_list(row[6])],
            trend=row[7],
            mentions=row[8],
            bullish_pct=row[9],
            bearish_pct=row[10],
            sentiment_score=row[11],
            buzz_score=row[12],
            trend_history=_json_list(row[13]),
            raw_metadata=_json_obj(row[14]),
            fetched_at=row[15],
        )
        for row in rows
    ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(item: RawItem) -> str:
    payload = json.dumps(
        {
            "url": item.url,
            "text": item.text,
            "metadata": item.metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _latest_analysis_run_id(db: aiosqlite.Connection) -> str | None:
    cursor = await db.execute(
        """
        SELECT analysis_run_id
        FROM llm_analysis_runs
        WHERE status = 'succeeded'
        ORDER BY finished_at DESC, started_at DESC
        LIMIT 1
        """,
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row[0] if row else None


async def _read_analysis_x_reports(db: aiosqlite.Connection, analysis_run_id: str) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT handle, status_id, source_ref, url, posted_at_text, sentiment, tags_json, tickers_json,
               summary, interpretation, importance, confidence
        FROM llm_x_post_analyses
        WHERE analysis_run_id = ?
        ORDER BY handle, posted_at_text DESC, status_id DESC
        """,
        (analysis_run_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(
            {
                "source_ref": row[2],
                "source_id": row[1],
                "title": _analysis_title(row[8]),
                "post_summary": row[8],
                "sentiment": row[5],
                "tags": _json_list(row[6]),
                "tickers": _normalized_tickers(row[7]),
                "interpretation": row[9],
                "importance": _normalized_importance(row[10]),
                "confidence": row[11],
                "urls": [row[3]] if row[3] else [],
            }
        )
    return [
        {
            "handle": handle,
            "overall_summary": [f"{len(posts)} analyzed X post{'s' if len(posts) != 1 else ''}."],
            "posts": posts,
        }
        for handle, posts in grouped.items()
    ]


async def _read_analysis_reddit_report(db: aiosqlite.Connection, analysis_run_id: str) -> dict[str, Any]:
    cursor = await db.execute(
        """
        SELECT subreddit, post_id, source_ref, title, url, created_at_text, sentiment, tags_json, tickers_json,
               summary, interpretation, importance, confidence, comment_sentiment_counts_json
        FROM llm_reddit_post_analyses
        WHERE analysis_run_id = ?
        ORDER BY created_at_text DESC, post_id DESC
        """,
        (analysis_run_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()

    posts = []
    total_comments = 0
    totals = {"bullish": 0, "bearish": 0, "mixed": 0, "neutral": 0, "unclear": 0}
    for row in rows:
        counts = _json_obj(row[13])
        for sentiment in totals:
            value = counts.get(sentiment)
            if isinstance(value, int):
                totals[sentiment] += value
                total_comments += value
        posts.append(
            {
                "source_ref": row[2],
                "source_id": row[1],
                "subreddit": row[0],
                "title": row[3],
                "post_summary": row[9],
                "comments_sentiment": _comment_counts_text(counts),
                "comment_sentiment_counts": counts,
                "sentiment": row[6],
                "tags": _json_list(row[7]),
                "tickers": _normalized_tickers(row[8]),
                "interpretation": row[10],
                "importance": _normalized_importance(row[11]),
                "confidence": row[12],
                "urls": [row[4]] if row[4] else [],
            }
        )
    return {
        "overall_summary": [
            f"{len(posts)} analyzed Reddit post{'s' if len(posts) != 1 else ''} with {total_comments} analyzed comments."
        ],
        "comment_sentiment_counts": totals,
        "posts": posts,
    }


async def _read_x_posts_by_ticker(
    db: aiosqlite.Connection,
    analysis_run_id: str,
    ticker: str,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT t.ticker, x.handle, x.status_id, x.source_ref, x.url, x.summary,
               x.sentiment, x.importance, x.confidence, x.analyzed_at
        FROM llm_x_post_tickers t
        JOIN llm_x_post_analyses x
          ON x.analysis_run_id = t.analysis_run_id
         AND x.status_id = t.status_id
        WHERE t.analysis_run_id = ? AND t.ticker = ?
        ORDER BY x.posted_at_text DESC, x.status_id DESC
        """,
        (analysis_run_id, ticker),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        {
            "source": "x",
            "ticker": row[0],
            "handle": row[1],
            "source_id": row[2],
            "source_ref": row[3],
            "url": row[4],
            "summary": row[5],
            "sentiment": row[6],
            "importance": _normalized_importance(row[7]),
            "confidence": row[8],
            "analyzed_at": row[9],
        }
        for row in rows
    ]


async def _read_reddit_posts_by_ticker(
    db: aiosqlite.Connection,
    analysis_run_id: str,
    ticker: str,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT t.ticker, r.subreddit, r.post_id, r.source_ref, r.url, r.title,
               r.summary, r.sentiment, r.importance, r.confidence, r.analyzed_at
        FROM llm_reddit_post_tickers t
        JOIN llm_reddit_post_analyses r
          ON r.analysis_run_id = t.analysis_run_id
         AND r.post_id = t.post_id
        WHERE t.analysis_run_id = ? AND t.ticker = ?
        ORDER BY r.created_at_text DESC, r.post_id DESC
        """,
        (analysis_run_id, ticker),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        {
            "source": "reddit",
            "ticker": row[0],
            "subreddit": row[1],
            "source_id": row[2],
            "source_ref": row[3],
            "url": row[4],
            "title": row[5],
            "summary": row[6],
            "sentiment": row[7],
            "importance": _normalized_importance(row[8]),
            "confidence": row[9],
            "analyzed_at": row[10],
        }
        for row in rows
    ]


async def _read_x_statistic_points(
    db: aiosqlite.Connection,
    *,
    analysis_run_id: str,
    ticker: str | None,
    fuzzy_tag: str | None,
    sentiment: str | None,
    posted_start: datetime | None,
    posted_end: datetime | None,
) -> list[StoredSocialStatisticPoint]:
    if ticker:
        select_ticker = "t.ticker"
        join_ticker = """
            JOIN llm_x_post_tickers t
              ON t.analysis_run_id = x.analysis_run_id
             AND t.status_id = x.status_id
        """
        conditions = ["x.analysis_run_id = ?", "t.ticker = ?"]
        params: list[Any] = [analysis_run_id, ticker]
    else:
        select_ticker = "NULL"
        join_ticker = ""
        conditions = ["x.analysis_run_id = ?"]
        params = [analysis_run_id]
    if sentiment:
        conditions.append("x.sentiment = ?")
        params.append(sentiment)
    if fuzzy_tag:
        conditions.append("LOWER(x.tags_json) LIKE ?")
        params.append(f"%{fuzzy_tag}%")
    if posted_start is not None:
        conditions.append("x.posted_at_text >= ?")
        params.append(_datetime_param(posted_start))
    if posted_end is not None:
        conditions.append("x.posted_at_text <= ?")
        params.append(_datetime_param(posted_end, end_of_day=True))
    cursor = await db.execute(
        f"""
        SELECT {select_ticker}, x.status_id, x.source_ref, x.handle, x.sentiment,
               x.importance, x.posted_at_text, x.analyzed_at, x.tags_json
        FROM llm_x_post_analyses x
        {join_ticker}
        WHERE {" AND ".join(conditions)}
        ORDER BY x.posted_at_text DESC, x.status_id DESC
        """,
        params,
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        StoredSocialStatisticPoint(
            source="x",
            ticker=row[0],
            source_id=row[1],
            source_ref=row[2],
            label=row[3],
            sentiment=row[4],
            importance=_normalized_importance(row[5]),
            posted_at=row[6],
            analyzed_at=row[7],
        )
        for row in rows
        if fuzzy_tag is None or _tags_contain(row[8], fuzzy_tag)
    ]


async def _read_reddit_statistic_points(
    db: aiosqlite.Connection,
    *,
    analysis_run_id: str,
    ticker: str | None,
    fuzzy_tag: str | None,
    sentiment: str | None,
    posted_start: datetime | None,
    posted_end: datetime | None,
) -> list[StoredSocialStatisticPoint]:
    if ticker:
        select_ticker = "t.ticker"
        join_ticker = """
            JOIN llm_reddit_post_tickers t
              ON t.analysis_run_id = r.analysis_run_id
             AND t.post_id = r.post_id
        """
        conditions = ["r.analysis_run_id = ?", "t.ticker = ?"]
        params: list[Any] = [analysis_run_id, ticker]
    else:
        select_ticker = "NULL"
        join_ticker = ""
        conditions = ["r.analysis_run_id = ?"]
        params = [analysis_run_id]
    if sentiment:
        conditions.append("r.sentiment = ?")
        params.append(sentiment)
    if fuzzy_tag:
        conditions.append("LOWER(r.tags_json) LIKE ?")
        params.append(f"%{fuzzy_tag}%")
    if posted_start is not None:
        conditions.append("r.created_at_text >= ?")
        params.append(_datetime_param(posted_start))
    if posted_end is not None:
        conditions.append("r.created_at_text <= ?")
        params.append(_datetime_param(posted_end, end_of_day=True))
    cursor = await db.execute(
        f"""
        SELECT {select_ticker}, r.post_id, r.source_ref, r.subreddit, r.sentiment,
               r.importance, r.created_at_text, r.analyzed_at, r.tags_json
        FROM llm_reddit_post_analyses r
        {join_ticker}
        WHERE {" AND ".join(conditions)}
        ORDER BY r.created_at_text DESC, r.post_id DESC
        """,
        params,
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        StoredSocialStatisticPoint(
            source="reddit",
            ticker=row[0],
            source_id=row[1],
            source_ref=row[2],
            label=row[3],
            sentiment=row[4],
            importance=_normalized_importance(row[5]),
            posted_at=row[6],
            analyzed_at=row[7],
        )
        for row in rows
        if fuzzy_tag is None or _tags_contain(row[8], fuzzy_tag)
    ]


def _analysis_title(summary: str | None) -> str:
    text = (summary or "Signal").strip()
    if len(text) <= 80:
        return text or "Signal"
    return text[:77].rstrip() + "..."


def _comment_counts_text(counts: dict[str, Any]) -> str:
    parts = [f"{key}: {counts.get(key, 0)}" for key in ("bullish", "bearish", "mixed", "neutral", "unclear")]
    return ", ".join(parts)


async def _read_x_media(db: aiosqlite.Connection, status_id: str) -> list[StoredMediaAsset]:
    cursor = await db.execute(
        """
        SELECT media_type, media_url, raw_json
        FROM raw_x_post_media
        WHERE status_id = ?
        ORDER BY media_url
        """,
        (status_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [await _stored_media_asset(db, row[1], row[0], row[2]) for row in rows]


async def _read_reddit_media(db: aiosqlite.Connection, post_id: str) -> list[StoredMediaAsset]:
    cursor = await db.execute(
        """
        SELECT media_type, media_url, raw_json
        FROM raw_reddit_post_media
        WHERE post_id = ?
        ORDER BY media_url
        """,
        (post_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [await _stored_media_asset(db, row[1], row[0], row[2]) for row in rows]


async def _read_reddit_comments(db: aiosqlite.Connection, post_id: str) -> list[StoredRedditComment]:
    cursor = await db.execute(
        """
        SELECT comment_id, post_id, parent_id, author, body, score, ups, url,
               created_at_text, depth, raw_json, collected_at
        FROM raw_reddit_comments
        WHERE post_id = ?
        ORDER BY COALESCE(depth, 0), created_at_text, comment_id
        """,
        (post_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return [
        StoredRedditComment(
            comment_id=row[0],
            post_id=row[1],
            parent_id=row[2],
            author=row[3],
            body=row[4],
            score=row[5],
            ups=row[6],
            url=row[7],
            created_at_text=row[8],
            depth=row[9],
            raw_metadata=_json_obj(row[10]),
            collected_at=row[11],
        )
        for row in rows
    ]


async def _stored_media_asset(
    db: aiosqlite.Connection,
    remote_url: str,
    media_type: str | None,
    raw_json_value: str | None,
) -> StoredMediaAsset:
    downloaded = await _get_downloaded_media(db, remote_url)
    return StoredMediaAsset(
        remote_url=remote_url,
        media_type=media_type,
        raw_metadata=_json_obj(raw_json_value),
        local_path=downloaded.local_path if downloaded else None,
        content_type=downloaded.content_type if downloaded else None,
        byte_size=downloaded.byte_size if downloaded else None,
        sha256=downloaded.sha256 if downloaded else None,
    )

async def _mapped_row_exists(db: aiosqlite.Connection, item: MappedRawItem) -> bool:
    if item.table == "raw_x_posts":
        query = "SELECT 1 FROM raw_x_posts WHERE handle = ? AND status_id = ?"
    elif item.table == "raw_reddit_posts":
        query = "SELECT 1 FROM raw_reddit_posts WHERE subreddit = ? AND post_id = ?"
    elif item.table == "raw_reddit_comments":
        query = "SELECT 1 FROM raw_reddit_comments WHERE post_id = ? AND comment_id = ?"
    elif item.table == "raw_house_ptr_filings":
        query = "SELECT 1 FROM raw_house_ptr_filings WHERE doc_id = ?"
    elif item.table == "raw_sec_13f_datasets":
        query = "SELECT 1 FROM raw_sec_13f_datasets WHERE dataset_id = ?"
    else:
        raise ValueError(f"Unsupported mapped table: {item.table}")
    cursor = await db.execute(query, item.key)
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


async def _upsert_mapped_item(db: aiosqlite.Connection, item: MappedRawItem) -> None:
    if item.table == "raw_x_posts":
        await _upsert_x_post(db, item.row)
        for media_row in item.media_rows:
            await _upsert_x_media(db, media_row)
        return
    if item.table == "raw_reddit_posts":
        await _upsert_reddit_post(db, item.row)
        for media_row in item.media_rows:
            await _upsert_reddit_media(db, media_row)
        return
    if item.table == "raw_reddit_comments":
        await _upsert_reddit_comment(db, item.row)
        return
    if item.table == "raw_house_ptr_filings":
        await _upsert_house_ptr_filing(db, item.row)
        await _delete_house_ptr_trade_rows(db, item.row["doc_id"])
        for trade_row in item.media_rows:
            await _upsert_house_ptr_trade_row(db, trade_row)
        return
    if item.table == "raw_sec_13f_datasets":
        await _upsert_sec_13f_dataset(db, item.row)
        await _delete_sec_13f_dataset_rows(db, item.row["dataset_id"])
        await _upsert_sec_13f_child_rows(db, item.media_rows)
        return
    raise ValueError(f"Unsupported mapped table: {item.table}")


async def _upsert_x_post(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_x_posts (
            status_id, handle, author_handle, author_name, posted_at_text, url, text,
            reply_count, repost_count, like_count, quote_count, view_count, raw_json, collected_at,
            posted_at_utc
        ) VALUES (
            :status_id, :handle, :author_handle, :author_name, :posted_at_text, :url, :text,
            :reply_count, :repost_count, :like_count, :quote_count, :view_count, :raw_json, :collected_at,
            :posted_at_utc
        )
        ON CONFLICT (handle, status_id) DO UPDATE SET
            author_handle = excluded.author_handle,
            author_name = excluded.author_name,
            posted_at_text = excluded.posted_at_text,
            url = excluded.url,
            text = excluded.text,
            reply_count = excluded.reply_count,
            repost_count = excluded.repost_count,
            like_count = excluded.like_count,
            quote_count = excluded.quote_count,
            view_count = excluded.view_count,
            raw_json = excluded.raw_json,
            collected_at = excluded.collected_at,
            posted_at_utc = excluded.posted_at_utc
        """,
        row,
    )


async def _upsert_x_media(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_x_post_media (
            status_id, media_key, media_type, media_url, alt_text, raw_json
        ) VALUES (
            :status_id, :media_key, :media_type, :media_url, :alt_text, :raw_json
        )
        ON CONFLICT (status_id, media_url) DO UPDATE SET
            media_key = excluded.media_key,
            media_type = excluded.media_type,
            alt_text = excluded.alt_text,
            raw_json = excluded.raw_json
        """,
        row,
    )


async def _upsert_reddit_post(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_reddit_posts (
            post_id, subreddit, fullname, title, author, url, permalink, selftext,
            score, ups, upvote_ratio, num_comments, thumbnail_url, created_at_text,
            raw_json, collected_at, created_at_utc
        ) VALUES (
            :post_id, :subreddit, :fullname, :title, :author, :url, :permalink, :selftext,
            :score, :ups, :upvote_ratio, :num_comments, :thumbnail_url, :created_at_text,
            :raw_json, :collected_at, :created_at_utc
        )
        ON CONFLICT (subreddit, post_id) DO UPDATE SET
            fullname = excluded.fullname,
            title = excluded.title,
            author = excluded.author,
            url = excluded.url,
            permalink = excluded.permalink,
            selftext = excluded.selftext,
            score = excluded.score,
            ups = excluded.ups,
            upvote_ratio = excluded.upvote_ratio,
            num_comments = excluded.num_comments,
            thumbnail_url = excluded.thumbnail_url,
            created_at_text = excluded.created_at_text,
            raw_json = excluded.raw_json,
            collected_at = excluded.collected_at,
            created_at_utc = excluded.created_at_utc
        """,
        row,
    )


async def _upsert_reddit_comment(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_reddit_comments (
            comment_id, post_id, parent_id, author, body, score, ups, url,
            created_at_text, depth, raw_json, collected_at, created_at_utc
        ) VALUES (
            :comment_id, :post_id, :parent_id, :author, :body, :score, :ups, :url,
            :created_at_text, :depth, :raw_json, :collected_at, :created_at_utc
        )
        ON CONFLICT (post_id, comment_id) DO UPDATE SET
            parent_id = excluded.parent_id,
            author = excluded.author,
            body = excluded.body,
            score = excluded.score,
            ups = excluded.ups,
            url = excluded.url,
            created_at_text = excluded.created_at_text,
            depth = excluded.depth,
            raw_json = excluded.raw_json,
            collected_at = excluded.collected_at,
            created_at_utc = excluded.created_at_utc
        """,
        row,
    )


async def _upsert_reddit_media(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_reddit_post_media (
            post_id, media_type, media_url, source_field, raw_json
        ) VALUES (
            :post_id, :media_type, :media_url, :source_field, :raw_json
        )
        ON CONFLICT (post_id, media_url) DO UPDATE SET
            media_type = excluded.media_type,
            source_field = excluded.source_field,
            raw_json = excluded.raw_json
        """,
        row,
    )


async def _upsert_house_ptr_filing(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_house_ptr_filings (
            doc_id, year, name, prefix, first_name, last_name, suffix, display_name,
            name_normalized, status, state, filing_date, filing_date_utc, pdf_url,
            raw_xml_json, tables_json, extraction_status, extraction_error, collected_at
            , extraction_warnings_json, extraction_metadata_json
        ) VALUES (
            :doc_id, :year, :name, :prefix, :first_name, :last_name, :suffix, :display_name,
            :name_normalized, :status, :state, :filing_date, :filing_date_utc, :pdf_url,
            :raw_xml_json, :tables_json, :extraction_status, :extraction_error, :collected_at
            , :extraction_warnings_json, :extraction_metadata_json
        )
        ON CONFLICT (doc_id) DO UPDATE SET
            year = excluded.year,
            name = excluded.name,
            prefix = excluded.prefix,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            suffix = excluded.suffix,
            display_name = excluded.display_name,
            name_normalized = excluded.name_normalized,
            status = excluded.status,
            state = excluded.state,
            filing_date = excluded.filing_date,
            filing_date_utc = excluded.filing_date_utc,
            pdf_url = excluded.pdf_url,
            raw_xml_json = excluded.raw_xml_json,
            tables_json = excluded.tables_json,
            extraction_status = excluded.extraction_status,
            extraction_error = excluded.extraction_error,
            extraction_warnings_json = excluded.extraction_warnings_json,
            extraction_metadata_json = excluded.extraction_metadata_json
        """,
        row,
    )


async def _delete_house_ptr_trade_rows(db: aiosqlite.Connection, doc_id: str) -> None:
    await db.execute("DELETE FROM raw_house_ptr_trade_rows WHERE doc_id = ?", (doc_id,))


async def _upsert_house_ptr_trade_row(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_house_ptr_trade_rows (
            doc_id, table_index, row_index, asset, asset_type_code, asset_type_label,
            stock_ticker, transaction_type, transaction_date,
            transaction_date_utc, transaction_action, amount, raw_cells_json, raw_json
        ) VALUES (
            :doc_id, :table_index, :row_index, :asset, :asset_type_code, :asset_type_label,
            :stock_ticker, :transaction_type, :transaction_date,
            :transaction_date_utc, :transaction_action, :amount, :raw_cells_json, :raw_json
        )
        ON CONFLICT (doc_id, table_index, row_index) DO UPDATE SET
            asset = excluded.asset,
            asset_type_code = excluded.asset_type_code,
            asset_type_label = excluded.asset_type_label,
            stock_ticker = excluded.stock_ticker,
            transaction_type = excluded.transaction_type,
            transaction_date = excluded.transaction_date,
            transaction_date_utc = excluded.transaction_date_utc,
            transaction_action = excluded.transaction_action,
            amount = excluded.amount,
            raw_cells_json = excluded.raw_cells_json,
            raw_json = excluded.raw_json
        """,
        row,
    )


async def _upsert_sec_13f_dataset(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    await db.execute(
        """
        INSERT INTO raw_sec_13f_datasets (
            dataset_id, label, download_url, sha256, byte_size, row_counts_json, downloaded_at
        ) VALUES (
            :dataset_id, :label, :download_url, :sha256, :byte_size, :row_counts_json, :downloaded_at
        )
        ON CONFLICT (dataset_id) DO UPDATE SET
            label = excluded.label,
            download_url = excluded.download_url,
            sha256 = excluded.sha256,
            byte_size = excluded.byte_size,
            row_counts_json = excluded.row_counts_json,
            downloaded_at = excluded.downloaded_at
        """,
        row,
    )


async def _delete_sec_13f_dataset_rows(db: aiosqlite.Connection, dataset_id: str) -> None:
    for table in (
        "raw_sec_13f_submissions",
        "raw_sec_13f_coverpages",
        "raw_sec_13f_other_managers",
        "raw_sec_13f_signatures",
        "raw_sec_13f_summary_pages",
        "raw_sec_13f_other_managers2",
        "raw_sec_13f_info_tables",
    ):
        await db.execute(f"DELETE FROM {table} WHERE dataset_id = ?", (dataset_id,))


async def _upsert_sec_13f_child_row(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    table_name = row.get("table_name")
    if table_name == "submissions":
        await db.execute(
            """
            INSERT INTO raw_sec_13f_submissions (
                dataset_id, accession_number, filing_date, filing_date_utc, submission_type,
                cik, period_of_report, period_of_report_utc, raw_json
            ) VALUES (
                :dataset_id, :accession_number, :filing_date, :filing_date_utc, :submission_type,
                :cik, :period_of_report, :period_of_report_utc, :raw_json
            )
            ON CONFLICT (dataset_id, accession_number) DO UPDATE SET
                filing_date = excluded.filing_date,
                filing_date_utc = excluded.filing_date_utc,
                submission_type = excluded.submission_type,
                cik = excluded.cik,
                period_of_report = excluded.period_of_report,
                period_of_report_utc = excluded.period_of_report_utc,
                raw_json = excluded.raw_json
            """,
            row,
        )
        return
    if table_name == "coverpages":
        await db.execute(
            """
            INSERT INTO raw_sec_13f_coverpages (
                dataset_id, accession_number, manager_name, manager_name_normalized,
                report_type, form_13f_file_number, raw_json
            ) VALUES (
                :dataset_id, :accession_number, :manager_name, :manager_name_normalized,
                :report_type, :form_13f_file_number, :raw_json
            )
            ON CONFLICT (dataset_id, accession_number) DO UPDATE SET
                manager_name = excluded.manager_name,
                manager_name_normalized = excluded.manager_name_normalized,
                report_type = excluded.report_type,
                form_13f_file_number = excluded.form_13f_file_number,
                raw_json = excluded.raw_json
            """,
            row,
        )
        return
    if table_name == "info_tables":
        await db.execute(
            """
            INSERT INTO raw_sec_13f_info_tables (
                dataset_id, accession_number, info_table_sk, issuer, issuer_normalized,
                title_of_class, cusip, figi, value, ssh_prn_amt, ssh_prn_type, put_call,
                investment_discretion, other_manager, voting_auth_sole, voting_auth_shared,
                voting_auth_none, raw_json
            ) VALUES (
                :dataset_id, :accession_number, :info_table_sk, :issuer, :issuer_normalized,
                :title_of_class, :cusip, :figi, :value, :ssh_prn_amt, :ssh_prn_type, :put_call,
                :investment_discretion, :other_manager, :voting_auth_sole, :voting_auth_shared,
                :voting_auth_none, :raw_json
            )
            ON CONFLICT (dataset_id, accession_number, info_table_sk) DO UPDATE SET
                issuer = excluded.issuer,
                issuer_normalized = excluded.issuer_normalized,
                title_of_class = excluded.title_of_class,
                cusip = excluded.cusip,
                figi = excluded.figi,
                value = excluded.value,
                ssh_prn_amt = excluded.ssh_prn_amt,
                ssh_prn_type = excluded.ssh_prn_type,
                put_call = excluded.put_call,
                investment_discretion = excluded.investment_discretion,
                other_manager = excluded.other_manager,
                voting_auth_sole = excluded.voting_auth_sole,
                voting_auth_shared = excluded.voting_auth_shared,
                voting_auth_none = excluded.voting_auth_none,
                raw_json = excluded.raw_json
            """,
            row,
        )
        return
    if table_name in {"other_managers", "other_managers2"}:
        table = "raw_sec_13f_other_managers" if table_name == "other_managers" else "raw_sec_13f_other_managers2"
        await db.execute(
            f"""
            INSERT INTO {table} (dataset_id, accession_number, row_key, raw_json)
            VALUES (:dataset_id, :accession_number, :row_key, :raw_json)
            ON CONFLICT (dataset_id, accession_number, row_key) DO UPDATE SET
                raw_json = excluded.raw_json
            """,
            {**row, "row_key": _sec_13f_row_key(row)},
        )
        return
    if table_name in {"signatures", "summary_pages"}:
        table = "raw_sec_13f_signatures" if table_name == "signatures" else "raw_sec_13f_summary_pages"
        await db.execute(
            f"""
            INSERT INTO {table} (dataset_id, accession_number, raw_json)
            VALUES (:dataset_id, :accession_number, :raw_json)
            ON CONFLICT (dataset_id, accession_number) DO UPDATE SET
                raw_json = excluded.raw_json
            """,
            row,
        )
        return


async def _upsert_sec_13f_child_rows(db: aiosqlite.Connection, rows: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("table_name")), []).append(row)

    if groups.get("submissions"):
        await db.executemany(
            """
            INSERT INTO raw_sec_13f_submissions (
                dataset_id, accession_number, filing_date, filing_date_utc, submission_type,
                cik, period_of_report, period_of_report_utc, raw_json
            ) VALUES (
                :dataset_id, :accession_number, :filing_date, :filing_date_utc, :submission_type,
                :cik, :period_of_report, :period_of_report_utc, :raw_json
            )
            ON CONFLICT (dataset_id, accession_number) DO UPDATE SET
                filing_date = excluded.filing_date,
                filing_date_utc = excluded.filing_date_utc,
                submission_type = excluded.submission_type,
                cik = excluded.cik,
                period_of_report = excluded.period_of_report,
                period_of_report_utc = excluded.period_of_report_utc,
                raw_json = excluded.raw_json
            """,
            groups["submissions"],
        )
    if groups.get("coverpages"):
        await db.executemany(
            """
            INSERT INTO raw_sec_13f_coverpages (
                dataset_id, accession_number, manager_name, manager_name_normalized,
                report_type, form_13f_file_number, raw_json
            ) VALUES (
                :dataset_id, :accession_number, :manager_name, :manager_name_normalized,
                :report_type, :form_13f_file_number, :raw_json
            )
            ON CONFLICT (dataset_id, accession_number) DO UPDATE SET
                manager_name = excluded.manager_name,
                manager_name_normalized = excluded.manager_name_normalized,
                report_type = excluded.report_type,
                form_13f_file_number = excluded.form_13f_file_number,
                raw_json = excluded.raw_json
            """,
            groups["coverpages"],
        )
    if groups.get("info_tables"):
        await db.executemany(
            """
            INSERT INTO raw_sec_13f_info_tables (
                dataset_id, accession_number, info_table_sk, issuer, issuer_normalized,
                title_of_class, cusip, figi, value, ssh_prn_amt, ssh_prn_type, put_call,
                investment_discretion, other_manager, voting_auth_sole, voting_auth_shared,
                voting_auth_none, raw_json
            ) VALUES (
                :dataset_id, :accession_number, :info_table_sk, :issuer, :issuer_normalized,
                :title_of_class, :cusip, :figi, :value, :ssh_prn_amt, :ssh_prn_type, :put_call,
                :investment_discretion, :other_manager, :voting_auth_sole, :voting_auth_shared,
                :voting_auth_none, :raw_json
            )
            ON CONFLICT (dataset_id, accession_number, info_table_sk) DO UPDATE SET
                issuer = excluded.issuer,
                issuer_normalized = excluded.issuer_normalized,
                title_of_class = excluded.title_of_class,
                cusip = excluded.cusip,
                figi = excluded.figi,
                value = excluded.value,
                ssh_prn_amt = excluded.ssh_prn_amt,
                ssh_prn_type = excluded.ssh_prn_type,
                put_call = excluded.put_call,
                investment_discretion = excluded.investment_discretion,
                other_manager = excluded.other_manager,
                voting_auth_sole = excluded.voting_auth_sole,
                voting_auth_shared = excluded.voting_auth_shared,
                voting_auth_none = excluded.voting_auth_none,
                raw_json = excluded.raw_json
            """,
            groups["info_tables"],
        )
    for table_name, sql_table in (("other_managers", "raw_sec_13f_other_managers"), ("other_managers2", "raw_sec_13f_other_managers2")):
        if groups.get(table_name):
            prepared = [{**row, "row_key": _sec_13f_row_key(row)} for row in groups[table_name]]
            await db.executemany(
                f"""
                INSERT INTO {sql_table} (dataset_id, accession_number, row_key, raw_json)
                VALUES (:dataset_id, :accession_number, :row_key, :raw_json)
                ON CONFLICT (dataset_id, accession_number, row_key) DO UPDATE SET
                    raw_json = excluded.raw_json
                """,
                prepared,
            )
    for table_name, sql_table in (("signatures", "raw_sec_13f_signatures"), ("summary_pages", "raw_sec_13f_summary_pages")):
        if groups.get(table_name):
            await db.executemany(
                f"""
                INSERT INTO {sql_table} (dataset_id, accession_number, raw_json)
                VALUES (:dataset_id, :accession_number, :raw_json)
                ON CONFLICT (dataset_id, accession_number) DO UPDATE SET
                    raw_json = excluded.raw_json
                """,
                groups[table_name],
            )


def _sec_13f_row_key(row: dict[str, Any]) -> str:
    raw_json_text = str(row.get("raw_json") or "")
    digest = hashlib.sha256(raw_json_text.encode("utf-8")).hexdigest()[:16]
    return str(row.get("info_table_sk") or digest)


async def _upsert_index_row(db: aiosqlite.Connection, item: RawItem) -> None:
    now = _utc_now()
    await db.execute(
        """
        INSERT INTO raw_item_index (
            source_type, source_id, canonical_url, collected_at, first_seen_at, latest_seen_at, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_type, source_id) DO UPDATE SET
            canonical_url = excluded.canonical_url,
            collected_at = excluded.collected_at,
            latest_seen_at = excluded.latest_seen_at,
            content_hash = excluded.content_hash
        """,
        (
            item.source_type,
            item.source_id,
            item.url,
            item.collected_at.isoformat(),
            now,
            now,
            _content_hash(item),
        ),
    )
