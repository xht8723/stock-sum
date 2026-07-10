"""HTTP job manager fail-safe behavior tests."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_sum.api.jobs import HttpJobManager, SocialReportJobOptions, Sec13FReportJobOptions, StatisticJobOptions, TradingReportJobOptions, TrendingsReportJobOptions
from stock_sum.collectors.api.adanos import AdanosEndpointResult, AdanosTrendingsResult
from stock_sum.config.loader import load_config
from stock_sum.config.models import XUserSourceConfig
from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult, PipelineSectionWarning, Summary
from stock_sum.retention import RetentionSummary
from stock_sum.storage.models import (
    StoredCollectionRun,
    StoredAdanosTrendingSector,
    StoredAdanosTrendingStock,
    StoredHousePtrTradeRow,
    StoredSec13FHolding,
    StoredSocialStatisticPoint,
    StoredTradingStatisticPoint,
    StoredXPost,
)
from stock_sum.worker import _run_request


async def test_report_job_succeeds_with_collection_warning(tmp_path) -> None:
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(
            PipelineCollectionResult(
                scope="social",
                runs=[],
                warnings=[
                    PipelineSectionWarning(
                        section="collector",
                        source_id="reddit.wallstreetbets",
                        phase="collecting",
                        message="temporary reddit failure",
                    )
                ],
            )
        ),
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    job = manager.create_social_report_job(SocialReportJobOptions(mode="discord"))

    await manager.run_social_report_job(job.job_id, SocialReportJobOptions(mode="discord"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert status.artifact_path is not None
    assert len(status.warnings) == 1
    summary = Path(status.summary_path or "").read_text(encoding="utf-8")
    assert "pipeline_warnings" in summary
    assert "failed_sections" in summary
    assert "temporary reddit failure" in summary


async def test_report_job_fails_when_no_usable_social_data(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(
            PipelineCollectionResult(
                scope="social",
                runs=[
                    CollectionRunResult(
                        run_id="run-1",
                        collector_id="x.missing",
                        source_type="raw_x_post",
                        status="failed",
                        collected_count=0,
                        inserted_count=0,
                        updated_count=0,
                        sqlite_path=str(tmp_path / "stock_sum.sqlite3"),
                        error="x failed",
                    )
                ],
                warnings=[
                    PipelineSectionWarning(
                        section="collector",
                        source_id="x.missing",
                        phase="collecting",
                        message="x failed",
                    )
                ],
            )
        ),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[]),
        llm_client_factory=lambda: llm,
    )
    job = manager.create_social_report_job(SocialReportJobOptions(mode="html"))

    await manager.run_social_report_job(job.job_id, SocialReportJobOptions(mode="html"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "failed"
    assert "no usable source data" in str(status.error)
    assert "x.missing" in str(status.error)
    assert status.artifact_path is None
    assert llm.calls == 0
    assert status.warnings[0]["source_id"] == "x.missing"


async def test_social_report_ignores_house_only_data_and_requires_social_data(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[_house_row()]),
        llm_client_factory=lambda: llm,
    )
    job = manager.create_social_report_job(SocialReportJobOptions(mode="text"))

    await manager.run_social_report_job(job.job_id, SocialReportJobOptions(mode="text"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "failed"
    assert "no usable source data" in str(status.error)
    assert llm.calls == 0


async def test_trading_report_succeeds_with_house_data_and_skips_llm(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[_house_row()]),
        llm_client_factory=lambda: llm,
    )
    options = TradingReportJobOptions(mode="text", name="Jane")
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert llm.calls == 0
    artifact = Path(status.artifact_path or "").read_text(encoding="utf-8")
    assert "OFFICIAL TRADING DISCLOSURES" in artifact
    assert "Jane Doe" in artifact


def test_trading_report_options_default_limit_is_100() -> None:
    assert TradingReportJobOptions(days=30).limit == 100


def test_13f_report_options_default_limit_is_20() -> None:
    assert Sec13FReportJobOptions(issuer="nvidia", limit=None).limit == 20


async def test_trading_report_filters_by_asset_type_and_ticker(tmp_path) -> None:
    repository = FakeRepository(
        with_social_data=False,
        house_rows=[
            _house_row(doc_id="amzn", asset="Amazon.com, Inc. - Common Stock (AMZN) [ST]", asset_type_code="ST", stock_ticker="AMZN"),
            _house_row(doc_id="bond", asset="US Treasury Note [GS]", asset_type_code="GS", stock_ticker=None),
        ],
    )
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: repository,
        llm_client_factory=lambda: FakeLLM(),
    )
    options = TradingReportJobOptions(mode="json", asset_type="st", ticker="amzn")
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    assert repository.last_house_filters["asset_type"] == "st"
    assert repository.last_house_filters["ticker"] == "amzn"
    status = manager.get_job(job.job_id)
    assert status is not None
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert summary["house_ptr"][0]["asset_type_code"] == "ST"
    assert summary["house_ptr"][0]["stock_ticker"] == "AMZN"


async def test_trading_report_filters_by_filing_days_and_orders_by_filing_date(tmp_path) -> None:
    repository = FakeRepository(
        with_social_data=False,
        house_rows=[
            _house_row(doc_id="old-filing", transaction_date="2026-07-09", filing_date="2026-07-01"),
            _house_row(doc_id="new-filing", transaction_date="2026-06-20", filing_date="2026-07-08"),
        ],
    )
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: repository,
        llm_client_factory=lambda: FakeLLM(),
    )
    options = TradingReportJobOptions(mode="json", filing_days=14)
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    assert repository.last_house_filters["filing_start"] is not None
    assert repository.last_house_filters["filing_end"] is not None
    assert repository.last_house_filters["order_by_filing_date"] is True
    status = manager.get_job(job.job_id)
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert [row["doc_id"] for row in summary["house_ptr"]] == ["new-filing", "old-filing"]
    assert summary["filters"]["filing_days"] == 14
    assert summary["filters"]["filing_start"] is not None
    assert summary["filters"]["filing_end"] is not None


def test_trading_report_rejects_mixed_filing_relative_and_explicit_dates(tmp_path) -> None:
    manager = HttpJobManager(_test_config(tmp_path), repository_factory=lambda: FakeRepository(with_social_data=False))

    try:
        manager.create_trading_report_job(TradingReportJobOptions(filing_days=1, filing_start_date="2026-07-01"))
    except ValueError as exc:
        assert "filing_days" in str(exc)
    else:
        raise AssertionError("expected filing date validation error")


async def test_13f_report_job_renders_matching_holdings_without_llm(tmp_path) -> None:
    row = StoredSec13FHolding(
        dataset_id="dataset-1",
        dataset_label="2026 March April May 13F",
        accession_number="0001234567-26-000001",
        cik="0001067983",
        manager_name="Berkshire Hathaway Inc",
        filing_date="31-MAY-2026",
        filing_date_utc="2026-05-31",
        period_of_report="31-MAR-2026",
        period_of_report_utc="2026-03-31",
        info_table_sk="1",
        issuer="NVIDIA CORP",
        title_of_class="COM",
        cusip="67066G104",
        figi="BBG000BBJQV0",
        value=1000,
        ssh_prn_amt=50,
        ssh_prn_type="SH",
        put_call="CALL",
        investment_discretion="SOLE",
        other_manager=None,
        voting_auth_sole=50,
        voting_auth_shared=0,
        voting_auth_none=0,
        filing_url="https://www.sec.gov/Archives/edgar/data/1067983/000123456726000001/0001234567-26-000001.txt",
        raw_metadata={},
    )
    repository = FakeRepository(with_social_data=False, sec_13f_rows=[row])
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
        llm_client_factory=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be used")),
    )
    options = Sec13FReportJobOptions(mode="json", issuer="nvidia", limit=20)
    job = manager.create_13f_report_job(options)

    await manager.run_13f_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert summary["sec_13f"][0]["issuer"] == "NVIDIA CORP"
    assert repository.last_sec_13f_filters["issuer"] == "nvidia"


async def test_statistic_job_creates_png_and_summary(tmp_path, monkeypatch) -> None:
    def fake_render(summary, output_path):
        output_path.write_bytes(b"fake-png")

    monkeypatch.setattr("stock_sum.statistics.render_statistic_png", fake_render)
    repository = FakeRepository(
        with_social_data=False,
        social_statistic_points=[
            StoredSocialStatisticPoint(
                source="x",
                ticker="NVDA",
                source_id="1",
                source_ref="x1",
                label="aleabitoreddit",
                sentiment="bullish",
                importance="high",
                posted_at="2026-06-30T00:00:00+00:00",
                analyzed_at="2026-06-30T01:00:00+00:00",
            )
        ],
    )
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
    )
    options = StatisticJobOptions(mode="social", ticker="NVDA", days=30)
    job = manager.create_statistic_job(options)

    await manager.run_statistic_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert status.artifact_media_type == "image/png"
    assert Path(status.artifact_path or "").read_bytes() == b"fake-png"
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert summary["statistic_mode"] == "social"
    populated_bucket = next(item for item in summary["buckets"] if item["post_count"] == 1)
    assert populated_bucket["avg_sentiment_score"] == 1.0


async def test_trendings_job_fetches_persists_and_renders(tmp_path, monkeypatch) -> None:
    async def fake_fetch(self, *, from_date, to_date):
        return AdanosTrendingsResult(
            skipped=False,
            responses=[
                AdanosEndpointResult(
                    platform="reddit",
                    category="stocks",
                    endpoint="/reddit/stocks/v1/trending",
                    request_args={"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": 100},
                    status="succeeded",
                    raw_response_text='[{"ticker":"NVDA"}]',
                    rows=[
                        {
                            "ticker": "NVDA",
                            "company_name": "NVIDIA Corp",
                            "rank": 1,
                            "trend": "up",
                            "mentions": 10,
                            "bullish_pct": 60,
                            "bearish_pct": 20,
                        }
                    ],
                )
            ],
        )

    monkeypatch.setattr("stock_sum.collectors.api.adanos.AdanosClient.fetch_trendings", fake_fetch)
    repository = FakeRepository(with_social_data=False)
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
        renderer_factory=lambda title: FakeRenderer(title, []),
    )
    options = TrendingsReportJobOptions(mode="discord", from_date="2026-07-01", to_date="2026-07-06", limit=1)
    job = manager.create_trendings_report_job(options)

    await manager.run_trendings_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert status.artifact_media_type == "text/markdown; charset=utf-8"
    assert repository.adanos_saved_job_ids == [job.job_id]
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert summary["filters"]["display_limit"] == 1
    assert summary["summary"]["stocks"][0]["ticker"] == "NVDA"
    assert summary["summary"]["changes"] == []


async def test_trendings_job_detects_mentions_sentiment_and_darkhorse_changes(tmp_path, monkeypatch) -> None:
    async def fake_fetch(self, *, from_date, to_date):
        return AdanosTrendingsResult(
            skipped=False,
            responses=[
                AdanosEndpointResult(
                    platform="reddit",
                    category="stocks",
                    endpoint="/reddit/stocks/v1/trending",
                    request_args={"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": 100},
                    status="succeeded",
                    raw_response_text="[]",
                    rows=[
                        {
                            "ticker": "NVDA",
                            "company_name": "NVIDIA Corp",
                            "rank": 1,
                            "trend": "up",
                            "mentions": 200,
                            "bullish_pct": 75,
                            "bearish_pct": 5,
                        },
                        {
                            "ticker": "TSLA",
                            "company_name": "Tesla Inc",
                            "rank": 2,
                            "trend": "up",
                            "mentions": 120,
                            "bullish_pct": 45,
                            "bearish_pct": 30,
                        },
                        {
                            "ticker": "LOWVOL",
                            "company_name": "Low Volume",
                            "rank": 3,
                            "trend": "up",
                            "mentions": 10,
                            "bullish_pct": 95,
                            "bearish_pct": 0,
                        },
                        {
                            "ticker": "AMD",
                            "company_name": "Advanced Micro Devices",
                            "rank": 4,
                            "trend": "up",
                            "mentions": 100,
                            "bullish_pct": 80,
                            "bearish_pct": 5,
                        },
                    ],
                )
            ],
        )

    monkeypatch.setattr("stock_sum.collectors.api.adanos.AdanosClient.fetch_trendings", fake_fetch)
    repository = FakeRepository(with_social_data=False)
    repository.adanos_stocks.extend(
        [
            StoredAdanosTrendingStock(
                job_id="prior-job",
                platform="reddit",
                rank=1,
                window_from="2026-06-30",
                window_to="2026-07-01",
                ticker="NVDA",
                company_name="NVIDIA Corp",
                trend="flat",
                mentions=100,
                bullish_pct=40,
                bearish_pct=30,
                sentiment_score=None,
                buzz_score=None,
                trend_history=[],
                raw_metadata={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
            ),
            StoredAdanosTrendingStock(
                job_id="prior-job",
                platform="reddit",
                rank=1,
                window_from="2026-06-30",
                window_to="2026-07-01",
                ticker="AMD",
                company_name="Advanced Micro Devices",
                trend="flat",
                mentions=100,
                bullish_pct=40,
                bearish_pct=30,
                sentiment_score=None,
                buzz_score=None,
                trend_history=[],
                raw_metadata={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
            ),
        ]
    )
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
        renderer_factory=lambda title: FakeRenderer(title, []),
    )
    options = TrendingsReportJobOptions(
        mode="discord",
        from_date="2026-07-01",
        to_date="2026-07-06",
        minimum_mentions=50,
        mentions_change_pct=30,
        sentiment_change_pct=30,
    )
    job = manager.create_trendings_report_job(options)

    await manager.run_trendings_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    changes = summary["summary"]["changes"]

    assert [(row["ticker"], row["change_type"]) for row in changes] == [
        ("NVDA", "mentions + sentiment"),
        ("AMD", "sentiment"),
        ("TSLA", "darkhorse"),
    ]
    nvda = changes[0]
    assert nvda["previous_mentions"] == 100
    assert nvda["current_mentions"] == 200
    assert nvda["mentions_delta_pct"] == 100.0
    assert nvda["bullish_delta_points"] == 35
    assert all(row["ticker"] != "LOWVOL" for row in changes)


async def test_trading_report_sorts_rows_by_transaction_date(tmp_path) -> None:
    old_row = _house_row(doc_id="old", asset="OLD", transaction_date="2026-06-01")
    new_row = _house_row(doc_id="new", asset="NEW", transaction_date="2026-06-30")
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[old_row, new_row]),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = TradingReportJobOptions(mode="json", days=90)
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert [row["asset"] for row in summary["house_ptr"]] == ["NEW", "OLD"]


async def test_report_job_uses_recent_cache_and_rerenders_requested_mode(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    renderer_calls: list[tuple[str, str, str]] = []
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
        renderer_factory=lambda title: FakeRenderer(title, renderer_calls),
    )
    first_options = SocialReportJobOptions(mode="html", detail="full")
    first_job = manager.create_social_report_job(first_options)
    await manager.run_social_report_job(first_job.job_id, first_options)

    second_options = SocialReportJobOptions(mode="discord", detail="minimum")
    second_job = manager.create_social_report_job(second_options)
    await manager.run_social_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert second_status.cache_age_seconds is not None
    assert second_status.artifact_path is not None
    assert second_status.artifact_path.endswith("report.md")
    assert Path(second_status.artifact_path).read_text(encoding="utf-8") == "Market Social Digest:discord:minimum"
    assert renderer_calls == [("Market Social Digest", "html", "full"), ("Market Social Digest", "discord", "minimum")]
    assert pipeline.calls == 1
    assert llm.calls == 1


async def test_report_job_cache_miss_when_content_options_change(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(first_options)
    await manager.run_social_report_job(first_job.job_id, first_options)

    second_options = SocialReportJobOptions(mode="html", instructions="Focus on semiconductor names.")
    second_job = manager.create_social_report_job(second_options)
    await manager.run_social_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert second_status.cached_from_job_id is None
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_social_report_reddit_method_flows_to_pipeline_and_cache_key(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    xpoz_options = SocialReportJobOptions(mode="html", reddit_method="xpoz")
    rss_options = SocialReportJobOptions(mode="html", reddit_method="rss")

    first_job = manager.create_social_report_job(xpoz_options)
    await manager.run_social_report_job(first_job.job_id, xpoz_options)
    second_job = manager.create_social_report_job(rss_options)
    await manager.run_social_report_job(second_job.job_id, rss_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.reddit_methods == ["xpoz", "rss"]
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_social_report_x_method_flows_to_pipeline_and_cache_key(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    xpoz_options = SocialReportJobOptions(mode="html", x_method="xpoz")
    rss_options = SocialReportJobOptions(mode="html", x_method="rss")

    first_job = manager.create_social_report_job(xpoz_options)
    await manager.run_social_report_job(first_job.job_id, xpoz_options)
    second_job = manager.create_social_report_job(rss_options)
    await manager.run_social_report_job(second_job.job_id, rss_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.x_methods == ["xpoz", "rss"]
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_cache_expires_after_ttl(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(first_job.job_id, options)
    first_status = manager.get_job(first_job.job_id)
    assert first_status is not None
    first_status.finished_at = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    manager._save(first_status)

    second_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_ignores_cache_when_cached_summary_is_missing(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(first_job.job_id, options)
    first_status = manager.get_job(first_job.job_id)
    assert first_status is not None
    assert first_status.summary_path is not None
    Path(first_status.summary_path).unlink()

    second_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_does_not_use_cache_when_disabled(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    config = _test_config(tmp_path)
    manager = HttpJobManager(
        config.model_copy(update={"server": config.server.model_copy(update={"report_cache_ttl_seconds": 0})}),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(first_job.job_id, options)
    second_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_runs_retention_after_regular_and_cache_hit_jobs(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    retention = FakeRetentionService()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
        retention_service_factory=lambda: retention,
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(first_job.job_id, options)
    second_job = manager.create_social_report_job(options)
    await manager.run_social_report_job(second_job.job_id, options)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert retention.calls == 2
    assert first_status is not None
    assert second_status is not None
    assert first_status.cleanup_result is not None
    assert second_status.cache_hit is True
    assert second_status.cleanup_result is not None


async def test_identical_concurrent_report_jobs_coalesce_to_one_pipeline_run(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = SocialReportJobOptions(mode="html", detail="full")
    second_options = SocialReportJobOptions(mode="discord", detail="minimum")
    first_job = manager.create_social_report_job(first_options)
    first_task = asyncio.create_task(manager.run_social_report_job(first_job.job_id, first_options))
    await asyncio.sleep(0.01)
    second_job = manager.create_social_report_job(second_options)
    second_task = asyncio.create_task(manager.run_social_report_job(second_job.job_id, second_options))

    await asyncio.gather(first_task, second_task)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "succeeded"
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.coalesced_wait_seconds is not None
    assert second_status.artifact_path is not None
    assert second_status.artifact_path.endswith("report.md")
    assert second_status.cache_hit is False
    assert pipeline.calls == 1
    assert llm.calls == 1


async def test_concurrent_report_jobs_do_not_coalesce_when_content_options_change(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = SocialReportJobOptions(mode="html")
    second_options = SocialReportJobOptions(mode="html", instructions="Focus on semiconductor names.")
    first_job = manager.create_social_report_job(first_options)
    second_job = manager.create_social_report_job(second_options)

    await asyncio.gather(
        manager.run_social_report_job(first_job.job_id, first_options),
        manager.run_social_report_job(second_job.job_id, second_options),
    )

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "succeeded"
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id is None
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_coalesced_report_job_fails_when_leader_fails(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05, fail=True)
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    first_task = asyncio.create_task(manager.run_social_report_job(first_job.job_id, options))
    await asyncio.sleep(0.01)
    second_job = manager.create_social_report_job(options)
    second_task = asyncio.create_task(manager.run_social_report_job(second_job.job_id, options))

    await asyncio.gather(first_task, second_task)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "failed"
    assert second_status.status == "failed"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert f"Coalesced report leader {first_job.job_id} failed" in str(second_status.error)
    assert pipeline.calls == 1


async def test_inflight_report_coalescing_can_be_disabled(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    config = _test_config(tmp_path)
    manager = HttpJobManager(
        config.model_copy(update={"server": config.server.model_copy(update={"coalesce_inflight_reports": False})}),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    second_job = manager.create_social_report_job(options)

    await asyncio.gather(
        manager.run_social_report_job(first_job.job_id, options),
        manager.run_social_report_job(second_job.job_id, options),
    )

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.coalesced_from_job_id is None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2


async def test_coalesced_report_job_runs_retention_after_writing_artifact(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    retention = FakeRetentionService()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
        retention_service_factory=lambda: retention,
    )
    options = SocialReportJobOptions(mode="html")
    first_job = manager.create_social_report_job(options)
    first_task = asyncio.create_task(manager.run_social_report_job(first_job.job_id, options))
    await asyncio.sleep(0.01)
    second_job = manager.create_social_report_job(options)
    second_task = asyncio.create_task(manager.run_social_report_job(second_job.job_id, options))

    await asyncio.gather(first_task, second_task)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.cleanup_result is not None
    assert retention.calls == 2


async def test_completed_jobs_older_than_retention_are_evicted_from_memory_and_reload_from_disk(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config)
    job = manager.create_collect_job()
    status = manager.get_job(job.job_id)
    assert status is not None
    status.status = "succeeded"
    status.phase = "succeeded"
    status.finished_at = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    status.artifact_path = str(tmp_path / "artifact.json")
    manager._save(status)

    evicted = manager._refresh_memory_status()

    assert evicted == 1
    assert job.job_id not in manager._jobs
    reloaded = manager.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.job_id == job.job_id
    assert reloaded.in_memory_jobs == 1


async def test_in_memory_job_cap_evicts_oldest_finished_jobs_and_keeps_active_jobs(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config)
    finished_ids: list[str] = []
    for index in range(4):
        job = manager.create_collect_job()
        status = manager.get_job(job.job_id)
        assert status is not None
        status.status = "succeeded"
        status.phase = "succeeded"
        status.finished_at = (datetime.now(timezone.utc) - timedelta(minutes=10 - index)).isoformat()
        status.artifact_path = str(tmp_path / f"artifact-{index}.json")
        manager._save(status)
        finished_ids.append(job.job_id)
    running = manager.create_collect_job()
    manager._mark_running(running.job_id, phase="running")
    manager.config = config.model_copy(update={"server": config.server.model_copy(update={"max_in_memory_jobs": 3})})

    evicted = manager._refresh_memory_status(protected_job_ids={running.job_id})

    assert evicted == 2
    assert running.job_id in manager._jobs
    assert finished_ids[0] not in manager._jobs
    assert len(manager._jobs) == 3


async def test_inflight_leader_is_preserved_when_memory_cache_is_pruned(tmp_path) -> None:
    config = _test_config(tmp_path)
    config = config.model_copy(update={"server": config.server.model_copy(update={"max_in_memory_jobs": 1})})
    manager = HttpJobManager(config)
    leader = manager.create_social_report_job(SocialReportJobOptions(mode="html"))
    old = manager.create_collect_job()
    old_status = manager.get_job(old.job_id)
    assert old_status is not None
    old_status.status = "succeeded"
    old_status.phase = "succeeded"
    old_status.finished_at = datetime.now(timezone.utc).isoformat()
    manager._save(old_status)
    is_leader, _ = await manager._join_or_register_inflight_report(leader.job_id, leader.cache_key)
    assert is_leader is True

    manager._refresh_memory_status()

    assert leader.job_id in manager._jobs
    assert old.job_id not in manager._jobs


async def test_completed_memory_record_is_evicted_when_status_file_is_deleted(tmp_path) -> None:
    manager = HttpJobManager(_test_config(tmp_path))
    job = manager.create_collect_job()
    status = manager.get_job(job.job_id)
    assert status is not None
    status.status = "succeeded"
    status.phase = "succeeded"
    status.finished_at = datetime.now(timezone.utc).isoformat()
    manager._save(status)
    (manager._job_dir(job.job_id) / "status.json").unlink()

    evicted = manager._refresh_memory_status()

    assert evicted == 1
    assert job.job_id not in manager._jobs


async def test_default_http_jobs_spawn_subprocess_worker_and_record_metadata(monkeypatch, tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config)
    job = manager.create_collect_job()
    calls = []

    class FakeProcess:
        pid = 4321
        returncode = 0

        async def communicate(self):
            request_path = Path(calls[0][4])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            status_path = Path(config.server.artifact_dir) / request["job_id"] / "status.json"
            data = json.loads(status_path.read_text(encoding="utf-8"))
            data.update(
                {
                    "status": "succeeded",
                    "phase": "succeeded",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "artifact_path": str(status_path),
                    "artifact_media_type": "application/json",
                }
            )
            status_path.write_text(json.dumps(data), encoding="utf-8")
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await manager.run_collect_job(job.job_id)

    status = manager.get_job(job.job_id)
    assert calls[0][:3] == (sys.executable, "-m", "stock_sum.worker")
    assert calls[0][3] == "--request"
    assert status.status == "succeeded"
    assert status.worker_pid == 4321
    assert status.worker_exit_code == 0
    assert status.worker_mode == "subprocess"
    assert status.worker_runtime_seconds is not None


async def test_stale_running_jobs_are_marked_failed_on_manager_startup(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config, recover_stale_jobs=False)
    job = manager.create_collect_job()
    manager._mark_running(job.job_id, phase="collecting")

    restarted = HttpJobManager(config)
    status = restarted.get_job(job.job_id)

    assert status.status == "failed"
    assert status.phase == "failed"
    assert status.error == "Job was interrupted by daemon restart before completion."
    assert status.finished_at is not None


async def test_worker_entrypoint_renders_cached_report_and_updates_status(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config, use_subprocess_workers=False, recover_stale_jobs=False)
    source_options = SocialReportJobOptions(mode="json")
    source = manager.create_social_report_job(source_options)
    summary_path = manager._job_dir(source.job_id) / "summary.json"
    manager._write_json(
        summary_path,
        {
            "report_type": "social",
            "summary": {"executive_summary": "Cached result"},
            "pipeline_warnings": [],
            "failed_sections": [],
        },
    )
    manager._mark_succeeded(
        source.job_id,
        artifact_path=str(summary_path),
        artifact_media_type="application/json",
        summary_path=str(summary_path),
        cache_key=source.cache_key,
    )
    current_options = SocialReportJobOptions(mode="text")
    current = manager.create_social_report_job(current_options)
    request_path = manager._job_dir(current.job_id) / "worker-request.json"
    manager._write_json(
        request_path,
        {
            "schema_version": 1,
            "operation": "http_render_cached_artifact_job",
            "job_id": current.job_id,
            "config": config.model_dump(mode="json"),
            "payload": {"kind": "social_report", "options": asdict(current_options), "cache_hit_job_id": source.job_id},
        },
    )

    code = await _run_request(request_path)

    status = manager.get_job(current.job_id)
    assert code == 0
    assert status.status == "succeeded"
    assert status.cache_hit is True
    assert status.cached_from_job_id == source.job_id
    assert status.artifact_path is not None
    assert Path(status.artifact_path).exists()
    assert status.cleanup_result is not None


async def test_trading_report_uses_recent_cache(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    repository = FakeRepository(with_social_data=False, house_rows=[_house_row()])
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: repository,
    )
    options = TradingReportJobOptions(mode="json", name="Jane")
    first_job = manager.create_trading_report_job(options)
    await manager.run_trading_report_job(first_job.job_id, options)
    second_job = manager.create_trading_report_job(options)
    await manager.run_trading_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert repository.house_read_calls == 1


async def test_trading_report_force_refresh_bypasses_completed_cache(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[_house_row()]),
    )
    options = TradingReportJobOptions(mode="json", name="Jane", force_refresh=True)
    first_job = manager.create_trading_report_job(options)
    await manager.run_trading_report_job(first_job.job_id, options)
    second_job = manager.create_trading_report_job(options)
    await manager.run_trading_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert second_status.cached_from_job_id is None
    assert pipeline.calls == 2


async def test_13f_report_uses_recent_cache(tmp_path) -> None:
    row = _sec_13f_row()
    repository = FakeRepository(with_social_data=False, sec_13f_rows=[row])
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
    )
    options = Sec13FReportJobOptions(mode="json", issuer="nvidia")
    first_job = manager.create_13f_report_job(options)
    await manager.run_13f_report_job(first_job.job_id, options)
    second_job = manager.create_13f_report_job(options)
    await manager.run_13f_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert repository.sec_13f_read_calls == 1


async def test_trendings_cache_reuses_summary_with_different_display_limit(tmp_path, monkeypatch) -> None:
    fetch_calls = 0

    async def fake_fetch(self, *, from_date, to_date):
        nonlocal fetch_calls
        fetch_calls += 1
        return _adanos_result(from_date, to_date)

    monkeypatch.setattr("stock_sum.collectors.api.adanos.AdanosClient.fetch_trendings", fake_fetch)
    repository = FakeRepository(with_social_data=False)
    renderer_calls: list[tuple[str, str, str]] = []
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
        renderer_factory=lambda title: FakeRenderer(title, renderer_calls),
    )
    first_options = TrendingsReportJobOptions(mode="discord", from_date="2026-07-01", to_date="2026-07-06", limit=1)
    first_job = manager.create_trendings_report_job(first_options)
    await manager.run_trendings_report_job(first_job.job_id, first_options)
    second_options = TrendingsReportJobOptions(mode="discord", from_date="2026-07-01", to_date="2026-07-06", limit=5)
    second_job = manager.create_trendings_report_job(second_options)
    await manager.run_trendings_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert Path(second_status.artifact_path or "").read_text(encoding="utf-8") == "Trending Market Sentiment:discord:trendings:5"
    assert fetch_calls == 1
    assert repository.adanos_saved_job_ids == [first_job.job_id]


async def test_statistic_cache_reuses_png_artifact(tmp_path, monkeypatch) -> None:
    render_calls = 0

    def fake_render(summary, output_path):
        nonlocal render_calls
        render_calls += 1
        output_path.write_bytes(f"fake-png-{render_calls}".encode("utf-8"))

    monkeypatch.setattr("stock_sum.statistics.render_statistic_png", fake_render)
    repository = FakeRepository(
        with_social_data=False,
        social_statistic_points=[
            StoredSocialStatisticPoint(
                source="x",
                ticker="NVDA",
                source_id="1",
                source_ref="x1",
                label="aleabitoreddit",
                sentiment="bullish",
                importance="high",
                posted_at="2026-06-30T00:00:00+00:00",
                analyzed_at="2026-06-30T01:00:00+00:00",
            )
        ],
    )
    manager = HttpJobManager(_test_config(tmp_path), repository_factory=lambda: repository)
    options = StatisticJobOptions(mode="social", ticker="NVDA", days=30)
    first_job = manager.create_statistic_job(options)
    await manager.run_statistic_job(first_job.job_id, options)
    second_job = manager.create_statistic_job(options)
    await manager.run_statistic_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert Path(second_status.artifact_path or "").read_bytes() == b"fake-png-1"
    assert render_calls == 1


async def test_identical_concurrent_trendings_jobs_coalesce_to_one_fetch(tmp_path, monkeypatch) -> None:
    fetch_calls = 0

    async def fake_fetch(self, *, from_date, to_date):
        nonlocal fetch_calls
        fetch_calls += 1
        await asyncio.sleep(0.05)
        return _adanos_result(from_date, to_date)

    monkeypatch.setattr("stock_sum.collectors.api.adanos.AdanosClient.fetch_trendings", fake_fetch)
    repository = FakeRepository(with_social_data=False)
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
        renderer_factory=lambda title: FakeRenderer(title, []),
    )
    first_options = TrendingsReportJobOptions(mode="discord", from_date="2026-07-01", to_date="2026-07-06", limit=1)
    second_options = TrendingsReportJobOptions(mode="discord", from_date="2026-07-01", to_date="2026-07-06", limit=5)
    first_job = manager.create_trendings_report_job(first_options)
    first_task = asyncio.create_task(manager.run_trendings_report_job(first_job.job_id, first_options))
    await asyncio.sleep(0.01)
    second_job = manager.create_trendings_report_job(second_options)
    second_task = asyncio.create_task(manager.run_trendings_report_job(second_job.job_id, second_options))

    await asyncio.gather(first_task, second_task)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.cache_hit is False
    assert Path(second_status.artifact_path or "").read_text(encoding="utf-8") == "Trending Market Sentiment:discord:trendings:5"
    assert fetch_calls == 1


class FakePipeline:
    def __init__(self, result: PipelineCollectionResult, *, delay_seconds: float = 0, fail: bool = False) -> None:
        self.result = result
        self.delay_seconds = delay_seconds
        self.fail = fail
        self.calls = 0
        self.x_methods: list[str] = []
        self.reddit_methods: list[str] = []

    async def collect_sources(self, *, collector_ids=None, scope: str = "social", x_method: str = "xpoz", reddit_method: str = "xpoz") -> PipelineCollectionResult:
        self.calls += 1
        self.x_methods.append(x_method)
        self.reddit_methods.append(reddit_method)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("pipeline failed")
        return self.result

    async def collect_collector(self, collector_id: str, *, raise_on_error: bool = True):
        self.calls += 1
        return CollectionRunResult(
            run_id="house-run",
            collector_id=collector_id,
            source_type="house_ptr_disclosure",
            status="succeeded",
            collected_count=1,
            inserted_count=1,
            updated_count=0,
            sqlite_path="stock_sum.sqlite3",
        )


class FakeRepository:
    def __init__(self, *, with_social_data: bool, house_rows=None, sec_13f_rows=None, social_statistic_points=None, trading_statistic_points=None) -> None:
        self.with_social_data = with_social_data
        self.house_rows = house_rows or []
        self.sec_13f_rows = sec_13f_rows or []
        self.social_statistic_points = social_statistic_points or []
        self.trading_statistic_points = trading_statistic_points or []
        self.x_analysis_rows = []
        self.reddit_post_analysis_rows = []
        self.reddit_comment_analysis_rows = []
        self.last_house_filters = {}
        self.last_sec_13f_filters = {}
        self.house_read_calls = 0
        self.sec_13f_read_calls = 0
        self.adanos_saved_job_ids: list[str] = []
        self.adanos_stocks: list[StoredAdanosTrendingStock] = []
        self.adanos_sectors: list[StoredAdanosTrendingSector] = []

    async def list_collection_runs(self, *, limit: int = 20):
        return [
            StoredCollectionRun(
                run_id="sec-run",
                collector_id="sec.13f",
                source_type="sec_13f_dataset",
                status="succeeded",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                collected_count=1,
                inserted_count=1,
                updated_count=0,
                error_text=None,
            ),
            StoredCollectionRun(
                run_id="house-run",
                collector_id="house.ptr",
                source_type="house_ptr_disclosure",
                status="succeeded",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                collected_count=1,
                inserted_count=1,
                updated_count=0,
                error_text=None,
            )
        ]

    async def read_x_posts(self, *, handles=None, since_posted_at=None, collector_id=None, since=None, limit=50):
        if not self.with_social_data:
            return []
        return [
            StoredXPost(
                status_id="1",
                handle="aleabitoreddit",
                author_handle="aleabitoreddit",
                author_name="Serenity",
                posted_at_text="2999-01-01T00:00:00+00:00",
                url="https://x.com/aleabitoreddit/status/1",
                text="market signal",
                reply_count=1,
                repost_count=2,
                like_count=3,
                quote_count=4,
                view_count=5,
                raw_metadata={},
                collected_at="2026-06-29T00:00:00+00:00",
            )
        ]

    async def read_reddit_posts(self, *, subreddits=None, since_posted_at=None, collector_id=None, since=None, limit=50):
        return []

    async def existing_house_ptr_doc_ids(self, *, year=None):
        return set()

    async def read_house_ptr_trades(
        self,
        *,
        name_contains=None,
        transaction_start=None,
        transaction_end=None,
        filing_start=None,
        filing_end=None,
        asset_type=None,
        ticker=None,
        limit=None,
        order_by_filing_date=False,
    ):
        self.house_read_calls += 1
        self.last_house_filters = {
            "asset_type": asset_type,
            "ticker": ticker,
            "transaction_start": transaction_start,
            "transaction_end": transaction_end,
            "filing_start": filing_start,
            "filing_end": filing_end,
            "order_by_filing_date": order_by_filing_date,
        }
        rows = list(self.house_rows)
        if asset_type:
            rows = [row for row in rows if (row.asset_type_code or "").upper() == asset_type.upper()]
        if ticker:
            rows = [row for row in rows if (row.stock_ticker or "").upper() == ticker.upper()]
        if filing_start:
            rows = [row for row in rows if row.filing_date_utc and row.filing_date_utc >= filing_start.isoformat()]
        if filing_end:
            rows = [row for row in rows if row.filing_date_utc and row.filing_date_utc <= filing_end.isoformat()]
        return rows if limit is None else rows[:limit]

    async def read_sec_13f_holdings(
        self,
        *,
        manager=None,
        cik=None,
        accession_number=None,
        issuer=None,
        cusip=None,
        figi=None,
        put_call=None,
        period_start=None,
        period_end=None,
        filing_start=None,
        filing_end=None,
        min_value=None,
        min_shares=None,
        limit=20,
    ):
        self.sec_13f_read_calls += 1
        self.last_sec_13f_filters = {"manager": manager, "issuer": issuer, "cusip": cusip, "limit": limit}
        rows = list(self.sec_13f_rows)
        if issuer:
            rows = [row for row in rows if issuer.lower() in (row.issuer or "").lower()]
        if manager:
            rows = [row for row in rows if manager.lower() in (row.manager_name or "").lower()]
        if cusip:
            rows = [row for row in rows if (row.cusip or "").upper() == cusip.upper()]
        return rows[:limit]

    async def read_social_statistic_points(
        self,
        *,
        ticker=None,
        fuzzy_tag=None,
        source=None,
        sentiment=None,
        posted_start=None,
        posted_end=None,
        analysis_run_id=None,
    ):
        rows = list(self.social_statistic_points)
        if ticker:
            rows = [row for row in rows if (row.ticker or "").upper() == ticker.upper()]
        if fuzzy_tag:
            rows = [row for row in rows if fuzzy_tag.lower() in (row.label or "").lower()]
        if source and source != "all":
            rows = [row for row in rows if row.source == source]
        if sentiment:
            rows = [row for row in rows if row.sentiment == sentiment]
        return rows

    async def read_trading_statistic_points(
        self,
        *,
        name_contains=None,
        asset_name=None,
        transaction_start=None,
        transaction_end=None,
        asset_type=None,
        ticker=None,
        action=None,
    ):
        rows = list(self.trading_statistic_points or self.house_rows)
        if ticker:
            rows = [row for row in rows if (row.stock_ticker or "").upper() == ticker.upper()]
        if asset_name:
            rows = [row for row in rows if asset_name.lower() in (row.asset or "").lower()]
        if asset_type:
            rows = [row for row in rows if (row.asset_type_code or "").upper() == asset_type.upper()]
        if action:
            rows = [row for row in rows if row.transaction_action == action]
        return rows

    async def save_adanos_trendings(self, *, job_id, from_date, to_date, responses):
        self.adanos_saved_job_ids.append(job_id)
        for response in responses:
            if response.status != "succeeded":
                continue
            for index, row in enumerate(response.rows, start=1):
                if response.category == "stocks":
                    self.adanos_stocks.append(
                        StoredAdanosTrendingStock(
                            job_id=job_id,
                            platform=response.platform,
                            rank=row.get("rank") or index,
                            window_from=str(from_date),
                            window_to=str(to_date),
                            ticker=row.get("ticker"),
                            company_name=row.get("company_name"),
                            trend=row.get("trend"),
                            mentions=row.get("mentions"),
                            bullish_pct=row.get("bullish_pct"),
                            bearish_pct=row.get("bearish_pct"),
                            sentiment_score=row.get("sentiment_score"),
                            buzz_score=row.get("buzz_score"),
                            trend_history=row.get("trend_history"),
                            raw_metadata=row,
                            fetched_at=response.fetched_at.isoformat(),
                        )
                    )
                else:
                    self.adanos_sectors.append(
                        StoredAdanosTrendingSector(
                            job_id=job_id,
                            platform=response.platform,
                            rank=row.get("rank") or index,
                            window_from=str(from_date),
                            window_to=str(to_date),
                            sector=row.get("sector"),
                            top_tickers=row.get("top_tickers"),
                            trend=row.get("trend"),
                            mentions=row.get("mentions"),
                            bullish_pct=row.get("bullish_pct"),
                            bearish_pct=row.get("bearish_pct"),
                            sentiment_score=row.get("sentiment_score"),
                            buzz_score=row.get("buzz_score"),
                            trend_history=row.get("trend_history"),
                            raw_metadata=row,
                            fetched_at=response.fetched_at.isoformat(),
                        )
                    )

    async def read_adanos_trending_stocks(self, *, job_id: str, limit=None):
        rows = [row for row in self.adanos_stocks if row.job_id == job_id]
        return rows if limit is None else rows[:limit]

    async def read_latest_prior_adanos_trending_stocks(self, *, exclude_job_id: str, tickers, since_fetched_at):
        ticker_set = {str(ticker).upper() for ticker in tickers}
        latest: dict[tuple[str, str], StoredAdanosTrendingStock] = {}
        for row in self.adanos_stocks:
            if row.job_id == exclude_job_id or row.ticker.upper() not in ticker_set or row.fetched_at < since_fetched_at:
                continue
            key = (row.platform, row.ticker.upper())
            if key not in latest or row.fetched_at > latest[key].fetched_at:
                latest[key] = row
        return list(latest.values())

    async def has_prior_adanos_trending_stock_history(self, *, exclude_job_id: str, since_fetched_at: str):
        return any(row.job_id != exclude_job_id and row.fetched_at >= since_fetched_at for row in self.adanos_stocks)

    async def read_adanos_trending_sectors(self, *, job_id: str, limit=None):
        rows = [row for row in self.adanos_sectors if row.job_id == job_id]
        return rows if limit is None else rows[:limit]

    async def start_llm_analysis_run(self, **kwargs):
        return None

    async def finish_llm_analysis_run(self, **kwargs):
        return None

    async def save_llm_x_post_analyses(self, rows):
        self.x_analysis_rows.extend(rows)

    async def save_llm_reddit_post_analyses(self, rows):
        self.reddit_post_analysis_rows.extend(rows)

    async def save_llm_reddit_comment_analyses(self, rows):
        self.reddit_comment_analysis_rows.extend(rows)

    async def read_llm_analysis_report(self, *, analysis_run_id: str | None = None):
        posts = [
            {
                "source_ref": row["source_ref"],
                "source_id": row["status_id"],
                "title": row["summary"],
                "post_summary": row["summary"],
                "sentiment": row["sentiment"],
                "tags": ["market", "social", "signal", "risk", "watch"],
                "interpretation": row["interpretation"],
                "importance": row.get("importance", "medium"),
                "confidence": row["confidence"],
                "urls": [row["url"]],
            }
            for row in self.x_analysis_rows
        ]
        return {
            "x_reports": [{"handle": "aleabitoreddit", "overall_summary": ["summary"], "posts": posts}],
            "reddit_report": {"overall_summary": [], "posts": []},
        }


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, payload, *, instructions=None) -> Summary:
        return await self.complete_json([])

    async def complete_json(self, messages) -> Summary:
        self.calls += 1
        return Summary(
            text='{"source":"x","posts":[{"source_ref":"x1","source_id":"1","sentiment":"bullish","tags":["market","social","signal","risk","watch"],"summary":"summary","interpretation":"interpretation","importance":"high","confidence":"medium"}]}',
            model="fake",
            metadata={
                "parsed": {
                    "source": "x",
                    "posts": [
                        {
                            "source_ref": "x1",
                            "source_id": "1",
                            "sentiment": "bullish",
                            "tags": ["market", "social", "signal", "risk", "watch"],
                            "summary": "summary",
                            "interpretation": "interpretation",
                            "importance": "high",
                            "confidence": "medium",
                        }
                    ],
                }
            },
        )


class FakeRenderer:
    def __init__(self, title: str, calls: list[tuple[str, str, str]]) -> None:
        self.title = title
        self.calls = calls

    def render(self, response, *, mode: str, detail: str = "minimum") -> str:
        self.calls.append((self.title, mode, detail))
        return f"{self.title}:{mode}:{detail}"

    def render_trading(self, response, *, mode: str) -> str:
        return f"{self.title}:{mode}:trading"

    def render_trendings(self, response, *, mode: str, limit: int = 5) -> str:
        return f"{self.title}:{mode}:trendings:{limit}"


class FakeRetentionService:
    def __init__(self) -> None:
        self.calls = 0

    async def prune(self, *, protected_paths=None, dry_run: bool = False) -> RetentionSummary:
        self.calls += 1
        return RetentionSummary(
            enabled=True,
            dry_run=dry_run,
            max_total_bytes=100,
            bytes_before=50,
            bytes_after=50,
        )




def _successful_collection_result() -> PipelineCollectionResult:
    return PipelineCollectionResult(scope="social", runs=[], warnings=[])


def _adanos_result(from_date, to_date) -> AdanosTrendingsResult:
    return AdanosTrendingsResult(
        skipped=False,
        responses=[
            AdanosEndpointResult(
                platform="reddit",
                category="stocks",
                endpoint="/reddit/stocks/v1/trending",
                request_args={"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": 100},
                status="succeeded",
                raw_response_text='[{"ticker":"NVDA"}]',
                rows=[
                    {
                        "ticker": "NVDA",
                        "company_name": "NVIDIA Corp",
                        "rank": 1,
                        "trend": "up",
                        "mentions": 10,
                        "bullish_pct": 60,
                        "bearish_pct": 20,
                    }
                ],
            )
        ],
    )


def _sec_13f_row() -> StoredSec13FHolding:
    return StoredSec13FHolding(
        dataset_id="dataset-1",
        dataset_label="2026 March April May 13F",
        accession_number="0001234567-26-000001",
        cik="0001067983",
        manager_name="Berkshire Hathaway Inc",
        filing_date="31-MAY-2026",
        filing_date_utc="2026-05-31",
        period_of_report="31-MAR-2026",
        period_of_report_utc="2026-03-31",
        info_table_sk="1",
        issuer="NVIDIA CORP",
        title_of_class="COM",
        cusip="67066G104",
        figi="BBG000BBJQV0",
        value=1000,
        ssh_prn_amt=50,
        ssh_prn_type="SH",
        put_call="CALL",
        investment_discretion="SOLE",
        other_manager=None,
        voting_auth_sole=50,
        voting_auth_shared=0,
        voting_auth_none=0,
        filing_url="https://www.sec.gov/Archives/edgar/data/1067983/000123456726000001/0001234567-26-000001.txt",
        raw_metadata={},
    )


def _house_row(
    *,
    doc_id: str = "20024228",
    asset: str = "AAPL",
    asset_type_code: str | None = None,
    stock_ticker: str | None = None,
    transaction_date: str = "2026-06-20",
    filing_date: str = "2026-06-30",
) -> StoredHousePtrTradeRow:
    if asset_type_code is None and asset.endswith("[ST]"):
        asset_type_code = "ST"
    if stock_ticker is None and asset_type_code == "ST":
        stock_ticker = "AAPL"
    return StoredHousePtrTradeRow(
        doc_id=doc_id,
        year=2026,
        name="Jane Doe",
        status="Member",
        state="CA",
        filing_date=filing_date,
        filing_date_utc=f"{filing_date}T00:00:00+00:00",
        pdf_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
        table_index=0,
        row_index=0,
        asset=asset,
        asset_type_code=asset_type_code,
        asset_type_label="Stocks, including ADRs" if asset_type_code == "ST" else None,
        stock_ticker=stock_ticker,
        transaction_type="Purchase",
        transaction_date=transaction_date,
        transaction_date_utc=f"{transaction_date}T00:00:00+00:00",
        transaction_action="purchase",
        amount="$1,001 - $15,000",
        raw_cells=["AAPL", "Purchase", "2026-06-20", "$1,001 - $15,000"],
        raw_metadata={},
        collected_at="2026-06-30T00:00:00+00:00",
    )


def _test_config(tmp_path):
    config = load_config(Path("stock_sum/config/example.toml"))
    return config.model_copy(
        update={
            "server": config.server.model_copy(update={"artifact_dir": str(tmp_path / "jobs")}),
            "sources": config.sources.model_copy(
                update={"x_users": [XUserSourceConfig(handle="aleabitoreddit")]}
            ),
            "storage": config.storage.model_copy(update={"sqlite_path": str(tmp_path / "stock_sum.sqlite3")}),
        }
    )
